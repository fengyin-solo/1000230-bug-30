"""审片意见业务规则：状态流转、字段校验、轮次意见与并发口径都收在这里。

设计要点：
- 意见按轮次追加到 ``opinions``，行上的「审片轮次/问题类型/修改意见」始终镜像
  最新一轮，列表与详情读的是同一份数据，不再各说各话。
- 每条记录带 ``version``，每次成功提交 +1；前端带着自己看到的版本号提交，
  别人已经抢先提交时返回冲突（409），避免后提交者把前一条意见盖掉。
- 每次提交可带 ``request_id`` 做幂等：网络失败重试时，同一个请求只会落一次，
  重试拿到的是已提交结果，不会把已经通过的记录重新打开或覆盖。
- 「已通过」是终态：任何后续动作一律拒绝，失败重试也不能改写已通过记录。
"""
from __future__ import annotations

from threading import Lock
from typing import Any

from app.store import store

MODULE = "review"
REQUIRED_FIELDS = ["审片编号", "审片轮次", "审片人"]
STATUS_ORDER = ["待审片", "审片中", "待修改", "已通过"]
FINAL_STATUS = "已通过"
ACTION_RULES = {"发起审片": "审片中", "提交意见": "待修改", "确认通过": "已通过"}
NEGATIVE_ACTIONS = []

# 各动作允许的前置状态；提交意见在「审片中」首轮提交、在「待修改」继续往返时都允许。
ACTION_PRECONDITIONS = {
    "发起审片": {"待审片"},
    "提交意见": {"审片中", "待修改"},
    "确认通过": {"审片中", "待修改"},
}
OPINION_FIELDS = ["问题类型", "修改意见", "回复说明", "审片人"]


class ReviewConflict(Exception):
    """提交基于的版本已过期（别人先提交了一轮），调用方应返回 409。"""

    def __init__(self, message: str, entry: dict[str, Any]) -> None:
        super().__init__(message)
        self.entry = entry


class ReviewService:
    def __init__(self) -> None:
        self._lock = Lock()
        self._normalize()

    # ---------- 读取 ----------

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        reviewer: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("审片编号", ""))]
        if reviewer:
            rows = [row for row in rows if reviewer in str(row.get("审片人", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    # ---------- 登记 ----------

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        with self._lock:
            rows = store.rows(MODULE)
            entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
            entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
            entry["审片对象"] = str(values.get("审片对象") or "").strip()
            entry["问题类型"] = ""
            entry["修改意见"] = ""
            entry["回复说明"] = ""
            entry["status"] = STATUS_ORDER[0]
            entry["opinions"] = []
            entry["version"] = 1
            entry["request_ids"] = []
            self._apply_flags(entry)
            rows.append(entry)
        return entry, []

    # ---------- 动作流转 ----------

    def run_action(
        self,
        entry_id: int,
        action: str,
        values: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        values = values or {}
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于审片意见可执行范围"

        expected_version = self._as_int(values.get("expected_version"))
        request_id = str(values.get("request_id") or "").strip()

        with self._lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"审片记录 {entry_id} 不存在或已归档"

            # 幂等：同一请求重试，直接把已落库的结果还回去，不再执行一遍。
            if request_id and request_id in entry.get("request_ids", []):
                return entry, f"审片记录已{action}（请求已处理，返回当前结果）"

            if entry.get("status") == FINAL_STATUS:
                return None, "审片记录已通过并锁定，不能再执行任何动作"
            if entry.get("status") not in ACTION_PRECONDITIONS[action]:
                return None, f"当前状态「{entry.get('status')}」不允许{action}"

            # 乐观并发：调用方持有的版本过期时拒绝，由调用方刷新后看到前一条意见再决定。
            current_version = int(entry.get("version", 1))
            if expected_version is not None and expected_version != current_version:
                raise ReviewConflict(
                    "审片意见已有更新轮次，请刷新后查看最新意见再提交", entry
                )

            if action == "提交意见":
                error = self._submit_opinion(entry, values)
            elif action == "确认通过":
                error = self._approve(entry, values)
            else:
                error = self._start(entry, values)
            if error:
                return None, error

            entry["version"] = current_version + 1
            if request_id:
                entry.setdefault("request_ids", []).append(request_id)
            self._apply_flags(entry)
        return entry, f"审片记录已{action}"

    # ---------- 各动作的具体落库 ----------

    def _start(self, entry: dict[str, Any], values: dict[str, Any]) -> str:
        reviewer = str(values.get("审片人") or entry.get("审片人") or "").strip()
        if reviewer:
            entry["审片人"] = reviewer
        entry["status"] = ACTION_RULES["发起审片"]
        return ""

    def _submit_opinion(self, entry: dict[str, Any], values: dict[str, Any]) -> str:
        issue_type = str(values.get("问题类型") or "").strip()
        comment = str(values.get("修改意见") or "").strip()
        if not issue_type:
            return "提交意见前必须填写问题类型"
        if not comment:
            return "提交意见前必须填写修改意见，空意见不能提交"

        reply = str(values.get("回复说明") or "").strip()
        reviewer = str(values.get("审片人") or entry.get("审片人") or "").strip()
        round_no = self._next_round(entry)
        opinion = {
            "轮次": round_no,
            "问题类型": issue_type,
            "修改意见": comment,
            "回复说明": reply,
            "审片人": reviewer,
        }
        entry.setdefault("opinions", []).append(opinion)
        # 行字段镜像最新一轮：列表与详情读同一口径。
        entry["审片轮次"] = round_no
        entry["问题类型"] = issue_type
        entry["修改意见"] = comment
        entry["回复说明"] = reply
        if reviewer:
            entry["审片人"] = reviewer
        entry["status"] = ACTION_RULES["提交意见"]
        return ""

    def _approve(self, entry: dict[str, Any], values: dict[str, Any]) -> str:
        opinions = entry.get("opinions") or []
        if not any(str(item.get("修改意见") or "").strip() for item in opinions):
            return "尚未提交任何审片意见，不能直接确认通过"
        reviewer = str(values.get("审片人") or "").strip()
        if reviewer:
            entry["审片人"] = reviewer
        entry["status"] = ACTION_RULES["确认通过"]
        return ""

    # ---------- 内部工具 ----------

    def _normalize(self) -> None:
        """补齐历史数据：旧种子没有 opinions/version 等字段，首次使用时统一收口。"""
        with self._lock:
            for entry in store.rows(MODULE):
                entry.setdefault("opinions", [])
                entry.setdefault("version", 1)
                entry.setdefault("request_ids", [])
                if not isinstance(entry.get("审片轮次"), int):
                    # 种子里的占位文本统一归零，第一轮提交后即为 1。
                    entry["审片轮次"] = 0
                self._apply_flags(entry)

    @staticmethod
    def _apply_flags(entry: dict[str, Any]) -> None:
        # pending/abnormal 始终由状态推导，避免概览与列表各算各的。
        entry["pending"] = entry.get("status") != FINAL_STATUS
        entry["abnormal"] = False

    @staticmethod
    def _next_round(entry: dict[str, Any]) -> int:
        # 轮次只由已落库的意见条数决定：首轮提交即为第 1 轮，与行上的占位初值无关。
        return max(
            (int(item.get("轮次", 0)) for item in entry.get("opinions", [])),
            default=0,
        ) + 1

    @staticmethod
    def _as_int(value: Any) -> int | None:
        if value is None or str(value).strip() == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


service = ReviewService()
