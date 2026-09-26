"""审片意见接口：维护审片记录，覆盖发起审片、提交意见、确认通过等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.review import ReviewService

router = APIRouter(prefix="/api/review", tags=["审片意见"])

service = ReviewService()

LIST_FIELDS = ["审片编号", "审片轮次", "问题类型", "修改意见", "回复说明", "审片人", "审片对象", "审片状态"]
STATUSES = ["待审片", "审片中", "待修改", "已通过"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按审片编号检索"),
    status: str | None = Query(default=None, description="待审片、审片中、待修改、已通过"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按审片编号与状态过滤审片意见列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条审片记录明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"审片记录 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条审片记录，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="审片记录已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条审片记录执行发起审片、提交意见、确认通过。

    - 提交意见必须带问题类型与修改意见，空意见会被拦下；
    - values.expected_version 与服务端 version 不一致时返回 409，提示后提交者刷新；
    - values.idempotency_key 相同的提交意见请求只追加一次，支持超时重试；
    - 已通过记录拒绝一切变更。
    """
    values = dict(payload.values)
    action = str(values.pop("action", "") or "").strip()
    expected_version = values.pop("expected_version", None)
    idempotency_key = values.pop("idempotency_key", None)
    try:
        expected_version = int(expected_version) if expected_version is not None else None
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="expected_version 必须是整数")
    entry, message, code = service.run_action(
        entry_id,
        action,
        values,
        idempotency_key=str(idempotency_key) if idempotency_key is not None else None,
        expected_version=expected_version,
    )
    if entry is None:
        if code == "conflict":
            raise HTTPException(status_code=409, detail=message)
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出审片意见清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "review", "total": total, "items": items}
