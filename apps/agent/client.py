from __future__ import annotations

import atexit
import logging

import httpx

from packages.core.config import settings


BASE_URL = f"http://{settings.api_host}:{settings.api_port}"
logger = logging.getLogger(__name__)
_CLIENT: httpx.Client | None = None


def _agent_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    if settings.agent_auth_enabled:
        if not settings.agent_token:
            raise RuntimeError("Agent authentication is enabled, but DNS_PROBE_AGENT_TOKEN is missing")
        headers[settings.agent_auth_header] = settings.agent_token
    return headers


def _get_client() -> httpx.Client:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = httpx.Client(
            base_url=BASE_URL,
            timeout=settings.request_timeout_seconds,
        )
    return _CLIENT


def _close_client() -> None:
    global _CLIENT
    if _CLIENT is not None:
        _CLIENT.close()
        _CLIENT = None


def _request(method: str, path: str, **kwargs) -> httpx.Response:
    response = _get_client().request(
        method,
        path,
        headers=_agent_headers(),
        **kwargs,
    )
    if response.status_code in {401, 403}:
        logger.error(
            "Agent API authentication failed: %s %s -> %s %s",
            method,
            path,
            response.status_code,
            response.text,
        )
    response.raise_for_status()
    return response


def register_node() -> None:
    payload = {"name": settings.agent_name, "node_ip": settings.agent_ip}
    _request("POST", "/api/nodes/register", json=payload)


def heartbeat() -> None:
    payload = {"name": settings.agent_name, "node_ip": settings.agent_ip}
    _request("POST", "/api/nodes/heartbeat", json=payload)


def pull_tasks() -> list[dict]:
    response = _request("GET", f"/api/nodes/{settings.agent_name}/tasks")
    return response.json()


def report_record(payload: dict) -> None:
    _request("POST", "/api/records", json=payload)


atexit.register(_close_client)
