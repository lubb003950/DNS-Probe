from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from math import ceil
from pathlib import Path
import secrets
from typing import List
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, case, delete, desc, func, select
from sqlalchemy.orm import Session

from packages.alerts.rules import FAIL_STATUSES, is_failure
from packages.db.models import AlertEvent, DnsServer, ProbeNode, ProbeRecord, ProbeTask, task_dns_servers, task_nodes
from packages.db.session import get_db


template_dir = Path(__file__).resolve().parents[2] / "web" / "templates"
templates = Jinja2Templates(directory=str(template_dir))
router = APIRouter(tags=["web"])
BEIJING_TZ = timezone(timedelta(hours=8))
TOP_FAILURE_WINDOW = timedelta(days=7)
TASK_METRIC_WINDOW = timedelta(hours=24)
TASK_PAGE_SIZE_OPTIONS = (20, 50, 100)


def to_beijing_time(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")


def build_agent_env_snippet(node: ProbeNode) -> str:
    return "\n".join(
        [
            f'DNS_PROBE_AGENT_NAME="{node.name}"',
            f'DNS_PROBE_AGENT_IP="{node.expected_ip or node.node_ip}"',
            f'DNS_PROBE_AGENT_TOKEN="{node.agent_token}"',
        ]
    )


templates.env.filters["bj_time"] = to_beijing_time
templates.env.filters["agent_env"] = build_agent_env_snippet


def _build_query_url(path: str, params: dict[str, object]) -> str:
    query_params = {
        key: value
        for key, value in params.items()
        if value not in ("", None)
    }
    if not query_params:
        return path
    return f"{path}?{urlencode(query_params, doseq=True)}"


def _normalize_page(value: int | str | None) -> int:
    try:
        page = int(value or 1)
    except (TypeError, ValueError):
        return 1
    return max(page, 1)


def _normalize_page_size(value: int | str | None) -> int:
    try:
        page_size = int(value or 50)
    except (TypeError, ValueError):
        return 50
    return page_size if page_size in TASK_PAGE_SIZE_OPTIONS else 50


def _task_form_values(task: ProbeTask | None = None) -> dict[str, object]:
    if task is None:
        return {
            "domain": "",
            "category": "normal",
            "record_type": "A",
            "frequency_seconds": 60,
            "timeout_seconds": 2,
            "retries": 1,
            "failure_rate_threshold": 30,
            "consecutive_failures_threshold": 3,
            "alert_contacts": "",
            "system_name": "\u0044\u004e\u0053\u63a2\u6d4b\u7cfb\u7edf",
            "app_name": "\u0044\u004e\u0053\u63a2\u6d4b\u5f15\u64ce",
        }
    return {
        "domain": task.domain,
        "category": task.category,
        "record_type": task.record_type,
        "frequency_seconds": task.frequency_seconds,
        "timeout_seconds": task.timeout_seconds,
        "retries": task.retries,
        "failure_rate_threshold": task.failure_rate_threshold,
        "consecutive_failures_threshold": task.consecutive_failures_threshold,
        "alert_contacts": task.alert_contacts,
        "system_name": task.system_name,
        "app_name": task.app_name,
    }


def _network_type_for(record_type: str) -> str:
    if record_type == "A":
        return "IPv4"
    if record_type == "AAAA":
        return "IPv6"
    return "-"


def _derive_task_status(task: ProbeTask, latest_status: str | None) -> tuple[str, str, str]:
    if not task.enabled:
        return "disabled", "\u5df2\u505c\u7528", "badge-warning"
    if latest_status is None:
        return "no_data", "\u65e0\u6570\u636e", "badge-brand"
    if latest_status == "NOERROR":
        return "normal", "\u6b63\u5e38", "badge-success"
    return "abnormal", "\u5f02\u5e38", "badge-danger"


def _fetch_task_metric_maps(
    db: Session,
    task_ids: list[int],
) -> tuple[dict[int, dict[str, float | int | None]], dict[int, str]]:
    if not task_ids:
        return {}, {}

    since = datetime.now(timezone.utc) - TASK_METRIC_WINDOW

    metric_rows = db.execute(
        select(
            ProbeRecord.task_id.label("task_id"),
            func.count().label("total_count"),
            func.sum(case((ProbeRecord.status == "NOERROR", 1), else_=0)).label("success_count"),
            func.avg(
                case(
                    (ProbeRecord.status == "NOERROR", ProbeRecord.latency_ms),
                    else_=None,
                )
            ).label("avg_latency_ms"),
        )
        .where(
            ProbeRecord.task_id.in_(task_ids),
            ProbeRecord.timestamp >= since,
        )
        .group_by(ProbeRecord.task_id)
    ).all()

    metrics_by_task: dict[int, dict[str, float | int | None]] = {
        row.task_id: {
            "total_count": row.total_count,
            "success_count": row.success_count or 0,
            "avg_latency_ms": row.avg_latency_ms,
        }
        for row in metric_rows
    }

    latest_record_subquery = (
        select(
            ProbeRecord.task_id.label("task_id"),
            func.max(ProbeRecord.timestamp).label("latest_timestamp"),
        )
        .where(
            ProbeRecord.task_id.in_(task_ids),
            ProbeRecord.timestamp >= since,
        )
        .group_by(ProbeRecord.task_id)
        .subquery()
    )

    latest_status_rows = db.execute(
        select(
            ProbeRecord.task_id,
            ProbeRecord.status,
            ProbeRecord.id,
        )
        .join(
            latest_record_subquery,
            and_(
                ProbeRecord.task_id == latest_record_subquery.c.task_id,
                ProbeRecord.timestamp == latest_record_subquery.c.latest_timestamp,
            ),
        )
        .order_by(ProbeRecord.task_id, desc(ProbeRecord.id))
    ).all()

    latest_status_by_task: dict[int, str] = {}
    for row in latest_status_rows:
        latest_status_by_task.setdefault(row.task_id, row.status)

    return metrics_by_task, latest_status_by_task


def _assignable_nodes(db: Session) -> list[ProbeNode]:
    return db.scalars(
        select(ProbeNode)
        .where(
            ProbeNode.enabled.is_(True),
            ProbeNode.status == "online",
        )
        .order_by(ProbeNode.id)
    ).all()


def _delete_task_related_data(db: Session, task: ProbeTask) -> None:
    task.nodes = []
    task.dns_servers = []
    db.execute(delete(ProbeRecord).where(ProbeRecord.task_id == task.id))
    db.execute(delete(AlertEvent).where(AlertEvent.task_id == task.id))
    db.delete(task)


@router.get("/")
def home():
    return RedirectResponse(url="/dashboard", status_code=302)


@router.get("/dashboard")
def dashboard_page(
    request: Request,
    hours: int = 24,
    task_id: str = "",
    status: str | None = None,
    db: Session = Depends(get_db),
):
    selected_task_id = int(task_id) if task_id.strip().isdigit() else None
    now_utc = datetime.now(timezone.utc)
    since = now_utc - timedelta(hours=hours)
    top_since = now_utc - TOP_FAILURE_WINDOW

    # 构建基础过滤条件
    base_filters = [ProbeRecord.timestamp >= since]
    if selected_task_id is not None:
        base_filters.append(ProbeRecord.task_id == selected_task_id)
    if status:
        base_filters.append(ProbeRecord.status == status)
    fail_filters = [*base_filters, ProbeRecord.status.in_(FAIL_STATUSES)]
    top_failure_filters = [ProbeRecord.timestamp >= top_since]
    if selected_task_id is not None:
        top_failure_filters.append(ProbeRecord.task_id == selected_task_id)
    if status:
        top_failure_filters.append(ProbeRecord.status == status)
    top_failure_filters.append(ProbeRecord.status.in_(FAIL_STATUSES))

    # SQL 聚合，避免将整张表加载到内存
    total = db.scalar(select(func.count()).select_from(ProbeRecord).where(*base_filters)) or 0
    failed = db.scalar(select(func.count()).select_from(ProbeRecord).where(*fail_filters)) or 0
    success = total - failed

    top_rows = db.execute(
        select(ProbeRecord.domain, func.count().label("cnt"))
        .where(*top_failure_filters)
        .group_by(ProbeRecord.domain)
        .order_by(desc("cnt"))
        .limit(10)
    ).all()
    top_failures = [(r.domain, r.cnt) for r in top_rows]

    status_rows = db.execute(
        select(ProbeRecord.status, func.count().label("cnt"))
        .where(*base_filters)
        .group_by(ProbeRecord.status)
    ).all()
    status_distribution = [(r.status, r.cnt) for r in status_rows]

    recent_records = db.scalars(
        select(ProbeRecord).where(*base_filters).order_by(desc(ProbeRecord.timestamp)).limit(20)
    ).all()

    tasks = db.scalars(select(ProbeTask).order_by(ProbeTask.domain).limit(200)).all()

    # 为 Top 失败域名找到对应的 task_id（取同名任务中 id 最小的一个）
    domain_to_task_id: dict[str, int] = {}
    for task in tasks:
        if task.domain not in domain_to_task_id:
            domain_to_task_id[task.domain] = task.id
    top_failures_task_ids = [domain_to_task_id.get(d) for d, _ in top_failures]

    # stat card counts
    total_tasks   = db.scalar(select(func.count()).select_from(ProbeTask)) or 0
    enabled_tasks = db.scalar(select(func.count()).select_from(ProbeTask).where(ProbeTask.enabled.is_(True))) or 0
    total_nodes   = db.scalar(select(func.count()).select_from(ProbeNode)) or 0
    online_nodes  = db.scalar(select(func.count()).select_from(ProbeNode).where(ProbeNode.status == "online")) or 0
    open_alerts   = db.scalar(select(func.count()).select_from(AlertEvent).where(AlertEvent.status == "open")) or 0

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "hours": hours,
            "task_id": selected_task_id,
            "status": status or "",
            "tasks": tasks,
            "success_rate": round((success * 100 / total), 2) if total else 0,
            "failure_rate": round((failed * 100 / total), 2) if total else 0,
            "top_failures": top_failures,
            "top_failures_task_ids": top_failures_task_ids,
            "status_distribution": status_distribution,
            "recent_records": recent_records,
            # stat cards
            "total_tasks": total_tasks,
            "enabled_tasks": enabled_tasks,
            "total_nodes": total_nodes,
            "online_nodes": online_nodes,
            "open_alerts": open_alerts,
            # chart data
            "chart_status_labels": [s for s, _ in status_distribution],
            "chart_status_data": [c for _, c in status_distribution],
            "chart_top_labels": [d for d, _ in top_failures],
            "chart_top_data": [c for _, c in top_failures],
            "chart_top_task_ids": top_failures_task_ids,
            "chart_sr_data": [
                round((success * 100 / total), 2) if total else 0,
                round((failed * 100 / total), 2) if total else 0,
            ],
        },
    )


