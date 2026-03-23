from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import quote, urlsplit, urlunsplit


def _read_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _recommended_probe_workers() -> int:
    cpu_count = os.cpu_count() or 1
    return max(2, min(16, cpu_count * 2))


def _probe_workers() -> int:
    value = os.getenv("DNS_PROBE_WORKERS")
    if value is not None:
        return max(1, int(value))
    return _recommended_probe_workers()


def _database_url() -> str:
    explicit_url = os.getenv("DNS_PROBE_DATABASE_URL")
    db_password = os.getenv("DNS_PROBE_DB_PASSWORD", "")

    if not explicit_url:
        return "mysql+pymysql://dns_probe_user:{password}@127.0.0.1:3306/dns_probe".format(
            password=db_password,
        )

    if not db_password:
        return explicit_url

    parsed = urlsplit(explicit_url)
    if "@" not in parsed.netloc:
        return explicit_url

    userinfo, hostinfo = parsed.netloc.rsplit("@", 1)

    # Keep the URL unchanged if it already embeds a password.
    if ":" in userinfo:
        return explicit_url

    return urlunsplit(
        (
            parsed.scheme,
            f"{userinfo}:{quote(db_password, safe='')}@{hostinfo}",
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )


@dataclass(frozen=True)
class Settings:
    app_name: str = "DNS Probe System"
    api_host: str = os.getenv("DNS_PROBE_API_HOST", "127.0.0.1")
    api_port: int = int(os.getenv("DNS_PROBE_API_PORT", "8000"))
    database_url: str = _database_url()
    yunzhi_base_url: str = os.getenv(
        "DNS_PROBE_YUNZHI_BASE_URL",
        "http://170.120.130.63:18080",
    )
    yunzhi_appkey: str = os.getenv(
        "DNS_PROBE_YUNZHI_APPKEY",
        "71b3c9e2-b06f-4b33-8f81-9ca28e3c024f",
    )
    agent_name: str = os.getenv("DNS_PROBE_AGENT_NAME", "local-agent")
    agent_ip: str = os.getenv("DNS_PROBE_AGENT_IP", "127.0.0.1")
    agent_token: str = os.getenv("DNS_PROBE_AGENT_TOKEN", "")
    agent_auth_enabled: bool = _read_bool("DNS_PROBE_AGENT_AUTH_ENABLED", True)
    agent_auth_header: str = os.getenv("DNS_PROBE_AGENT_AUTH_HEADER", "X-Agent-Token")
    pull_interval_seconds: int = int(os.getenv("DNS_PROBE_PULL_INTERVAL", "30"))
    heartbeat_interval_seconds: int = int(os.getenv("DNS_PROBE_HEARTBEAT_INTERVAL", "30"))
    request_timeout_seconds: int = int(os.getenv("DNS_PROBE_REQUEST_TIMEOUT", "10"))
    failure_rate_window_minutes: int = int(os.getenv("DNS_PROBE_FAILURE_RATE_WINDOW", "5"))
    record_retention_days: int = int(os.getenv("DNS_PROBE_RECORD_RETENTION_DAYS", "30"))
    alert_retention_days: int = int(os.getenv("DNS_PROBE_ALERT_RETENTION_DAYS", "90"))
    probe_workers: int = _probe_workers()
    db_pool_size: int = int(os.getenv("DNS_PROBE_DB_POOL_SIZE", "20"))
    db_max_overflow: int = int(os.getenv("DNS_PROBE_DB_MAX_OVERFLOW", "10"))


settings = Settings()
