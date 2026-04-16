from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from apps.api.main import app
from packages.core.node_status import node_offline_threshold_seconds, online_node_filters
from packages.db.models import ProbeNode
import packages.db.session as db_session


client = TestClient(app)


def test_online_node_filters_and_api_nodes_list_follow_last_heartbeat() -> None:
    unique = uuid.uuid4().hex[:8]
    now_utc = datetime.now(timezone.utc)
    fresh_name = f"fresh-node-{unique}"
    stale_name = f"stale-node-{unique}"
    never_name = f"never-node-{unique}"
    disabled_name = f"disabled-node-{unique}"

    db = db_session.SessionLocal()
    try:
        db.add_all(
            [
                ProbeNode(
                    name=fresh_name,
                    node_ip="10.0.0.31",
                    expected_ip="10.0.0.31",
                    status="online",
                    enabled=True,
                    last_heartbeat=now_utc,
                ),
                ProbeNode(
                    name=stale_name,
                    node_ip="10.0.0.32",
                    expected_ip="10.0.0.32",
                    status="online",
                    enabled=True,
                    last_heartbeat=now_utc - timedelta(seconds=node_offline_threshold_seconds() + 5),
                ),
                ProbeNode(
                    name=never_name,
                    node_ip="10.0.0.33",
                    expected_ip="10.0.0.33",
                    status="offline",
                    enabled=True,
                    last_heartbeat=None,
                ),
                ProbeNode(
                    name=disabled_name,
                    node_ip="10.0.0.34",
                    expected_ip="10.0.0.34",
                    status="online",
                    enabled=False,
                    last_heartbeat=now_utc,
                ),
            ]
        )
        db.commit()

        online_names = {
            name
            for name in db.scalars(
                select(ProbeNode.name).where(*online_node_filters(now_utc))
            ).all()
        }
    finally:
        db.close()

    assert online_names == {fresh_name}

    response = client.get("/api/nodes")
    assert response.status_code == 200

    statuses = {
        item["name"]: item["status"]
        for item in response.json()
        if item["name"] in {fresh_name, stale_name, never_name, disabled_name}
    }
    assert statuses == {
        fresh_name: "online",
        stale_name: "offline",
        never_name: "offline",
        disabled_name: "offline",
    }
