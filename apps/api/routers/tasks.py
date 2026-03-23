from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from apps.api.schemas import ProbeTaskCreate, ProbeTaskRead
from packages.db.models import AlertEvent, AuditLog, DnsServer, ProbeNode, ProbeRecord, ProbeTask
from packages.db.session import get_db


router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _resolve_dns_servers(db: Session, ids: list[int]) -> list[DnsServer]:
    servers = db.scalars(select(DnsServer).where(DnsServer.id.in_(ids))).all()
    found = {s.id for s in servers}
    missing = set(ids) - found
    if missing:
        raise HTTPException(status_code=400, detail=f"DNS server(s) not found: {sorted(missing)}")
    return list(servers)


def _resolve_nodes(db: Session, ids: list[int]) -> list[ProbeNode]:
    if not ids:
        return []
    nodes = db.scalars(select(ProbeNode).where(ProbeNode.id.in_(ids))).all()
    found = {n.id for n in nodes}
    missing = set(ids) - found
    if missing:
        raise HTTPException(status_code=400, detail=f"Node(s) not found: {sorted(missing)}")
    return list(nodes)


def _delete_task_related_data(db: Session, item: ProbeTask) -> None:
    item.nodes = []
    item.dns_servers = []
    db.execute(delete(ProbeRecord).where(ProbeRecord.task_id == item.id))
    db.execute(delete(AlertEvent).where(AlertEvent.task_id == item.id))
    db.delete(item)


@router.get("", response_model=list[ProbeTaskRead])
def list_tasks(db: Session = Depends(get_db)):
    return db.scalars(select(ProbeTask).order_by(ProbeTask.id.desc())).all()


@router.get("/{task_id}", response_model=ProbeTaskRead)
def get_task(task_id: int, db: Session = Depends(get_db)):
    item = db.get(ProbeTask, task_id)
    if not item:
        raise HTTPException(status_code=404, detail="Task not found")
    return item


@router.post("", response_model=ProbeTaskRead)
def create_task(payload: ProbeTaskCreate, db: Session = Depends(get_db)):
    dns_list  = _resolve_dns_servers(db, payload.dns_server_ids)
    node_list = _resolve_nodes(db, payload.node_ids)
    data = payload.model_dump(exclude={"dns_server_ids", "node_ids"})
    item = ProbeTask(**data)
    item.dns_servers = dns_list
    item.nodes = node_list
    db.add(item)
    db.flush()  # 分配 id，不提交事务
    db.add(AuditLog(entity_type="task", entity_id=item.id, action="create", details=item.domain))
    db.commit()
    db.refresh(item)
    return item


@router.put("/{task_id}", response_model=ProbeTaskRead)
def update_task(task_id: int, payload: ProbeTaskCreate, db: Session = Depends(get_db)):
    item = db.get(ProbeTask, task_id)
    if not item:
        raise HTTPException(status_code=404, detail="Task not found")
    dns_list  = _resolve_dns_servers(db, payload.dns_server_ids)
    node_list = _resolve_nodes(db, payload.node_ids)
    data = payload.model_dump(exclude={"dns_server_ids", "node_ids"})
    for key, value in data.items():
        setattr(item, key, value)
    item.dns_servers = dns_list
    item.nodes = node_list
    db.add(AuditLog(entity_type="task", entity_id=item.id, action="update", details=item.domain))
    db.commit()
    db.refresh(item)
    return item


@router.post("/{task_id}/toggle")
def toggle_task(task_id: int, db: Session = Depends(get_db)):
    item = db.get(ProbeTask, task_id)
    if not item:
        raise HTTPException(status_code=404, detail="Task not found")
    item.enabled = not item.enabled
    db.add(AuditLog(entity_type="task", entity_id=item.id, action="toggle", details=str(item.enabled)))
    db.commit()
    return {"success": True, "enabled": item.enabled}


@router.delete("/{task_id}")
def delete_task(task_id: int, db: Session = Depends(get_db)):
    item = db.get(ProbeTask, task_id)
    if not item:
        raise HTTPException(status_code=404, detail="Task not found")
    domain = item.domain
    _delete_task_related_data(db, item)
    db.add(AuditLog(entity_type="task", entity_id=task_id, action="delete", details=domain))
    db.commit()
    return {"success": True}
