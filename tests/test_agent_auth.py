from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from packages.core.config import settings
from packages.db.models import DnsServer, ProbeNode, ProbeTask
import packages.db.session as db_session


client = TestClient(app)


@pytest.fixture()
def configured_agent_auth():
    original = {
        "agent_auth_enabled": settings.agent_auth_enabled,
        "agent_auth_header": settings.agent_auth_header,
    }
    object.__setattr__(settings, "agent_auth_enabled", True)
    object.__setattr__(settings, "agent_auth_header", "X-Agent-Token")
    try:
        yield
    finally:
        for key, value in original.items():
            object.__setattr__(settings, key, value)


def _headers(token: str | None) -> dict[str, str]:
    return {"X-Agent-Token": token} if token else {}


def _name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _create_node(*, name: str, token: str, enabled: bool = True, expected_ip: str = "10.0.0.11") -> ProbeNode:
    db = db_session.SessionLocal()
    try:
        node = ProbeNode(
            name=name,
            expected_ip=expected_ip,
            node_ip=expected_ip,
            agent_token=token,
            enabled=enabled,
            description="test",
            status="offline",
        )
        db.add(node)
        db.commit()
        db.refresh(node)
        return node
    finally:
        db.close()


def _seed_task() -> ProbeTask:
    unique = uuid.uuid4().hex[:8]
    db = db_session.SessionLocal()
    try:
        dns = DnsServer(
            dns_alias=f"auth-test-dns-{unique}",
            dns_server="10.0.0.53",
            category="internal",
        )
        db.add(dns)
        db.flush()

        task = ProbeTask(
            domain=f"auth-test-{unique}.example.com",
            category="normal",
            record_type="A",
            frequency_seconds=60,
            timeout_seconds=2,
            retries=1,
            enabled=True,
            failure_rate_threshold=30,
            consecutive_failures_threshold=3,
            alert_contacts="",
            system_name="DNS探测系统",
            app_name="DNS探测引擎",
        )
        task.dns_servers = [dns]
        db.add(task)
        db.commit()
        db.refresh(task)
        return task
    finally:
        db.close()


def test_register_requires_agent_token(configured_agent_auth) -> None:
    node_name = _name("node-a")
    _create_node(name=node_name, token="token-a")
    response = client.post("/api/nodes/register", json={"name": node_name, "node_ip": "10.0.0.11"})
    assert response.status_code == 401


def test_register_rejects_invalid_agent_token(configured_agent_auth) -> None:
    node_name = _name("node-a")
    _create_node(name=node_name, token="token-a")
    response = client.post(
        "/api/nodes/register",
        json={"name": node_name, "node_ip": "10.0.0.11"},
        headers=_headers("wrong-token"),
    )
    assert response.status_code == 403


def test_unregistered_agent_is_rejected(configured_agent_auth) -> None:
    response = client.post(
        "/api/nodes/register",
        json={"name": "ghost-node", "node_ip": "10.0.0.99"},
        headers=_headers("ghost-token"),
    )
    assert response.status_code == 403


def test_disabled_agent_is_rejected(configured_agent_auth) -> None:
    node_name = _name("node-disabled")
    _create_node(name=node_name, token="token-disabled", enabled=False)
    response = client.post(
        "/api/nodes/heartbeat",
        json={"name": node_name, "node_ip": "10.0.0.12"},
        headers=_headers("token-disabled"),
    )
    assert response.status_code == 403


def test_agent_token_allows_register_and_heartbeat(configured_agent_auth) -> None:
    node_name = _name("node-a")
    _create_node(name=node_name, token="token-a")

    response = client.post(
        "/api/nodes/register",
        json={"name": node_name, "node_ip": "10.0.0.11"},
        headers=_headers("token-a"),
    )
    assert response.status_code == 200
    assert response.json()["success"] is True

    response = client.post(
        "/api/nodes/heartbeat",
        json={"name": node_name, "node_ip": "10.0.0.11"},
        headers=_headers("token-a"),
    )
    assert response.status_code == 200
    assert response.json()["success"] is True


def test_agent_token_cannot_impersonate_other_node(configured_agent_auth) -> None:
    node_a = _name("node-a")
    node_b = _name("node-b")
    _create_node(name=node_a, token="token-a")
    _create_node(name=node_b, token="token-b")
    response = client.get(f"/api/nodes/{node_b}/tasks", headers=_headers("token-a"))
    assert response.status_code == 403


def test_agent_token_allows_pull_tasks_and_report_record(configured_agent_auth) -> None:
    node_name = _name("node-a")
    _create_node(name=node_name, token="token-a")
    task = _seed_task()

    response = client.get(f"/api/nodes/{node_name}/tasks", headers=_headers("token-a"))
    assert response.status_code == 200
    assert response.json()

    response = client.post(
        "/api/records",
        json={
            "task_id": task.id,
            "node_name": node_name,
            "probe_node": node_name,
            "dns_alias": "auth-test-dns",
            "dns_server": "10.0.0.53",
            "domain": task.domain,
            "record_type": "A",
            "status": "NOERROR",
            "latency_ms": 12,
            "result_snippet": "1.1.1.1",
            "error_message": "",
        },
        headers=_headers("token-a"),
    )
    assert response.status_code == 200
    assert response.json()["success"] is True


def test_reset_token_invalidates_old_token(configured_agent_auth) -> None:
    node = _create_node(name=_name("node-reset"), token="old-token")

    response = client.post(f"/nodes/{node.id}/reset-token", follow_redirects=False)
    assert response.status_code == 303

    response = client.post(
        "/api/nodes/register",
        json={"name": node.name, "node_ip": "10.0.0.13"},
        headers=_headers("old-token"),
    )
    assert response.status_code == 403
