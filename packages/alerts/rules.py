from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from packages.alerts.yunzhi import build_payload, push_single
from packages.core.config import settings
from packages.core.enums import AlertRuleType, AlertStatus, ProbeStatus, YunzhiLevel
from packages.db.models import AlertEvent, ProbeNode, ProbeRecord, ProbeTask

logger = logging.getLogger(__name__)

FAIL_STATUSES = {
    ProbeStatus.TIMEOUT.value,
    ProbeStatus.SERVFAIL.value,
    ProbeStatus.NXDOMAIN.value,
    ProbeStatus.REFUSED.value,
    ProbeStatus.ERROR.value,
}


def is_failure(status: str) -> bool:
    return status in FAIL_STATUSES


def evaluate_alerts(db: Session, task: ProbeTask, record: ProbeRecord) -> None:
    # ProbeNode 只查一次，两条规则共用
    node = db.scalar(select(ProbeNode).where(ProbeNode.name == record.node_name))
    node_ip = node.node_ip if node is not None else "127.0.0.1"

    consecutive_failed = check_consecutive_failures(db, task, record)
    failure_rate_failed = check_failure_rate(db, task)

    handle_rule(
        db=db,
        task=task,
        record=record,
        node_ip=node_ip,
        rule_type=AlertRuleType.CONSECUTIVE_FAILURES.value,
        should_open=consecutive_failed,
        level=YunzhiLevel.MAJOR.value,
        check_text=f"连续{task.consecutive_failures_threshold}次探测失败",
    )
    handle_rule(
        db=db,
        task=task,
        record=record,
        node_ip=node_ip,
        rule_type=AlertRuleType.FAILURE_RATE.value,
        should_open=failure_rate_failed,
        level=YunzhiLevel.CRITICAL.value,
        check_text=f"最近{settings.failure_rate_window_minutes}分钟失败率>{task.failure_rate_threshold}%",
    )


def check_consecutive_failures(db: Session, task: ProbeTask, record: ProbeRecord) -> bool:
    """检查同一探测路径（task + node + dns_server）的最近 N 次是否全部失败。"""
    records = (
        db.scalars(
            select(ProbeRecord)
            .where(
                ProbeRecord.task_id == task.id,
                ProbeRecord.node_name == record.node_name,
                ProbeRecord.dns_server == record.dns_server,
            )
            .order_by(desc(ProbeRecord.timestamp))
            .limit(task.consecutive_failures_threshold)
        )
        .all()
    )
    if len(records) < task.consecutive_failures_threshold:
        return False
    return all(is_failure(item.status) for item in records)


def check_failure_rate(db: Session, task: ProbeTask) -> bool:
    """用 SQL COUNT 聚合计算时间窗口内的失败率，避免将全部记录加载到内存。"""
    since = datetime.now(timezone.utc) - timedelta(minutes=settings.failure_rate_window_minutes)
    base = [ProbeRecord.task_id == task.id, ProbeRecord.timestamp >= since]

    total = db.scalar(
        select(func.count()).select_from(ProbeRecord).where(*base)
    ) or 0
    if total == 0:
        return False

    failed = db.scalar(
        select(func.count()).select_from(ProbeRecord).where(
            *base, ProbeRecord.status.in_(FAIL_STATUSES)
        )
    ) or 0

    return (failed * 100 / total) > task.failure_rate_threshold


def push_with_retry(payload: dict, retries: int = 3, delay: float = 2.0) -> tuple[bool, str]:
    """推送告警，失败时重试最多 retries 次。"""
    result = ""
    for attempt in range(1, retries + 1):
        ok, result = push_single(payload)
        if ok:
            return ok, result
        if attempt < retries:
            logger.warning("告警推送失败（第 %d/%d 次），%.0fs 后重试…", attempt, retries, delay)
            time.sleep(delay)
    return False, result


def handle_rule(
    *,
    db: Session,
    task: ProbeTask,
    record: ProbeRecord,
    node_ip: str,
    rule_type: str,
    should_open: bool,
    level: str,
    check_text: str,
) -> None:
    open_event = db.scalar(
        select(AlertEvent).where(
            AlertEvent.task_id == task.id,
            AlertEvent.rule_type == rule_type,
            AlertEvent.status == AlertStatus.OPEN.value,
        )
    )
    if should_open and open_event is None:
        description = (
            f"域名 {task.domain} 通过 DNS {record.dns_alias}({record.dns_server}) 探测异常，"
            f"状态 {record.status}，规则 {check_text}"
        )
        event = AlertEvent(
            task_id=task.id,
            rule_type=rule_type,
            level=level,
            check_text=check_text,
            description=description,
            status=AlertStatus.OPEN.value,
        )
        payload = build_payload(
            targetname=task.domain,
            targetip=record.dns_server,
            level=level,
            check=check_text,
            description=description,
            customerip=node_ip,
            contacts=task.alert_contacts,
            system_name=task.system_name,
            app_name=task.app_name,
        )
        ok, result = push_with_retry(payload)
        event.last_push_result = result
        db.add(event)
        db.commit()
        return

    if should_open and open_event is not None:
        open_event.last_triggered_at = datetime.now(timezone.utc)
        db.commit()
        return

    if not should_open and open_event is not None:
        description = f"域名 {task.domain} 通过 DNS {record.dns_alias}({record.dns_server}) 探测已恢复正常"
        payload = build_payload(
            targetname=task.domain,
            targetip=record.dns_server,
            level=YunzhiLevel.OK.value,
            check=check_text,
            description=description,
            customerip=node_ip,
            contacts=task.alert_contacts,
            system_name=task.system_name,
            app_name=task.app_name,
        )
        ok, result = push_with_retry(payload)
        open_event.status = AlertStatus.RECOVERED.value
        open_event.recovered_at = datetime.now(timezone.utc)
        open_event.last_push_result = result
        db.commit()
