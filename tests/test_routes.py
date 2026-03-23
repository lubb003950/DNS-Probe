from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from apps.api.main import app
from packages.db.models import AlertEvent, DnsServer, ProbeNode, ProbeRecord, ProbeTask
import packages.db.session as db_session


client = TestClient(app)


def test_dashboard_page_available() -> None:
    response = client.get("/dashboard")
    assert response.status_code == 200


def test_dashboard_page_all_tasks_filter_empty_value() -> None:
    response = client.get("/dashboard?task_id=")
    assert response.status_code == 200


def test_alert_not_found_api() -> None:
    response = client.get("/api/alerts/999999")
    assert response.status_code == 404


def test_timeout_record_creates_alert_event_and_keeps_pages_available(monkeypatch) -> None:
    unique = uuid.uuid4().hex[:8]
    token = f"timeout-token-{unique}"
    node_name = f"timeout-node-{unique}"
    alert_id = None
    task_id = None
    domain = f"timeout-route-test-{unique}.example.com"

    db = db_session.SessionLocal()
    try:
        dns = DnsServer(
            dns_alias=f"timeout-test-dns-{unique}",
            dns_server="10.0.0.53",
            category="internal",
        )
        node = ProbeNode(
            name=node_name,
            node_ip="10.0.0.31",
            expected_ip="10.0.0.31",
            agent_token=token,
            status="online",
            enabled=True,
        )
        task = ProbeTask(
            domain=domain,
            category="normal",
            record_type="A",
            frequency_seconds=60,
            timeout_seconds=2,
            retries=1,
            enabled=True,
            failure_rate_threshold=30,
            consecutive_failures_threshold=3,
            alert_contacts="",
            system_name="DNS System",
            app_name="DNS App",
        )
        task.dns_servers = [dns]
        db.add_all([dns, node, task])
        db.commit()
        task_id = task.id
    finally:
        db.close()

    monkeypatch.setattr("packages.alerts.rules.push_single", lambda payload: (True, "ok"))

    response = client.post(
        "/api/records",
        json={
            "task_id": task_id,
            "node_name": node_name,
            "probe_node": node_name,
            "dns_alias": f"timeout-test-dns-{unique}",
            "dns_server": "10.0.0.53",
            "domain": domain,
            "record_type": "A",
            "status": "TIMEOUT",
            "latency_ms": 2000,
            "result_snippet": "",
            "error_message": "dig timeout",
        },
        headers={"X-Agent-Token": token},
    )
    assert response.status_code == 200
    assert response.json()["success"] is True

    db = db_session.SessionLocal()
    try:
        alerts = db.scalars(select(AlertEvent).where(AlertEvent.task_id == task_id)).all()
        assert len(alerts) == 1
        assert alerts[0].rule_type == "failure_rate"
        assert alerts[0].status == "open"
        alert_id = alerts[0].id
    finally:
        db.close()

    dashboard_response = client.get("/dashboard")
    assert dashboard_response.status_code == 200
    assert "TIMEOUT" in dashboard_response.text

    alerts_response = client.get("/alerts")
    assert alerts_response.status_code == 200

    detail_response = client.get(f"/alerts/{alert_id}")
    assert detail_response.status_code == 200


def test_local_chart_bundle_is_served() -> None:
    response = client.get("/static/chart.umd.min.js")
    assert response.status_code == 200
    assert "Chart" in response.text


def test_task_detail_page_available_with_records() -> None:
    unique = uuid.uuid4().hex[:8]
    task_id = None
    db = db_session.SessionLocal()
    try:
        dns = DnsServer(
            dns_alias=f"route-test-dns-{unique}",
            dns_server="10.0.0.53",
            category="internal",
        )
        task = ProbeTask(
            domain=f"route-test-{unique}.example.com",
            category="normal",
            record_type="A",
            frequency_seconds=60,
            timeout_seconds=2,
            retries=1,
            enabled=True,
            failure_rate_threshold=30,
            consecutive_failures_threshold=3,
            alert_contacts="",
            system_name="DNS System",
            app_name="DNS App",
        )
        task.dns_servers = [dns]
        db.add_all([dns, task])
        db.flush()

        db.add(
            ProbeRecord(
                task_id=task.id,
                node_name="node-a",
                probe_node="node-a",
                dns_alias=dns.dns_alias,
                dns_server=dns.dns_server,
                domain=task.domain,
                record_type="A",
                status="NOERROR",
                latency_ms=12,
                result_snippet="1.1.1.1",
                error_message="",
            )
        )
        db.commit()
        task_id = task.id
    finally:
        db.close()

    response = client.get(f"/tasks/{task_id}")
    assert response.status_code == 200
    assert "node-a" in response.text
    assert "NOERROR" in response.text
    assert "latencyDatasets" in response.text
    assert 'data-ts="' not in response.text


