from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DnsServerCreate(BaseModel):
    dns_alias: str
    dns_server: str
    category: str = "internal"
    enabled: bool = True


class DnsServerRead(DnsServerCreate):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProbeTaskCreate(BaseModel):
    domain: str
    category: str = "normal"
    dns_server_ids: list[int] = Field(min_length=1)
    node_ids: list[int] = Field(default_factory=list)
    record_type: str = "A"
    frequency_seconds: int = Field(default=60, ge=10, le=86400)
    timeout_seconds: int = Field(default=2, ge=1, le=30)
    retries: int = Field(default=1, ge=0, le=5)
    enabled: bool = True
    failure_rate_threshold: int = Field(default=30, ge=1, le=100)
    consecutive_failures_threshold: int = Field(default=3, ge=1, le=100)
    alert_contacts: str = Field(default="", description="格式: 张三:138...,李四:139...")
    system_name: str = "DNS探测系统"
    app_name: str = "DNS探测引擎"


class ProbeTaskRead(BaseModel):
    id: int
    domain: str
    category: str
    dns_server_ids: list[int] = []
    node_ids: list[int] = []
    record_type: str
    frequency_seconds: int
    timeout_seconds: int
    retries: int
    enabled: bool
    failure_rate_threshold: int
    consecutive_failures_threshold: int
    alert_contacts: str
    system_name: str
    app_name: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProbeNodeUpsert(BaseModel):
    name: str
    node_ip: str


class ProbeNodeRead(BaseModel):
    id: int
    name: str
    expected_ip: str
    node_ip: str
    enabled: bool
    description: str
    status: str
    last_heartbeat: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProbeRecordCreate(BaseModel):
    task_id: int
    node_name: str
    probe_node: str
    dns_alias: str
    dns_server: str
    domain: str
    record_type: str
    status: str
    latency_ms: int = 0
    result_snippet: str = ""
    error_message: str = ""


class AlertEventRead(BaseModel):
    id: int
    task_id: int
    rule_type: str
    level: str
    check_text: str
    description: str
    status: str
    first_triggered_at: datetime
    last_triggered_at: datetime
    recovered_at: datetime | None
    last_push_result: str

    model_config = ConfigDict(from_attributes=True)