@router.get("/tasks")
def tasks_page(
    request: Request,
    status: str = "",
    task_name: str = "",
    target: str = "",
    category: str = "",
    dns_alias: str = "",
    page: int = 1,
    page_size: int = 50,
    q: str = "",
    enabled: str = "",
    advanced: str = "",
    message: str = "",
    db: Session = Depends(get_db),
):
    task_name = task_name or q
    page = _normalize_page(page)
    page_size = _normalize_page_size(page_size)

    stmt = select(ProbeTask).order_by(desc(ProbeTask.id))
    if task_name:
        stmt = stmt.where(ProbeTask.domain.ilike(f"%{task_name}%"))
    if target:
        stmt = stmt.where(ProbeTask.domain.ilike(f"%{target}%"))
    if category:
        stmt = stmt.where(ProbeTask.category == category)
    if enabled == "true":
        stmt = stmt.where(ProbeTask.enabled.is_(True))
    elif enabled == "false":
        stmt = stmt.where(ProbeTask.enabled.is_(False))
    if dns_alias:
        stmt = stmt.join(ProbeTask.dns_servers).where(DnsServer.dns_alias == dns_alias)
    tasks = db.scalars(stmt).unique().all()

    metrics_by_task, latest_status_by_task = _fetch_task_metric_maps(
        db,
        [task.id for task in tasks],
    )

    task_rows: list[dict[str, object]] = []
    for task in tasks:
        status_key, status_label, status_badge_class = _derive_task_status(
            task,
            latest_status_by_task.get(task.id),
        )
        if status and status != status_key:
            continue

        metrics = metrics_by_task.get(task.id, {})
        total_count = int(metrics.get("total_count") or 0)
        success_count = int(metrics.get("success_count") or 0)
        avg_latency_ms = metrics.get("avg_latency_ms")

        availability_text = "--"
        if total_count:
            availability_text = f"{(success_count * 100 / total_count):.2f}%"

        response_time_text = "--"
        if avg_latency_ms is not None:
            response_time_text = f"{round(float(avg_latency_ms))}ms"

        task_rows.append(
            {
                "task": task,
                "status_key": status_key,
                "status_label": status_label,
                "status_badge_class": status_badge_class,
                "network_type": _network_type_for(task.record_type),
                "availability_text": availability_text,
                "response_time_text": response_time_text,
            }
        )

    total_tasks = len(task_rows)
    total_pages = max(ceil(total_tasks / page_size), 1)
    page = min(page, total_pages)
    page_start = (page - 1) * page_size
    page_end = page_start + page_size
    paged_rows = task_rows[page_start:page_end]

    dns_servers = db.scalars(select(DnsServer).order_by(DnsServer.id)).all()
    all_aliases = sorted({s.dns_alias for s in dns_servers})
    query_base = {
        "status": status,
        "task_name": task_name,
        "target": target,
        "category": category,
        "dns_alias": dns_alias,
        "page_size": page_size,
        "advanced": advanced,
    }
    page_numbers = list(range(max(1, page - 2), min(total_pages, page + 2) + 1))
    return templates.TemplateResponse(
        request,
        "tasks.html",
        {
            "task_rows": paged_rows,
            "all_aliases": all_aliases,
            "status": status,
            "task_name": task_name,
            "target": target,
            "category": category,
            "dns_alias": dns_alias,
            "advanced_open": advanced == "1" or bool(category or dns_alias),
            "message": message,
            "page": page,
            "page_size": page_size,
            "page_size_options": TASK_PAGE_SIZE_OPTIONS,
            "total_tasks": total_tasks,
            "page_start": page_start + 1 if total_tasks else 0,
            "page_end": min(page_end, total_tasks),
            "total_pages": total_pages,
            "page_numbers": page_numbers,
            "page_urls": {
                page_number: _build_query_url("/tasks", {**query_base, "page": page_number})
                for page_number in page_numbers
            },
            "prev_page_url": _build_query_url("/tasks", {**query_base, "page": page - 1}) if page > 1 else None,
            "next_page_url": _build_query_url("/tasks", {**query_base, "page": page + 1}) if page < total_pages else None,
        },
    )


