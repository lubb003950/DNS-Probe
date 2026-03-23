from __future__ import annotations

from datetime import datetime, timezone

import httpx

from packages.core.config import settings


def utc_millis(dt: datetime | None = None) -> int:
    current = dt or datetime.now(timezone.utc)
    return int(current.timestamp() * 1000)


def split_contacts(raw: str) -> tuple[str, str]:
    if not raw.strip():
        return "", ""
    names: list[str] = []
    phones: list[str] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            name, phone = item.split(":", 1)
            names.append(name.strip())
            phones.append(phone.strip())
        else:
            names.append(item)
    return ",".join(names), ",".join(phones)


def build_payload(
    *,
    targetname: str,
    targetip: str,
    level: str,
    check: str,
    description: str,
    customerip: str,
    contacts: str,
    system_name: str,
    app_name: str,
) -> dict:
    warner, telephone = split_contacts(contacts)
    return {
        "targetname": targetname,
        "targetip": targetip,
        "level": level,
        "check": check,
        "description": description,
        "timestamp": utc_millis(),
        "alarmtype": "DNS",
        "customer": "DNS探测系统",
        "customerip": customerip or "127.0.0.1",
        "telephone": telephone,
        "warner": warner,
        "系统名称": system_name,
        "应用名称": app_name,
    }


def push_single(payload: dict) -> tuple[bool, str]:
    url = f"{settings.yunzhi_base_url}/gateway/event/v1/artemis/message/rest"
    headers = {"Content-Type": "application/json", "appkey": settings.yunzhi_appkey}
    try:
        response = httpx.post(url, json=payload, headers=headers, timeout=settings.request_timeout_seconds)
        text = response.text
        return response.is_success, text
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
