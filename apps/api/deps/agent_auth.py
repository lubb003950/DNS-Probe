from __future__ import annotations

import hmac
import logging
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.core.config import settings
from packages.db.models import ProbeNode
from packages.db.session import get_db


logger = logging.getLogger(__name__)


def _forbidden(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=message)


def _unauthorized(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=message)


def _service_unavailable(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=message)


def verify_agent_token(agent_name: str, provided_token: str | None, db: Session) -> str:
    if not settings.agent_auth_enabled:
        return agent_name
    if not agent_name:
        raise _unauthorized("Missing agent identity")
    node = db.scalar(select(ProbeNode).where(ProbeNode.name == agent_name))
    if node is None:
        raise _forbidden("Agent is not pre-registered")
    if not node.enabled:
        raise _forbidden("Agent is disabled")
    if not node.agent_token:
        logger.error("预注册节点 %s 缺少 agent_token", agent_name)
        raise _service_unavailable("Agent token is not configured")
    if not provided_token:
        raise _unauthorized("Missing agent token")
    if not hmac.compare_digest(provided_token, node.agent_token):
        raise _forbidden("Invalid agent token")
    return agent_name


async def _load_json_body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:  # pragma: no cover - framework/body errors
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON body") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON body")
    return payload


async def authenticate_node_payload(request: Request, db: Session = Depends(get_db)) -> str:
    payload = await _load_json_body(request)
    agent_name = str(payload.get("name") or "").strip()
    provided_token = request.headers.get(settings.agent_auth_header)
    return verify_agent_token(agent_name, provided_token, db)


async def authenticate_record_payload(request: Request, db: Session = Depends(get_db)) -> str:
    payload = await _load_json_body(request)
    agent_name = str(payload.get("node_name") or "").strip()
    provided_token = request.headers.get(settings.agent_auth_header)
    return verify_agent_token(agent_name, provided_token, db)


async def authenticate_node_path(
    request: Request,
    node_name: str,
    db: Session = Depends(get_db),
) -> str:
    provided_token = request.headers.get(settings.agent_auth_header)
    return verify_agent_token(node_name, provided_token, db)