@router.get("/tasks/new")
def new_task_page(request: Request, db: Session = Depends(get_db)):
    dns_servers = db.scalars(select(DnsServer).order_by(DnsServer.id)).all()
    available_nodes = _assignable_nodes(db)
    return templates.TemplateResponse(
        request,
        "task_new.html",
        {
            "dns_servers": dns_servers,
            "available_nodes": available_nodes,
            "selected_dns_ids": set(),
            "selected_nodes": [],
            "selected_node_ids": set(),
            "form_values": _task_form_values(),
            "form_mode": "create",
            "form_action": "/tasks",
            "submit_label": "\u521b\u5efa\u4efb\u52a1",
        },
    )


@router.post("/tasks/batch")
def batch_toggle_tasks(
    action: str = Form(...),
    task_ids: List[int] = Form(default=[]),
    db: Session = Depends(get_db),
):
    if task_ids:
        items = db.scalars(select(ProbeTask).where(ProbeTask.id.in_(task_ids))).all()
        for t in items:
            if action == "enable":
                t.enabled = True
            elif action == "disable":
                t.enabled = False
        db.commit()
        action_label = "\u542f\u7528" if action == "enable" else "\u505c\u7528"
        message = f"\u5df2\u6279\u91cf{action_label} {len(items)} \u6761\u4efb\u52a1"
        return RedirectResponse(url=_build_query_url("/tasks", {"message": message}), status_code=303)
    return RedirectResponse(url="/tasks", status_code=303)


