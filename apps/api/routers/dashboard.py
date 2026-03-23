from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from packages.db.models import ProbeRecord
from packages.db.session import get_db


router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

_FAIL_SET = ["TIMEOUT", "SERVFAIL", "NXDOMAIN", "REFUSED", "ERROR"]


@router.get("")
def get_dashboard(db: Session = Depends(get_db)):
    since = datetime.now(timezone.utc) - timedelta(hours=24)

    total = db.scalar(
        select(func.count()).select_from(ProbeRecord).where(ProbeRecord.timestamp >= since)
    ) or 0

    failed = db.scalar(
        select(func.count()).select_from(ProbeRecord).where(
            ProbeRecord.timestamp >= since,
            ProbeRecord.status.in_(_FAIL_SET),
        )
    ) or 0

    top_rows = db.execute(
        select(ProbeRecord.domain, func.count().label("cnt"))
        .where(ProbeRecord.timestamp >= since, ProbeRecord.status.in_(_FAIL_SET))
        .group_by(ProbeRecord.domain)
        .order_by(desc("cnt"))
        .limit(10)
    ).all()

    success = total - failed
    top_failures = [{"domain": r.domain, "count": r.cnt} for r in top_rows]
    return {
        "total_records": total,
        "success_rate": round((success * 100 / total), 2) if total else 0,
        "failure_rate": round((failed * 100 / total), 2) if total else 0,
        "top_failures": top_failures,
    }
