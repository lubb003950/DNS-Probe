from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from apps.api.deps.agent_auth import authenticate_record_payload
from apps.api.schemas import ProbeRecordCreate
from packages.alerts.rules import evaluate_alerts
from packages.db.models import ProbeRecord, ProbeTask
from packages.db.session import get_db


router = APIRouter(prefix="/api/records", tags=["records"])
logger = logging.getLogger(__name__)


@router.post("")
def create_record(
    payload: ProbeRecordCreate,
    _agent_name: str = Depends(authenticate_record_payload),
    db: Session = Depends(get_db),
):
    # 幂等检查：15 秒内若已存在同 task/节点/DNS 的记录则跳过，防止网络重试产生重复数据
    dedup_window = datetime.now(timezone.utc) - timedelta(seconds=15)
    duplicate = db.scalar(
        select(ProbeRecord).where(
            ProbeRecord.task_id == payload.task_id,
            ProbeRecord.node_name == payload.node_name,
            ProbeRecord.dns_server == payload.dns_server,
            ProbeRecord.timestamp >= dedup_window,
        ).limit(1)
    )
    if duplicate is not None:
        return {"success": True, "record_id": duplicate.id, "deduplicated": True}

    record = ProbeRecord(**payload.model_dump(), timestamp=datetime.now(timezone.utc))
    db.add(record)
    db.commit()
    db.refresh(record)

    try:
        task = db.get(ProbeTask, payload.task_id)
        if task is not None:
            evaluate_alerts(db, task, record)
    except Exception:
        db.rollback()
        logger.exception(
            "Failed to evaluate alerts for task_id=%s record_id=%s",
            payload.task_id,
            record.id,
        )
    return {"success": True, "record_id": record.id}


@router.get("")
def list_records(
    task_id: int | None = None,
    status: str | None = None,
    dns_alias: str | None = None,
    hours: int = Query(default=24, ge=1, le=720),
    db: Session = Depends(get_db),
):
    stmt = select(ProbeRecord).where(ProbeRecord.timestamp >= datetime.now(timezone.utc) - timedelta(hours=hours))
    if task_id:
        stmt = stmt.where(ProbeRecord.task_id == task_id)
    if status:
        stmt = stmt.where(ProbeRecord.status == status)
    if dns_alias:
        stmt = stmt.where(ProbeRecord.dns_alias == dns_alias)
    stmt = stmt.order_by(desc(ProbeRecord.timestamp)).limit(500)
    return db.scalars(stmt).all()