@router.post("/tasks")
def create_task_page(
    domain: str = Form(...),
    category: str = Form("normal"),
    dns_server_ids: List[int] = Form(...),
    node_ids: List[int] = Form(default=[]),
    record_type: str = Form("A"),
    frequency_seconds: int = Form(60),
    timeout_seconds: int = Form(2),
    retries: int = Form(1),
    failure_rate_threshold: int = Form(30),
    consecutive_failures_threshold: int = Form(3),
    alert_contacts: str = Form(""),
    system_name: str = Form("DNS探测系统"),
    app_name: str = Form("DNS探测引擎"),
    db: Session = Depends(get_db),
):
    dns_list  = db.scalars(select(DnsServer).where(DnsServer.id.in_(dns_server_ids))).all()
    node_list = db.scalars(select(ProbeNode).where(ProbeNode.id.in_(node_ids))).all() if node_ids else []
    task = ProbeTask(
        domain=domain,
        category=category,
        record_type=record_type,
        frequency_seconds=frequency_seconds,
        timeout_seconds=timeout_seconds,
        retries=retries,
        failure_rate_threshold=failure_rate_threshold,
        consecutive_failures_threshold=consecutive_failures_threshold,
        alert_contacts=alert_contacts,
        system_name=system_name,
        app_name=app_name,
    )
    task.dns_servers = list(dns_list)
    task.nodes = list(node_list)
    db.add(task)
    db.commit()
    return RedirectResponse(url="/tasks", status_code=303)


