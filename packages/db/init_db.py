from __future__ import annotations

import logging

from sqlalchemy import inspect, text

from packages.db import models  # noqa: F401
from packages.db.session import Base, engine

logger = logging.getLogger(__name__)


def _ensure_columns(table_name: str, columns: dict[str, str]) -> None:
    inspector = inspect(engine)
    if not inspector.has_table(table_name):
        return

    existing = {column["name"] for column in inspector.get_columns(table_name)}
    missing = {name: ddl for name, ddl in columns.items() if name not in existing}
    if not missing:
        return

    with engine.begin() as conn:
        for name, ddl in missing.items():
            conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {ddl}"))
            logger.info("Added missing column %s.%s", table_name, name)


def ensure_dns_server_columns() -> None:
    _ensure_columns(
        "dns_servers",
        {
            "category": "category VARCHAR(20) NOT NULL DEFAULT 'internal'",
            "enabled": "enabled BOOLEAN NOT NULL DEFAULT 1",
            "created_at": "created_at DATETIME NULL",
        },
    )


def ensure_probe_node_columns() -> None:
    _ensure_columns(
        "probe_nodes",
        {
            "expected_ip": "expected_ip VARCHAR(64) NOT NULL DEFAULT ''",
            "node_ip": "node_ip VARCHAR(64) NOT NULL DEFAULT ''",
            "agent_token": "agent_token VARCHAR(255) NOT NULL DEFAULT ''",
            "enabled": "enabled BOOLEAN NOT NULL DEFAULT 1",
            "description": "description VARCHAR(255) NOT NULL DEFAULT ''",
            "status": "status VARCHAR(20) NOT NULL DEFAULT 'offline'",
            "last_heartbeat": "last_heartbeat DATETIME NULL",
            "created_at": "created_at DATETIME NULL",
        },
    )


def ensure_probe_task_columns() -> None:
    _ensure_columns(
        "probe_tasks",
        {
            "category": "category VARCHAR(20) NOT NULL DEFAULT 'normal'",
            "record_type": "record_type VARCHAR(20) NOT NULL DEFAULT 'A'",
            "frequency_seconds": "frequency_seconds INTEGER NOT NULL DEFAULT 60",
            "timeout_seconds": "timeout_seconds INTEGER NOT NULL DEFAULT 2",
            "retries": "retries INTEGER NOT NULL DEFAULT 1",
            "enabled": "enabled BOOLEAN NOT NULL DEFAULT 1",
            "failure_rate_threshold": "failure_rate_threshold INTEGER NOT NULL DEFAULT 30",
            "consecutive_failures_threshold": "consecutive_failures_threshold INTEGER NOT NULL DEFAULT 3",
            "alert_contacts": "alert_contacts VARCHAR(1000) NOT NULL DEFAULT ''",
            "system_name": "system_name VARCHAR(255) NOT NULL DEFAULT 'DNS Probe System'",
            "app_name": "app_name VARCHAR(255) NOT NULL DEFAULT 'DNS Probe Engine'",
            "created_at": "created_at DATETIME NULL",
        },
    )


def ensure_probe_record_columns() -> None:
    _ensure_columns(
        "probe_records",
        {
            "task_id": "task_id INTEGER NULL",
            "node_name": "node_name VARCHAR(100) NOT NULL DEFAULT ''",
            "timestamp": "timestamp DATETIME NULL",
            "probe_node": "probe_node VARCHAR(100) NOT NULL DEFAULT ''",
            "dns_alias": "dns_alias VARCHAR(100) NOT NULL DEFAULT ''",
            "dns_server": "dns_server VARCHAR(64) NOT NULL DEFAULT ''",
            "domain": "domain VARCHAR(255) NOT NULL DEFAULT ''",
            "record_type": "record_type VARCHAR(20) NOT NULL DEFAULT 'A'",
            "status": "status VARCHAR(30) NOT NULL DEFAULT 'ERROR'",
            "latency_ms": "latency_ms INTEGER NOT NULL DEFAULT 0",
            "result_snippet": "result_snippet VARCHAR(2048) NOT NULL DEFAULT ''",
            "error_message": "error_message VARCHAR(4096) NOT NULL DEFAULT ''",
        },
    )


