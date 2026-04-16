from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, select

from apps.api.routers import alerts, dashboard, dns_servers, nodes, records, tasks, web
from packages.core.config import settings
from packages.core.logging_config import setup_logging
from packages.core.node_status import node_offline_threshold_seconds, node_online_cutoff
from packages.db import init_db
from packages.db.models import AlertEvent, ProbeNode, ProbeRecord
from packages.db.session import SessionLocal

setup_logging("api")
logger = logging.getLogger(__name__)

# 节点超过该时间未上报心跳则标记为 offline
# 探测记录保留天数（来自配置，默认 30 天）
_RECORD_RETENTION_DAYS = settings.record_retention_days
# 已恢复告警保留天数（来自配置，默认 90 天）
_ALERT_RETENTION_DAYS = settings.alert_retention_days


async def _mark_nodes_offline() -> None:
    """每 60 秒检查一次，将心跳超时的节点标记为 offline。"""
    while True:
        await asyncio.sleep(60)
        db = SessionLocal()
        try:
            cutoff = node_online_cutoff()
            stale = db.scalars(
                select(ProbeNode).where(
                    ProbeNode.status != "offline",
                    ProbeNode.last_heartbeat.is_not(None),
                    ProbeNode.last_heartbeat < cutoff,
                )
            ).all()
            for node in stale:
                node.status = "offline"
                logger.warning(
                    "节点 %s 心跳超时（最后心跳: %s），已标记为 offline",
                    node.name,
                    node.last_heartbeat,
                )
            if stale:
                db.commit()
        except Exception:
            logger.exception("mark_nodes_offline 任务异常")
        finally:
            db.close()


async def _cleanup_old_data() -> None:
    """每 6 小时执行一次数据清理，删除超出保留期的记录。"""
    # 启动时立即运行一次，处理存量数据
    await asyncio.sleep(10)
    while True:
        db = SessionLocal()
        try:
            record_cutoff = datetime.now(timezone.utc) - timedelta(days=_RECORD_RETENTION_DAYS)
            alert_cutoff = datetime.now(timezone.utc) - timedelta(days=_ALERT_RETENTION_DAYS)

            r1 = db.execute(
                delete(ProbeRecord).where(ProbeRecord.timestamp < record_cutoff)
            )
            r2 = db.execute(
                delete(AlertEvent).where(
                    AlertEvent.status == "recovered",
                    AlertEvent.recovered_at < alert_cutoff,
                )
            )
            r3 = db.execute(
                delete(AlertEvent).where(
                    AlertEvent.status == "open",
                    AlertEvent.first_triggered_at < alert_cutoff,
                )
            )
            db.commit()
            logger.info(
                "数据清理完成：删除 %d 条探测记录（>%d天）、%d 条已恢复告警（>%d天）、%d 条过期未恢复告警（>%d天）",
                r1.rowcount, _RECORD_RETENTION_DAYS,
                r2.rowcount, _ALERT_RETENTION_DAYS,
                r3.rowcount, _ALERT_RETENTION_DAYS,
            )
        except Exception:
            logger.exception("cleanup_old_data 任务异常")
        finally:
            db.close()

        await asyncio.sleep(6 * 3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("DNS 探测系统 API 启动（节点离线阈值 %ds，记录保留 %d 天）",
                node_offline_threshold_seconds(), _RECORD_RETENTION_DAYS)
    t1 = asyncio.create_task(_mark_nodes_offline())
    t2 = asyncio.create_task(_cleanup_old_data())
    yield
    t1.cancel()
    t2.cancel()
    await asyncio.gather(t1, t2, return_exceptions=True)
    logger.info("DNS 探测系统 API 已停止")


app = FastAPI(title="DNS Probe System", lifespan=lifespan)

_static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(web.router)
app.include_router(dns_servers.router)
app.include_router(tasks.router)
app.include_router(nodes.router)
app.include_router(records.router)
app.include_router(dashboard.router)
app.include_router(alerts.router)
