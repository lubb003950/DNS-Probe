from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from packages.db.session import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


task_dns_servers = Table(
    "task_dns_servers",
    Base.metadata,
    Column("task_id", Integer, ForeignKey("probe_tasks.id", ondelete="CASCADE"), primary_key=True),
    Column("dns_server_id", Integer, ForeignKey("dns_servers.id", ondelete="CASCADE"), primary_key=True),
)

task_nodes = Table(
    "task_nodes",
    Base.metadata,
    Column("task_id", Integer, ForeignKey("probe_tasks.id", ondelete="CASCADE"), primary_key=True),
    Column("node_id", Integer, ForeignKey("probe_nodes.id", ondelete="CASCADE"), primary_key=True),
)


class DnsServer(Base):
    __tablename__ = "dns_servers"

    id: Mapped[int] = mapped_column(primary_key=True)
    dns_alias: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    dns_server: Mapped[str] = mapped_column(String(64), index=True)
    category: Mapped[str] = mapped_column(String(20))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    tasks: Mapped[list["ProbeTask"]] = relationship(
        secondary=task_dns_servers, back_populates="dns_servers", lazy="selectin",
    )


class ProbeTask(Base):
    __tablename__ = "probe_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    domain: Mapped[str] = mapped_column(String(255), index=True)
    category: Mapped[str] = mapped_column(String(20), default="normal")
    dns_server_id: Mapped[int | None] = mapped_column(
        ForeignKey("dns_servers.id"), nullable=True,
    )
    record_type: Mapped[str] = mapped_column(String(20), default="A")
    frequency_seconds: Mapped[int] = mapped_column(Integer, default=60)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=2)
    retries: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    failure_rate_threshold: Mapped[int] = mapped_column(Integer, default=30)
    consecutive_failures_threshold: Mapped[int] = mapped_column(Integer, default=3)
    alert_contacts: Mapped[str] = mapped_column(Text, default="")
    system_name: Mapped[str] = mapped_column(String(255), default="DNS探测系统")
    app_name: Mapped[str] = mapped_column(String(255), default="DNS探测引擎")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    dns_servers: Mapped[list["DnsServer"]] = relationship(
        secondary=task_dns_servers, back_populates="tasks", lazy="selectin",
    )
    nodes: Mapped[list["ProbeNode"]] = relationship(
        secondary=task_nodes, back_populates="tasks", lazy="selectin",
    )
    records: Mapped[list["ProbeRecord"]] = relationship(back_populates="task")

    @property
    def dns_server_ids(self) -> list[int]:
        return [ds.id for ds in self.dns_servers]

    @property
    def node_ids(self) -> list[int]:
        return [n.id for n in self.nodes]


class ProbeNode(Base):
    __tablename__ = "probe_nodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    expected_ip: Mapped[str] = mapped_column(String(64), default="")
    node_ip: Mapped[str] = mapped_column(String(64), default="")
    agent_token: Mapped[str] = mapped_column(String(255), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(20), default="online")
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    tasks: Mapped[list["ProbeTask"]] = relationship(
        secondary=task_nodes, back_populates="nodes", lazy="selectin",
    )


class ProbeRecord(Base):
    __tablename__ = "probe_records"
    __table_args__ = (
        # 告警评估核心查询：WHERE task_id=? AND node_name=? AND dns_server=? ORDER BY timestamp DESC
        Index("ix_probe_records_task_node_dns_ts", "task_id", "node_name", "dns_server", "timestamp"),
        # 失败率聚合查询：WHERE task_id=? AND timestamp>=? AND status IN (...)
        Index("ix_probe_records_task_ts", "task_id", "timestamp"),
        # 按状态过滤的 dashboard 查询
        Index("ix_probe_records_status_ts", "status", "timestamp"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("probe_tasks.id"), index=True)
    node_name: Mapped[str] = mapped_column(String(100), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    probe_node: Mapped[str] = mapped_column(String(100))
    dns_alias: Mapped[str] = mapped_column(String(100), index=True)
    dns_server: Mapped[str] = mapped_column(String(64), index=True)
    domain: Mapped[str] = mapped_column(String(255), index=True)
    record_type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30), index=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    result_snippet: Mapped[str] = mapped_column(Text, default="")
    error_message: Mapped[str] = mapped_column(Text, default="")

    task: Mapped["ProbeTask"] = relationship(back_populates="records")


class AlertEvent(Base):
    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("probe_tasks.id"), index=True)
    rule_type: Mapped[str] = mapped_column(String(50), index=True)
    level: Mapped[str] = mapped_column(String(20))
    check_text: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    first_triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_push_result: Mapped[str] = mapped_column(Text, default="")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(50), index=True)
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(50))
    details: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