def test_dashboard_page_shows_status_distribution_summary() -> None:
    unique = uuid.uuid4().hex[:8]
    task_id = None
    db = db_session.SessionLocal()
    try:
        dns = DnsServer(
            dns_alias=f"dashboard-test-dns-{unique}",
            dns_server="10.0.0.53",
            category="internal",
        )
        task = ProbeTask(
            domain=f"dashboard-test-{unique}.example.com",
            category="normal",
            record_type="A",
            frequency_seconds=60,
            timeout_seconds=2,
            retries=1,
            enabled=True,
            failure_rate_threshold=30,
            consecutive_failures_threshold=3,
            alert_contacts="",
            system_name="DNS System",
            app_name="DNS App",
        )
        task.dns_servers = [dns]
        db.add_all([dns, task])
        db.flush()
        task_id = task.id

        db.add(
            ProbeRecord(
                task_id=task.id,
                node_name="node-b",
                probe_node="node-b",
                dns_alias=dns.dns_alias,
                dns_server=dns.dns_server,
                domain=task.domain,
                record_type="A",
                status="SERVFAIL",
                latency_ms=45,
                result_snippet="",
                error_message="server failure",
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "SERVFAIL" in response.text
    assert f'/tasks/{task_id}' in response.text
    assert 'data-ts="' not in response.text


def test_tasks_page_hides_offline_nodes_from_available_list() -> None:
    unique = uuid.uuid4().hex[:8]
    online_name = f"online-node-{unique}"
    offline_name = f"offline-node-{unique}"
    db = db_session.SessionLocal()
    try:
        online_node = ProbeNode(
            name=online_name,
            node_ip="10.0.0.11",
            expected_ip="10.0.0.11",
            status="online",
            enabled=True,
        )
        offline_node = ProbeNode(
            name=offline_name,
            node_ip="10.0.0.12",
            expected_ip="10.0.0.12",
            status="offline",
            enabled=True,
        )
        db.add_all([online_node, offline_node])
        db.commit()
    finally:
        db.close()

    response = client.get("/tasks")
    assert response.status_code == 200

    available_html = response.text.split('id="node-available-create"', 1)[1].split("</select>", 1)[0]
    assert online_name in available_html
    assert offline_name not in available_html


def test_task_edit_page_keeps_selected_offline_nodes_out_of_available_list() -> None:
    unique = uuid.uuid4().hex[:8]
    task_id = None
    online_name = f"edit-online-node-{unique}"
    offline_name = f"edit-offline-node-{unique}"
    db = db_session.SessionLocal()
    try:
        dns = DnsServer(
            dns_alias=f"edit-test-dns-{unique}",
            dns_server="10.0.0.53",
            category="internal",
        )
        online_node = ProbeNode(
            name=online_name,
            node_ip="10.0.0.21",
            expected_ip="10.0.0.21",
            status="online",
            enabled=True,
        )
        offline_node = ProbeNode(
            name=offline_name,
            node_ip="10.0.0.22",
            expected_ip="10.0.0.22",
            status="offline",
            enabled=True,
        )
        task = ProbeTask(
            domain=f"edit-route-test-{unique}.example.com",
            category="normal",
            record_type="A",
            frequency_seconds=60,
            timeout_seconds=2,
            retries=1,
            enabled=True,
            failure_rate_threshold=30,
            consecutive_failures_threshold=3,
            alert_contacts="",
            system_name="DNS System",
            app_name="DNS App",
        )
        task.dns_servers = [dns]
        task.nodes = [offline_node]
        db.add_all([dns, online_node, offline_node, task])
        db.commit()
        task_id = task.id
    finally:
        db.close()

    response = client.get(f"/tasks/{task_id}/edit")
    assert response.status_code == 200

    available_html = response.text.split('id="node-available-edit"', 1)[1].split("</select>", 1)[0]
    selected_html = response.text.split('id="node-selected-node-edit"', 1)[1].split("</select>", 1)[0]
    assert online_name in available_html
    assert offline_name not in available_html
    assert offline_name in selected_html


def test_task_delete_page_removes_records_and_alerts() -> None:
    unique = uuid.uuid4().hex[:8]
    task_id = None
    db = db_session.SessionLocal()
    try:
        dns = DnsServer(
            dns_alias=f"delete-test-dns-{unique}",
            dns_server="10.0.0.53",
            category="internal",
        )
        task = ProbeTask(
            domain=f"delete-route-test-{unique}.example.com",
            category="normal",
            record_type="A",
            frequency_seconds=60,
            timeout_seconds=2,
            retries=1,
            enabled=True,
            failure_rate_threshold=30,
            consecutive_failures_threshold=3,
            alert_contacts="",
            system_name="DNS System",
            app_name="DNS App",
        )
        task.dns_servers = [dns]
        db.add_all([dns, task])
        db.flush()
        task_id = task.id

        db.add(
            ProbeRecord(
                task_id=task.id,
                node_name="delete-node",
                probe_node="delete-node",
                dns_alias=dns.dns_alias,
                dns_server=dns.dns_server,
                domain=task.domain,
                record_type="A",
                status="SERVFAIL",
                latency_ms=30,
                result_snippet="",
                error_message="server failure",
            )
        )
        db.add(
            AlertEvent(
                task_id=task.id,
                rule_type="failure_rate",
                level="warning",
                check_text="test",
                description="test alert",
                status="open",
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.post(f"/tasks/{task_id}/delete", follow_redirects=False)
    assert response.status_code == 303

    db = db_session.SessionLocal()
    try:
        assert db.get(ProbeTask, task_id) is None
        assert db.scalars(select(ProbeRecord).where(ProbeRecord.task_id == task_id)).all() == []
        assert db.scalars(select(AlertEvent).where(AlertEvent.task_id == task_id)).all() == []
    finally:
        db.close()


def test_record_route_keeps_success_response_when_alert_evaluation_fails(monkeypatch) -> None:
    unique = uuid.uuid4().hex[:8]
    token = f"record-token-{unique}"
    node_name = f"record-node-{unique}"
    task_id = None
    domain = f"record-route-test-{unique}.example.com"

    db = db_session.SessionLocal()
    try:
        dns = DnsServer(
            dns_alias=f"record-test-dns-{unique}",
            dns_server="10.0.0.53",
            category="internal",
        )
        node = ProbeNode(
            name=node_name,
            node_ip="10.0.0.41",
            expected_ip="10.0.0.41",
            agent_token=token,
            status="online",
            enabled=True,
        )
        task = ProbeTask(
            domain=domain,
            category="normal",
            record_type="A",
            frequency_seconds=60,
            timeout_seconds=2,
            retries=1,
            enabled=True,
            failure_rate_threshold=30,
            consecutive_failures_threshold=3,
            alert_contacts="",
            system_name="DNS System",
            app_name="DNS App",
        )
        task.dns_servers = [dns]
        db.add_all([dns, node, task])
        db.commit()
        task_id = task.id
    finally:
        db.close()

    def _boom(*args, **kwargs):
        raise RuntimeError("alert evaluation failed")

    monkeypatch.setattr("apps.api.routers.records.evaluate_alerts", _boom)

    response = client.post(
        "/api/records",
        json={
            "task_id": task_id,
            "node_name": node_name,
            "probe_node": node_name,
            "dns_alias": f"record-test-dns-{unique}",
            "dns_server": "10.0.0.53",
            "domain": domain,
            "record_type": "A",
            "status": "TIMEOUT",
            "latency_ms": 2000,
            "result_snippet": "",
            "error_message": "dig timeout",
        },
        headers={"X-Agent-Token": token},
    )
    assert response.status_code == 200
    assert response.json()["success"] is True

    db = db_session.SessionLocal()
    try:
        records = db.scalars(select(ProbeRecord).where(ProbeRecord.task_id == task_id)).all()
        assert len(records) == 1
        assert records[0].status == "TIMEOUT"
    finally:
        db.close()