@router.get("/tasks/{task_id}/edit")
def edit_task_page(task_id: int, request: Request, db: Session = Depends(get_db)):
    task = db.get(ProbeTask, task_id)
    dns_servers = db.scalars(select(DnsServer).order_by(DnsServer.id)).all()
    available_nodes = _assignable_nodes(db)
    selected_nodes = list(task.nodes) if task else []
    selected_dns_ids  = set(task.dns_server_ids) if task else set()
    selected_node_ids = set(task.node_ids) if task else set()
    return templates.TemplateResponse(
        request,
        "task_edit.html",
        {
            "task": task,
            "dns_servers": dns_servers,
            "selected_dns_ids": selected_dns_ids,
            "available_nodes": available_nodes,
            "selected_nodes": selected_nodes,
            "selected_node_ids": selected_node_ids,
            "form_values": _task_form_values(task),
            "form_mode": "edit",
            "form_action": f"/tasks/{task_id}/edit",
            "submit_label": "\u4fdd\u5b58\u4fee\u6539",
        },
    )


@router.post("/tasks/{task_id}/edit")
def update_task_page(
    task_id: int,
    domain: str = Form(...),
    category: str = Form("normal"),
    dns_server_ids: List[int] = Form(...),
    node_ids: List[int] = Form(default=[]),
    record_type: str = Form("A"),
    frequency_seconds: int = Form(60),
    timeout_seconds: int = Form(2),
    retries: int = Form(1),
    failure_rate_threshold: int = Form(30),
    consecutive_failures_threshold: int = Form(3),
    alert_contacts: str = Form(""),
    system_name: str = Form("DNS探测系统"),
    app_name: str = Form("DNS探测引擎"),
    db: Session = Depends(get_db),
):
    task = db.get(ProbeTask, task_id)
    if task:
        task.domain = domain
        task.category = category
        task.record_type = record_type
        task.frequency_seconds = frequency_seconds
        task.timeout_seconds = timeout_seconds
        task.retries = retries
        task.failure_rate_threshold = failure_rate_threshold
        task.consecutive_failures_threshold = consecutive_failures_threshold
        task.alert_contacts = alert_contacts
        task.system_name = system_name
        task.app_name = app_name
        dns_list  = db.scalars(select(DnsServer).where(DnsServer.id.in_(dns_server_ids))).all()
        node_list = db.scalars(select(ProbeNode).where(ProbeNode.id.in_(node_ids))).all() if node_ids else []
        task.dns_servers = list(dns_list)
        task.nodes = list(node_list)
        db.commit()
    return RedirectResponse(url="/tasks", status_code=303)


