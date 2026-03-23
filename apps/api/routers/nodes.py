from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.deps.agent_auth import authenticate_node_path, authenticate_node_payload
from apps.api.schemas import ProbeNodeRead, ProbeNodeUpsert
from packages.db.models import ProbeNode, ProbeTask, task_nodes
from packages.db.session import get_db


router = APIRouter(prefix="/api/nodes", tags=["nodes"])


def _mark_node_online(payload: ProbeNodeUpsert, db: Session) -> dict:
    """将节点标记为在线并更新 IP / 心跳时间，register 与 heartbeat 共用。"""
    item = db.scalar(select(ProbeNode).where(ProbeNode.name == payload.name))
    if item is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Agent is not pre-registered")
    item.node_ip = payload.node_ip
    item.status = "online"
    item.last_heartbeat = datetime.now(timezone.utc)
    db.commit()
    return {"success": True}


@router.post("/register")
def register_node(
    payload: ProbeNodeUpsert,
    _agent_name: str = Depends(authenticate_node_payload),
    db: Session = Depends(get_db),
):
    return _mark_node_online(payload, db)


@router.post("/heartbeat")
def heartbeat(
    payload: ProbeNodeUpsert,
    _agent_name: str = Depends(authenticate_node_payload),
    db: Session = Depends(get_db),
):
    return _mark_node_online(payload, db)


@router.get("")
def list_nodes(db: Session = Depends(get_db)):
    return [
        ProbeNodeRead.model_validate(item).model_dump()
        for item in db.scalars(select(ProbeNode).order_by(ProbeNode.id.desc())).all()
    ]


@router.get("/{node_name}/tasks")
def pull_tasks(
    node_name: str,
    _agent_name: str = Depends(authenticate_node_path),
    db: Session = Depends(get_db),
):
    node = db.scalar(select(ProbeNode).where(ProbeNode.name == node_name))
    if node is None or not node.enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Agent is not available")

    # 只下发该节点被明确分配的任务；若任务未分配任何节点则全量下发（向下兼容）
    assigned_task_ids = db.scalars(
        select(task_nodes.c.task_id).where(task_nodes.c.node_id == node.id)
    ).all()
    if assigned_task_ids:
        task_filter = ProbeTask.id.in_(assigned_task_ids)
    else:
        task_filter = ~ProbeTask.id.in_(
            select(task_nodes.c.task_id).distinct()
        )
    tasks = db.scalars(
        select(ProbeTask)
        .where(ProbeTask.enabled.is_(True), task_filter)
        .order_by(ProbeTask.id)
    ).all()
    response = []
    for task in tasks:
        for dns in task.dns_servers:
            if not dns.enabled:
                continue
            response.append(
                {
                    "id": task.id,
                    "domain": task.domain,
                    "category": task.category,
                    "record_type": task.record_type,
                    "frequency_seconds": task.frequency_seconds,
                    "timeout_seconds": task.timeout_seconds,
                    "retries": task.retries,
                    "failure_rate_threshold": task.failure_rate_threshold,
                    "consecutive_failures_threshold": task.consecutive_failures_threshold,
                    "alert_contacts": task.alert_contacts,
                    "system_name": task.system_name,
                    "app_name": task.app_name,
                    "dns_alias": dns.dns_alias,
                    "dns_server": dns.dns_server,
                }
            )
    return response
