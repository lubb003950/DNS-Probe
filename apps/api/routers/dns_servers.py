from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.schemas import DnsServerCreate, DnsServerRead
from packages.db.models import AuditLog, DnsServer, task_dns_servers
from packages.db.session import get_db


router = APIRouter(prefix="/api/dns-servers", tags=["dns-servers"])


@router.get("", response_model=list[DnsServerRead])
def list_dns_servers(db: Session = Depends(get_db)):
    return db.scalars(select(DnsServer).order_by(DnsServer.id.desc())).all()


@router.post("", response_model=DnsServerRead)
def create_dns_server(payload: DnsServerCreate, db: Session = Depends(get_db)):
    item = DnsServer(**payload.model_dump())
    db.add(item)
    db.flush()  # 分配 id，不提交事务
    db.add(AuditLog(entity_type="dns_server", entity_id=item.id, action="create", details=item.dns_alias))
    db.commit()
    db.refresh(item)
    return item


@router.put("/{dns_server_id}", response_model=DnsServerRead)
def update_dns_server(dns_server_id: int, payload: DnsServerCreate, db: Session = Depends(get_db)):
    item = db.get(DnsServer, dns_server_id)
    if not item:
        raise HTTPException(status_code=404, detail="DNS server not found")
    for key, value in payload.model_dump().items():
        setattr(item, key, value)
    db.add(AuditLog(entity_type="dns_server", entity_id=item.id, action="update", details=item.dns_alias))
    db.commit()
    db.refresh(item)
    return item


@router.delete("/{dns_server_id}")
def delete_dns_server(dns_server_id: int, db: Session = Depends(get_db)):
    item = db.get(DnsServer, dns_server_id)
    if not item:
        raise HTTPException(status_code=404, detail="DNS server not found")
    ref = db.scalar(
        select(task_dns_servers.c.task_id).where(task_dns_servers.c.dns_server_id == dns_server_id)
    )
    if ref is not None:
        raise HTTPException(status_code=400, detail="DNS server is referenced by tasks")
    db.delete(item)
    db.add(AuditLog(entity_type="dns_server", entity_id=dns_server_id, action="delete", details=item.dns_alias))
    db.commit()
    return {"success": True}