@router.post("/tasks/{task_id}/toggle")
def toggle_task_page(task_id: int, db: Session = Depends(get_db)):
    task = db.get(ProbeTask, task_id)
    if task:
        task.enabled = not task.enabled
        db.commit()
    return RedirectResponse(url="/tasks", status_code=303)


@router.post("/tasks/{task_id}/delete")
def delete_task_page(task_id: int, db: Session = Depends(get_db)):
    task = db.get(ProbeTask, task_id)
    if task:
        _delete_task_related_data(db, task)
        db.commit()
    return RedirectResponse(url="/tasks", status_code=303)


@router.get("/dns-servers")
def dns_servers_page(request: Request, message: str = "", db: Session = Depends(get_db)):
    dns_servers = db.scalars(select(DnsServer).order_by(desc(DnsServer.id))).all()
    return templates.TemplateResponse(
        request,
        "dns_servers.html",
        {"dns_servers": dns_servers, "message": message},
    )


@router.post("/dns-servers")
def create_dns_server_page(
    dns_alias: str = Form(...),
    dns_server: str = Form(...),
    category: str = Form("internal"),
    db: Session = Depends(get_db),
):
    db.add(DnsServer(dns_alias=dns_alias, dns_server=dns_server, category=category))
    db.commit()
    return RedirectResponse(url="/dns-servers", status_code=303)


@router.get("/dns-servers/{dns_server_id}/edit")
def edit_dns_server_page(dns_server_id: int, request: Request, db: Session = Depends(get_db)):
    dns_server = db.get(DnsServer, dns_server_id)
    return templates.TemplateResponse(request, "dns_server_edit.html", {"dns_server": dns_server})


@router.post("/dns-servers/{dns_server_id}/edit")
def update_dns_server_page(
    dns_server_id: int,
    dns_alias: str = Form(...),
    dns_server: str = Form(...),
    category: str = Form("internal"),
    enabled: str = Form("true"),
    db: Session = Depends(get_db),
):
    item = db.get(DnsServer, dns_server_id)
    if item:
        item.dns_alias = dns_alias
        item.dns_server = dns_server
        item.category = category
        item.enabled = enabled == "true"
        db.commit()
    return RedirectResponse(url="/dns-servers", status_code=303)


@router.post("/dns-servers/{dns_server_id}/delete")
def delete_dns_server_page(dns_server_id: int, db: Session = Depends(get_db)):
    item = db.get(DnsServer, dns_server_id)
    if item:
        ref = db.scalar(
            select(task_dns_servers.c.task_id).where(task_dns_servers.c.dns_server_id == dns_server_id)
        )
        if ref is not None:
            return RedirectResponse(url="/dns-servers?message=该DNS服务器已被任务引用，无法删除", status_code=303)
        db.delete(item)
        db.commit()
    return RedirectResponse(url="/dns-servers", status_code=303)


@router.get("/nodes")
def nodes_page(request: Request, message: str = "", db: Session = Depends(get_db)):
    nodes = db.scalars(select(ProbeNode).order_by(desc(ProbeNode.id))).all()
    return templates.TemplateResponse(request, "nodes.html", {"nodes": nodes, "message": message})


