"""
手动数据清理脚本（正常情况下由 API 后台任务自动执行）。

用法：
    python scripts/cleanup_db.py               # 使用默认保留天数（记录30天，告警90天）
    python scripts/cleanup_db.py --records 7   # 记录只保留7天
    python scripts/cleanup_db.py --dry-run     # 仅统计，不实际删除
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

from packages.db.models import AlertEvent, ProbeRecord
from packages.db.session import SessionLocal


def main() -> None:
    parser = argparse.ArgumentParser(description="清理过期的探测记录和告警事件")
    parser.add_argument("--records", type=int, default=30, help="探测记录保留天数（默认30）")
    parser.add_argument("--alerts", type=int, default=90, help="已恢复告警保留天数（默认90）")
    parser.add_argument("--dry-run", action="store_true", help="只统计数量，不实际删除")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        record_cutoff = datetime.now(timezone.utc) - timedelta(days=args.records)
        alert_cutoff = datetime.now(timezone.utc) - timedelta(days=args.alerts)

        # 统计待删除数量
        n_records = db.scalar(
            select(func.count()).select_from(ProbeRecord).where(ProbeRecord.timestamp < record_cutoff)
        )
        n_alerts = db.scalar(
            select(func.count()).select_from(AlertEvent).where(
                AlertEvent.status == "recovered",
                AlertEvent.recovered_at < alert_cutoff,
            )
        )

        print(f"探测记录：将删除 {n_records} 条（创建时间早于 {record_cutoff.date()}）")
        print(f"已恢复告警：将删除 {n_alerts} 条（恢复时间早于 {alert_cutoff.date()}）")

        if args.dry_run:
            print("[dry-run] 未实际删除任何数据")
            return

        if n_records == 0 and n_alerts == 0:
            print("没有需要清理的数据")
            return

        confirm = input("确认删除？（y/n）: ").strip().lower()
        if confirm != "y":
            print("已取消")
            return

        db.execute(delete(ProbeRecord).where(ProbeRecord.timestamp < record_cutoff))
        db.execute(
            delete(AlertEvent).where(
                AlertEvent.status == "recovered",
                AlertEvent.recovered_at < alert_cutoff,
            )
        )
        db.commit()
        print(f"清理完成：已删除 {n_records} 条探测记录和 {n_alerts} 条已恢复告警")

    finally:
        db.close()


if __name__ == "__main__":
    main()
