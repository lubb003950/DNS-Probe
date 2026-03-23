from __future__ import annotations

from datetime import datetime, timedelta, timezone

from packages.db import init_db
from packages.db.models import DnsServer, ProbeNode, ProbeRecord, ProbeTask
from packages.db.session import SessionLocal


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        if db.query(DnsServer).count() == 0:
            internal = DnsServer(dns_alias="Internal-Master", dns_server="10.0.0.53", category="internal")
            public = DnsServer(dns_alias="AliDNS", dns_server="223.5.5.5", category="public")
            db.add_all([internal, public])
            db.commit()
            db.refresh(internal)
            db.refresh(public)

            task1 = ProbeTask(
                domain="api.example.com",
                category="core",
                record_type="A",
                frequency_seconds=60,
                alert_contacts="张三:13800000000,李四:13900000000",
                system_name="DNS探测系统",
                app_name="DNS探测引擎",
            )
            task1.dns_servers = [internal]
            task2 = ProbeTask(
                domain="github.com",
                category="normal",
                record_type="A",
                frequency_seconds=300,
                alert_contacts="王五:13700000000",
                system_name="DNS探测系统",
                app_name="DNS探测引擎",
            )
            task2.dns_servers = [public]
            node = ProbeNode(name="local-agent", node_ip="127.0.0.1", status="online")
            db.add_all([task1, task2, node])
            db.commit()
            db.refresh(task1)
            db.refresh(task2)

            now = datetime.now(timezone.utc)
            demo_records = []
            for idx in range(12):
                status1 = "SERVFAIL" if idx % 4 == 0 else "NOERROR"
                status2 = "TIMEOUT" if idx % 5 == 0 else "NOERROR"
                demo_records.append(
                    ProbeRecord(
                        task_id=task1.id,
                        node_name="local-agent",
                        probe_node="local-agent",
                        timestamp=now - timedelta(minutes=idx * 5),
                        dns_alias="Internal-Master",
                        dns_server="10.0.0.53",
                        domain="api.example.com",
                        record_type="A",
                        status=status1,
                        latency_ms=80 + idx,
                        result_snippet="10.10.10.10" if status1 == "NOERROR" else "",
                        error_message="" if status1 == "NOERROR" else "simulated failure",
                    )
                )
                demo_records.append(
                    ProbeRecord(
                        task_id=task2.id,
                        node_name="local-agent",
                        probe_node="local-agent",
                        timestamp=now - timedelta(minutes=idx * 5),
                        dns_alias="AliDNS",
                        dns_server="223.5.5.5",
                        domain="github.com",
                        record_type="A",
                        status=status2,
                        latency_ms=120 + idx,
                        result_snippet="140.82.114.4" if status2 == "NOERROR" else "",
                        error_message="" if status2 == "NOERROR" else "simulated timeout",
                    )
                )
            db.add_all(demo_records)
            db.commit()
            print("demo data seeded")
        else:
            print("data already exists, skip seeding")
    finally:
        db.close()


if __name__ == "__main__":
    main()
