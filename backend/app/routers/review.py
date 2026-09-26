"""审片意见接口：维护审片记录，覆盖发起审片、提交意见、确认通过等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.review import ReviewConflict, service as review_service

router = APIRouter(prefix="/api/review", tags=["审片意见"])

service = review_service

LIST_FIELDS = ["审片编号", "审片轮次", "审片人", "审片对象", "问题类型", "修改意见", "回复说明", "审片状态"]
STATUSES = ["待审片", "审片中", "待修改", "已通过"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按审片编号检索"),
    reviewer: str | None = Query(default=None, description="按审片人检索"),
    status: str | None = Query(default=None, description="待审片、审片中、待修改、已通过"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按审片编号、审片人与状态过滤审片意见列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword, reviewer=reviewer, status=status, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出审片意见清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "review", "total": total, "items": items}


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

    提交时可带 expected_version 做乐观并发校验、request_id 做失败重试幂等；
    版本过期说明已有别人提交了新一轮，返回 409 让前端刷新后再决定。
    """
    action = str(payload.values.get("action") or "").strip()
    try:
        entry, message = service.run_action(entry_id, action, payload.values)
    except ReviewConflict as conflict:
        raise HTTPException(
            status_code=409,
            detail={"message": str(conflict), "entry": conflict.entry},
        )
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