@router.post("/nodes")
def create_node_page(
    name: str = Form(...),
    expected_ip: str = Form(""),
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    if db.scalar(select(ProbeNode).where(ProbeNode.name == name)):
        return RedirectResponse(url="/nodes?message=节点名称已存在", status_code=303)
    db.add(
        ProbeNode(
            name=name,
            expected_ip=expected_ip,
            node_ip=expected_ip,
            agent_token=secrets.token_urlsafe(24),
            enabled=True,
            description=description,
            status="offline",
        )
    )
    db.commit()
    return RedirectResponse(url="/nodes?message=节点已创建，请复制下方配置到Agent机器", status_code=303)


@router.get("/nodes/{node_id}/edit")
def edit_node_page(node_id: int, request: Request, db: Session = Depends(get_db)):
    node = db.get(ProbeNode, node_id)
    return templates.TemplateResponse(request, "node_edit.html", {"node": node})


@router.post("/nodes/{node_id}/edit")
def update_node_page(
    node_id: int,
    name: str = Form(...),
    expected_ip: str = Form(""),
    description: str = Form(""),
    enabled: str = Form("true"),
    db: Session = Depends(get_db),
):
    node = db.get(ProbeNode, node_id)
    if node:
        duplicate = db.scalar(select(ProbeNode).where(ProbeNode.name == name, ProbeNode.id != node_id))
        if duplicate:
            return RedirectResponse(url=f"/nodes/{node_id}/edit", status_code=303)
        node.name = name
        node.expected_ip = expected_ip
        node.description = description
        node.enabled = enabled == "true"
        if not node.enabled:
            node.status = "offline"
        db.commit()
    return RedirectResponse(url="/nodes", status_code=303)


@router.post("/nodes/{node_id}/toggle")
def toggle_node_page(node_id: int, db: Session = Depends(get_db)):
    node = db.get(ProbeNode, node_id)
    if node:
        node.enabled = not node.enabled
        if not node.enabled:
            node.status = "offline"
        db.commit()
    return RedirectResponse(url="/nodes", status_code=303)


@router.post("/nodes/{node_id}/reset-token")
def reset_node_token_page(node_id: int, db: Session = Depends(get_db)):
    node = db.get(ProbeNode, node_id)
    if node:
        node.agent_token = secrets.token_urlsafe(24)
        db.commit()
        return RedirectResponse(url=f"/nodes?message=节点 {node.name} 的Token已重置，请同步更新Agent配置", status_code=303)
    return RedirectResponse(url="/nodes", status_code=303)


@router.post("/nodes/{node_id}/delete")
def delete_node_page(node_id: int, db: Session = Depends(get_db)):
    node = db.get(ProbeNode, node_id)
    if node:
        has_records = db.scalar(select(ProbeRecord.id).where(ProbeRecord.node_name == node.name).limit(1))
        if has_records:
            return RedirectResponse(url="/nodes?message=该节点已有探测记录，无法删除", status_code=303)
        db.delete(node)
        db.commit()
    return RedirectResponse(url="/nodes", status_code=303)


@router.get("/alerts")
def alerts_page(
    request: Request,
    q: str = "",
    status: str = "",
    level: str = "",
    db: Session = Depends(get_db),
):
    stmt = select(AlertEvent).order_by(desc(AlertEvent.last_triggered_at)).limit(200)
    if q:
        # 按关联任务域名模糊搜索
        stmt = stmt.join(ProbeTask, AlertEvent.task_id == ProbeTask.id, isouter=True).where(
            ProbeTask.domain.ilike(f"%{q}%")
        )
    if status:
        stmt = stmt.where(AlertEvent.status == status)
    if level:
        stmt = stmt.where(AlertEvent.level == level)
    alerts = db.scalars(stmt).all()
    return templates.TemplateResponse(
        request,
        "alerts.html",
        {"alerts": alerts, "q": q, "status": status, "level": level},
    )


@router.post("/alerts/{alert_id}/close")
def close_alert(alert_id: int, db: Session = Depends(get_db)):
    alert = db.get(AlertEvent, alert_id)
    if alert and alert.status == "open":
        alert.status = "recovered"
        alert.recovered_at = datetime.now(timezone.utc)
        alert.last_push_result = (alert.last_push_result or "") + " [手动关闭]"
        db.commit()
    return RedirectResponse(url=f"/alerts/{alert_id}", status_code=303)


@router.get("/alerts/{alert_id}")
def alert_detail_page(alert_id: int, request: Request, db: Session = Depends(get_db)):
    alert = db.get(AlertEvent, alert_id)
    task = db.get(ProbeTask, alert.task_id) if alert else None
    records = []
    if alert:
        window_end = alert.recovered_at or alert.last_triggered_at
        records = db.scalars(
            select(ProbeRecord)
            .where(
                ProbeRecord.task_id == alert.task_id,
                ProbeRecord.timestamp >= alert.first_triggered_at - timedelta(minutes=5),
                ProbeRecord.timestamp <= window_end + timedelta(minutes=5),
            )
            .order_by(desc(ProbeRecord.timestamp))
            .limit(200)
        ).all()
    return templates.TemplateResponse(
        request,
        "alert_detail.html",
        {"alert": alert, "task": task, "records": records},
    )


@router.get("/tasks/{task_id}")
def task_detail_page(
    task_id: int,
    request: Request,
    hours: str = "24",
    time_from: str = "",
    time_to: str = "",
    status: str | None = None,
    node: str = "",
    db: Session = Depends(get_db),
):
    task = db.get(ProbeTask, task_id)

    # 解析时间范围：自定义区间优先，否则按小时偏移
    now_utc = datetime.now(timezone.utc)
    if time_from and time_to:
        try:
            ts_from = datetime.fromisoformat(time_from).replace(tzinfo=BEIJING_TZ).astimezone(timezone.utc)
            ts_to   = datetime.fromisoformat(time_to).replace(tzinfo=BEIJING_TZ).astimezone(timezone.utc)
        except ValueError:
            ts_from = now_utc - timedelta(hours=24)
            ts_to   = now_utc
        hours_val = 0
    else:
        try:
            hours_val = int(hours)
        except ValueError:
            hours_val = 24
        ts_from = now_utc - timedelta(hours=hours_val)
        ts_to   = now_utc

    stmt = select(ProbeRecord).where(
        ProbeRecord.task_id == task_id,
        ProbeRecord.timestamp >= ts_from,
        ProbeRecord.timestamp <= ts_to,
    )
    if status:
        stmt = stmt.where(ProbeRecord.status == status)
    if node:
        stmt = stmt.where(ProbeRecord.node_name == node)
    records = db.scalars(stmt.order_by(desc(ProbeRecord.timestamp)).limit(500)).all()

    # 当前任务所有出现过的节点名，用于筛选下拉框
    all_nodes = sorted(
        {
            node_name
            for node_name in db.scalars(
                select(ProbeRecord.node_name).where(ProbeRecord.task_id == task_id)
            ).all()
            if node_name
        }
    )

    by_dns = Counter(record.dns_alias for record in records if is_failure(record.status))
    status_distribution = Counter(record.status for record in records)

    chart_time_labels: list[str] = []
    latency_series: dict[str, dict[str, object]] = {}
    chart_records = sorted(
        records,
        key=lambda rec: rec.timestamp or datetime.min.replace(tzinfo=timezone.utc),
    )
    for rec in chart_records:
        ts = rec.timestamp
        if ts:
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            time_label = ts.astimezone(BEIJING_TZ).strftime("%m-%d %H:%M:%S")
        else:
            time_label = "?"

        chart_time_labels.append(time_label)
        point_index = len(chart_time_labels) - 1

        for dataset in latency_series.values():
            dataset["data"].append(None)
            dataset["point_meta"].append(None)

        node_label = rec.probe_node or rec.node_name or "-"
        dns_label = rec.dns_alias or rec.dns_server or "-"
        dns_display = f"{dns_label} ({rec.dns_server})" if rec.dns_server and rec.dns_server != dns_label else dns_label
        series_key = f"{node_label} | {dns_display}"

        if series_key not in latency_series:
            latency_series[series_key] = {
                "label": series_key,
                "data": [None] * len(chart_time_labels),
                "point_meta": [None] * len(chart_time_labels),
            }

        latency_series[series_key]["data"][point_index] = rec.latency_ms
        latency_series[series_key]["point_meta"][point_index] = {
            "status": rec.status,
            "node": node_label,
            "dns": dns_display,
            "time": time_label,
        }

    chart_latency_datasets = list(latency_series.values())

    return templates.TemplateResponse(
        request,
        "task_detail.html",
        {
            "task": task,
            "records": records,
            "by_dns": by_dns,
            "status_distribution": list(status_distribution.items()),
            "hours": str(hours_val) if hours_val else hours,
            "time_from": time_from,
            "time_to": time_to,
            "status": status or "",
            "node": node,
            "all_nodes": all_nodes,
            # chart data
            "chart_time_labels": chart_time_labels,
            "chart_latency_datasets": chart_latency_datasets,
            "chart_status_labels": list(status_distribution.keys()),
            "chart_status_data": list(status_distribution.values()),
        },
    )