def ensure_alert_event_columns() -> None:
    _ensure_columns(
        "alert_events",
        {
            "task_id": "task_id INTEGER NULL",
            "rule_type": "rule_type VARCHAR(50) NOT NULL DEFAULT ''",
            "level": "level VARCHAR(20) NOT NULL DEFAULT 'Major'",
            "check_text": "check_text VARCHAR(255) NOT NULL DEFAULT ''",
            "description": "description VARCHAR(2048) NOT NULL DEFAULT ''",
            "status": "status VARCHAR(20) NOT NULL DEFAULT 'open'",
            "first_triggered_at": "first_triggered_at DATETIME NULL",
            "last_triggered_at": "last_triggered_at DATETIME NULL",
            "recovered_at": "recovered_at DATETIME NULL",
            "last_push_result": "last_push_result VARCHAR(4096) NOT NULL DEFAULT ''",
        },
    )


def ensure_audit_log_columns() -> None:
    _ensure_columns(
        "audit_logs",
        {
            "entity_type": "entity_type VARCHAR(50) NOT NULL DEFAULT ''",
            "entity_id": "entity_id INTEGER NOT NULL DEFAULT 0",
            "action": "action VARCHAR(50) NOT NULL DEFAULT ''",
            "details": "details VARCHAR(2048) NOT NULL DEFAULT ''",
            "created_at": "created_at DATETIME NULL",
        },
    )


def migrate_task_dns_servers() -> None:
    """Backfill task_dns_servers from legacy dns_server_id column."""
    inspector = inspect(engine)
    if not inspector.has_table("task_dns_servers"):
        return
    if not inspector.has_table("probe_tasks"):
        return

    with engine.begin() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM task_dns_servers")).scalar()
        if count and count > 0:
            return

        has_old = conn.execute(
            text("SELECT COUNT(*) FROM probe_tasks WHERE dns_server_id IS NOT NULL")
        ).scalar()
        if has_old and has_old > 0:
            conn.execute(text(
                "INSERT INTO task_dns_servers (task_id, dns_server_id) "
                "SELECT id, dns_server_id FROM probe_tasks WHERE dns_server_id IS NOT NULL"
            ))
            logger.info("Backfilled %d rows into task_dns_servers", has_old)

        try:
            conn.execute(text("ALTER TABLE probe_tasks MODIFY COLUMN dns_server_id INT NULL"))
            logger.info("Made probe_tasks.dns_server_id nullable")
        except Exception:
            pass


def migrate_task_nodes() -> None:
    """Ensure task_nodes table exists (no legacy data to backfill)."""
    inspector = inspect(engine)
    if not inspector.has_table("task_nodes"):
        logger.info("task_nodes table will be created by create_all")
    # No backfill needed: new feature, tasks start with empty node assignments.


def ensure_probe_record_indexes() -> None:
    """为 probe_records 表补充复合索引（幂等，已存在则跳过）。"""
    inspector = inspect(engine)
    if not inspector.has_table("probe_records"):
        return
    existing = {idx["name"] for idx in inspector.get_indexes("probe_records")}
    statements: list[str] = []
    if "ix_probe_records_task_node_dns_ts" not in existing:
        statements.append(
            "CREATE INDEX ix_probe_records_task_node_dns_ts "
            "ON probe_records (task_id, node_name, dns_server, timestamp)"
        )
    if "ix_probe_records_task_ts" not in existing:
        statements.append(
            "CREATE INDEX ix_probe_records_task_ts "
            "ON probe_records (task_id, timestamp)"
        )
    if "ix_probe_records_status_ts" not in existing:
        statements.append(
            "CREATE INDEX ix_probe_records_status_ts "
            "ON probe_records (status, timestamp)"
        )
    if not statements:
        return
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
            logger.info("Created index: %s", stmt.split()[2])


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    ensure_dns_server_columns()
    ensure_probe_node_columns()
    ensure_probe_task_columns()
    migrate_task_dns_servers()
    migrate_task_nodes()
    ensure_probe_record_columns()
    ensure_alert_event_columns()
    ensure_audit_log_columns()
    ensure_probe_record_indexes()
