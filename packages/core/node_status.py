from __future__ import annotations

from datetime import datetime, timedelta, timezone

from packages.core.config import settings
from packages.db.models import ProbeNode


def node_offline_threshold_seconds() -> int:
    return max(settings.heartbeat_interval_seconds * 3, 90)


def node_online_cutoff(now: datetime | None = None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)
    return current - timedelta(seconds=node_offline_threshold_seconds())


def _normalize_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def is_node_online(node: ProbeNode, now: datetime | None = None) -> bool:
    if not node.enabled:
        return False
    last_heartbeat = _normalize_utc(node.last_heartbeat)
    if last_heartbeat is None:
        return False
    return last_heartbeat >= node_online_cutoff(now)


def derive_node_status(node: ProbeNode, now: datetime | None = None) -> str:
    return "online" if is_node_online(node, now) else "offline"


def online_node_filters(now: datetime | None = None):
    cutoff = node_online_cutoff(now)
    return (
        ProbeNode.enabled.is_(True),
        ProbeNode.last_heartbeat.is_not(None),
        ProbeNode.last_heartbeat >= cutoff,
    )
