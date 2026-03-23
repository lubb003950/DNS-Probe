from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from packages.db.models import AlertEvent
from packages.db.session import get_db


router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("")
def list_alerts(db: Session = Depends(get_db)):
    return db.scalars(select(AlertEvent).order_by(desc(AlertEvent.last_triggered_at)).limit(200)).all()


@router.get("/{alert_id}")
def get_alert(alert_id: int, db: Session = Depends(get_db)):
    item = db.get(AlertEvent, alert_id)
    if not item:
        raise HTTPException(status_code=404, detail="Alert not found")
    return item
