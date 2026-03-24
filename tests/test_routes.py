from __future__ import annotations

from datetime import datetime, timedelta, timezone
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


def test_dashboard_top_failures_only_count_recent_7_days() -> None:
    unique = uuid.uuid4().hex[:8]
    now_utc = datetime.now(timezone.utc)
    recent_domain = f"recent-window-{unique}.example.com"
    old_domain = f"old-window-{unique}.example.com"
    db = db_session.SessionLocal()
    try:
        dns = DnsServer(
            dns_alias=f"dashboard-window-dns-{unique}",
            dns_server="10.0.0.53",
            category="internal",
        )
        recent_task = ProbeTask(
            domain=recent_domain,
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
        old_task = ProbeTask(
            domain=old_domain,
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
        recent_task.dns_servers = [dns]
        old_task.dns_servers = [dns]
        db.add_all([dns, recent_task, old_task])
        db.flush()

        db.add(
            ProbeRecord(
                task_id=recent_task.id,
                node_name="node-window",
                probe_node="node-window",
                dns_alias=dns.dns_alias,
                dns_server=dns.dns_server,
                domain=recent_domain,
                record_type="A",
                status="SERVFAIL",
                latency_ms=30,
                result_snippet="",
                error_message="recent failure",
                timestamp=now_utc - timedelta(days=1),
            )
        )
        db.add(
            ProbeRecord(
                task_id=old_task.id,
                node_name="node-window",
                probe_node="node-window",
                dns_alias=dns.dns_alias,
                dns_server=dns.dns_server,
                domain=old_domain,
                record_type="A",
                status="SERVFAIL",
                latency_ms=30,
                result_snippet="",
                error_message="old failure",
                timestamp=now_utc - timedelta(days=8),
            )
        )
        db.commit()
    finally:
        db.close()

    api_response = client.get("/api/dashboard")
    assert api_response.status_code == 200
    api_domains = [item["domain"] for item in api_response.json()["top_failures"]]
    assert recent_domain in api_domains
    assert old_domain not in api_domains

    page_response = client.get("/dashboard")
    assert page_response.status_code == 200
    chart_section = page_response.text.split("const topLabels  = ", 1)[1].split(";", 1)[0]
    assert recent_domain in chart_section
    assert old_domain not in chart_section


def test_tasks_new_page_hides_offline_nodes_from_available_list() -> None:
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

    response = client.get("/tasks/new")
    assert response.status_code == 200

    available_html = response.text.split('id="node-available-create"', 1)[1].split("</select>", 1)[0]
    assert online_name in available_html
    assert offline_name not in available_html


def test_tasks_page_shows_24h_metrics_and_runtime_filters() -> None:
    unique = uuid.uuid4().hex[:8]
    now_utc = datetime.now(timezone.utc)
    normal_domain = f"normal-task-{unique}.example.com"
    disabled_domain = f"disabled-task-{unique}.example.com"
    no_data_domain = f"no-data-task-{unique}.example.com"
    db = db_session.SessionLocal()
    try:
        dns = DnsServer(
            dns_alias=f"task-metric-dns-{unique}",
            dns_server="10.0.0.53",
            category="internal",
        )
        normal_task = ProbeTask(
            domain=normal_domain,
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
        disabled_task = ProbeTask(
            domain=disabled_domain,
            category="normal",
            record_type="AAAA",
            frequency_seconds=120,
            timeout_seconds=2,
            retries=1,
            enabled=False,
            failure_rate_threshold=30,
            consecutive_failures_threshold=3,
            alert_contacts="",
            system_name="DNS System",
            app_name="DNS App",
        )
        no_data_task = ProbeTask(
            domain=no_data_domain,
            category="normal",
            record_type="A",
            frequency_seconds=180,
            timeout_seconds=2,
            retries=1,
            enabled=True,
            failure_rate_threshold=30,
            consecutive_failures_threshold=3,
            alert_contacts="",
            system_name="DNS System",
            app_name="DNS App",
        )
        normal_task.dns_servers = [dns]
        disabled_task.dns_servers = [dns]
        no_data_task.dns_servers = [dns]
        db.add_all([dns, normal_task, disabled_task, no_data_task])
        db.flush()

        db.add_all(
            [
                ProbeRecord(
                    task_id=normal_task.id,
                    node_name="node-metric",
                    probe_node="node-metric",
                    dns_alias=dns.dns_alias,
                    dns_server=dns.dns_server,
                    domain=normal_task.domain,
                    record_type="A",
                    status="NOERROR",
                    latency_ms=10,
                    result_snippet="1.1.1.1",
                    error_message="",
                    timestamp=now_utc - timedelta(hours=2),
                ),
                ProbeRecord(
                    task_id=normal_task.id,
                    node_name="node-metric",
                    probe_node="node-metric",
                    dns_alias=dns.dns_alias,
                    dns_server=dns.dns_server,
                    domain=normal_task.domain,
                    record_type="A",
                    status="SERVFAIL",
                    latency_ms=90,
                    result_snippet="",
                    error_message="failure",
                    timestamp=now_utc - timedelta(hours=1),
                ),
                ProbeRecord(
                    task_id=normal_task.id,
                    node_name="node-metric",
                    probe_node="node-metric",
                    dns_alias=dns.dns_alias,
                    dns_server=dns.dns_server,
                    domain=normal_task.domain,
                    record_type="A",
                    status="NOERROR",
                    latency_ms=20,
                    result_snippet="1.1.1.1",
                    error_message="",
                    timestamp=now_utc - timedelta(minutes=10),
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    response = client.get("/tasks")
    assert response.status_code == 200
    assert normal_domain in response.text
    assert "66.67%" in response.text
    assert "15ms" in response.text
    assert "IPv4" in response.text
    assert "IPv6" in response.text
    assert "/tasks/new" in response.text

    disabled_response = client.get("/tasks?status=disabled")
    assert disabled_response.status_code == 200
    assert disabled_domain in disabled_response.text
    assert normal_domain not in disabled_response.text

    no_data_response = client.get("/tasks?status=no_data")
    assert no_data_response.status_code == 200
    assert no_data_domain in no_data_response.text


def test_tasks_page_paginates_rows() -> None:
    unique = uuid.uuid4().hex[:8]
    db = db_session.SessionLocal()
    created_tasks: list[tuple[int, str]] = []
    try:
        dns = DnsServer(
            dns_alias=f"pagination-dns-{unique}",
            dns_server="10.0.0.53",
            category="internal",
        )
        db.add(dns)
        db.flush()

        for index in range(21):
            domain = f"pagination-{index}-{unique}.example.com"
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
            db.add(task)
            db.flush()
            created_tasks.append((task.id, domain))
        db.commit()
    finally:
        db.close()

    created_tasks.sort(reverse=True)
    first_expected = created_tasks[0][1]
    second_page_expected = created_tasks[-1][1]

    first_page = client.get(f"/tasks?task_name=pagination-&target={unique}&page_size=20&page=1")
    assert first_page.status_code == 200
    assert first_expected in first_page.text
    assert second_page_expected not in first_page.text

    second_page = client.get(f"/tasks?task_name=pagination-&target={unique}&page_size=20&page=2")
    assert second_page.status_code == 200
    assert second_page_expected in second_page.text
    assert first_expected not in second_page.text


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
