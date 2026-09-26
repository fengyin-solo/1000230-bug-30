"""审片意见业务规则：状态流转、轮次追加、字段校验与并发口径都收在这里。

一致性约定：
- 意见只能追加（``opinions``），不允许覆盖；列表快照字段（审片轮次/问题类型/修改意见/
  回复说明/审片人/审片状态）始终从最新一轮意见同步，列表、详情、概览读的是同一份内存数据。
- 每次成功变更都会推进 ``version``；提交时带上 ``expected_version`` 可做乐观并发控制，
  多人同时处理时后提交者会拿到冲突提示并刷新，而不是静默覆盖前一条意见。
- 提交意见支持幂等键（``idempotency_key``）：请求超时后的同键重试只会取回原意见，
  不会重复追加轮次；已通过的记录拒绝一切变更，重试不会覆盖已通过记录。
"""
from __future__ import annotations

import threading
from typing import Any

from app.store import store

MODULE = "review"
# 审片轮次由系统按意见追加次数自动维护，登记时只需编号、审片人和审片对象。
REQUIRED_FIELDS = ["审片编号", "审片人", "审片对象"]
STATUS_ORDER = ["待审片", "审片中", "待修改", "已通过"]
ACTION_RULES = {"发起审片": "审片中", "提交意见": "待修改", "确认通过": "已通过"}
NEGATIVE_ACTIONS = []

# 模块级锁：FastAPI 同步端点跑在线程池里，写动作需要串行化，保证 version 校验与追加是原子的。
_lock = threading.RLock()


def _next_round(entry: dict[str, Any]) -> int:
    try:
        return max(0, int(str(entry.get("审片轮次") or 0).strip())) + 1
    except (TypeError, ValueError):
        return len(entry.get("opinions") or []) + 1


def _normalize_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """把种子/历史数据归一到新口径：轮次数字化、状态同步、意见列表补齐。"""
    status = entry.get("status") if entry.get("status") in STATUS_ORDER else STATUS_ORDER[0]
    entry["status"] = status
    entry["审片状态"] = status

    opinions = entry.get("opinions")
    if not isinstance(opinions, list):
        opinions = []
    entry["opinions"] = opinions

    if not str(entry.get("审片轮次") or "").strip().isdigit():
        entry["审片轮次"] = str(len(opinions))
    entry.setdefault("version", 1)
    entry.setdefault("idempotency_keys", {})

    entry["pending"] = status != STATUS_ORDER[-1]
    entry["abnormal"] = False
    return entry


class ReviewService:
    def __init__(self) -> None:
        # 首次加载时归一化种子数据，避免概览与列表残留旧轮次/旧状态。
        with _lock:
            for row in store.rows(MODULE):
                _normalize_entry(row)

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        with _lock:
            rows = list(store.rows(MODULE))
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("审片编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        with _lock:
            return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        with _lock:
            rows = store.rows(MODULE)
            entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
            entry.update({field: str(values.get(field)).strip() for field in REQUIRED_FIELDS})
            entry["审片对象"] = str(values.get("审片对象") or "").strip()
            entry["status"] = STATUS_ORDER[0]
            entry["审片状态"] = STATUS_ORDER[0]
            entry["审片轮次"] = "0"
            entry["opinions"] = []
            entry["version"] = 1
            entry["idempotency_keys"] = {}
            entry["pending"] = True
            entry["abnormal"] = False
            rows.append(entry)
        return entry, []

    def run_action(
        self,
        entry_id: int,
        action: str,
        values: dict[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
        expected_version: int | None = None,
    ) -> tuple[dict[str, Any] | None, str, str | None]:
        """执行动作，返回 (记录, 提示信息, 结果代码)。

        结果代码：``conflict`` 表示并发版本冲突；``idempotent`` 表示幂等重放；
        ``None`` 表示正常成功或普通业务校验失败（entry 为 None 时）。
        """
        values = values or {}
        with _lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"审片记录 {entry_id} 不存在或已归档", None
            _normalize_entry(entry)

            if action not in ACTION_RULES:
                return None, f"动作「{action}」不属于审片意见可执行范围", None

            current_version = int(entry.get("version", 1))
            if expected_version is not None and expected_version != current_version:
                return (
                    None,
                    "该记录已被其他人更新（可能已有新的审片意见），列表已刷新，请基于最新轮次再操作",
                    "conflict",
                )

            # 终态保护：已通过记录拒绝任何变更，失败重试也不会覆盖已通过结果。
            if entry["status"] == STATUS_ORDER[-1]:
                return None, "审片记录已通过，不允许重复提交或重复确认", None

            status = entry["status"]
            if action == "发起审片":
                if status != "待审片":
                    return None, f"当前状态为「{status}」，不能重复发起审片", None
                entry["status"] = "审片中"
            elif action == "提交意见":
                key = (idempotency_key or "").strip() or None
                if key and key in entry["idempotency_keys"]:
                    # 幂等重放：返回已落库的同一轮意见，不重复追加。
                    return entry, f"第 {entry['审片轮次']} 轮意见已提交（幂等重放）", "idempotent"

                opinion = {
                    "round": _next_round(entry),
                    "问题类型": str(values.get("问题类型") or "").strip(),
                    "修改意见": str(values.get("修改意见") or "").strip(),
                    "回复说明": str(values.get("回复说明") or "").strip(),
                    "审片人": str(values.get("审片人") or entry.get("审片人") or "").strip(),
                }
                if not opinion["修改意见"]:
                    return None, "修改意见不能为空，请填写本轮审片意见后再提交", None
                if not opinion["问题类型"]:
                    return None, "问题类型不能为空，请先选择问题类型", None

                entry["opinions"].append(opinion)
                if key:
                    entry["idempotency_keys"][key] = opinion["round"]
                entry["审片轮次"] = str(opinion["round"])
                entry["问题类型"] = opinion["问题类型"]
                entry["修改意见"] = opinion["修改意见"]
                entry["回复说明"] = opinion["回复说明"]
                if opinion["审片人"]:
                    entry["审片人"] = opinion["审片人"]
                entry["status"] = "待修改"
            else:  # 确认通过
                if status != "待修改":
                    return None, f"当前状态为「{status}」，需先提交意见后才能确认通过", None
                if not entry.get("opinions"):
                    return None, "尚无审片意见，不能空意见确认通过", None
                entry["status"] = "已通过"

            entry["审片状态"] = entry["status"]
            entry["pending"] = entry["status"] != STATUS_ORDER[-1]
            entry["abnormal"] = action in NEGATIVE_ACTIONS
            entry["version"] = current_version + 1
            return entry, f"审片记录已{action}", None
