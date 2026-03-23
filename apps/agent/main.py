from __future__ import annotations

import atexit
import hashlib
import logging
import os
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from apps.agent.client import heartbeat, pull_tasks, register_node, report_record
from apps.agent.probe import probe_backend_name, probe_dns
from packages.core.config import settings
from packages.core.logging_config import setup_logging

setup_logging("agent")
logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_LOCK_FILE = _DATA_DIR / "agent.pid"


def _recommended_probe_workers() -> int:
    cpu_count = os.cpu_count() or 1
    return max(2, min(16, cpu_count * 2))


def _initial_probe_delay(task: dict) -> int:
    frequency = max(1, int(task["frequency_seconds"]))
    seed = f'{settings.agent_name}:{task["id"]}:{task["dns_server"]}'.encode("utf-8")
    digest = hashlib.blake2s(seed, digest_size=4).digest()
    offset = int.from_bytes(digest, "big") % frequency
    if frequency > 1:
        return max(1, offset)
    return 0


def _acquire_lock() -> None:
    """Prevent multiple agent processes from running on the same node."""
    _DATA_DIR.mkdir(exist_ok=True)
    if _LOCK_FILE.exists():
        try:
            pid = int(_LOCK_FILE.read_text().strip())
            os.kill(pid, 0)
            logger.error(
                "Agent is already running (PID %d). Remove %s only if that process is gone.",
                pid,
                _LOCK_FILE,
            )
            sys.exit(1)
        except OSError:
            logger.warning("Found a stale PID file, replacing it and continuing to start.")

    _LOCK_FILE.write_text(str(os.getpid()))
    logger.info("Agent started. pid=%d lock=%s", os.getpid(), _LOCK_FILE)


def _release_lock() -> None:
    _LOCK_FILE.unlink(missing_ok=True)


def _signal_handler(sig: int, _frame) -> None:
    logger.info("Received signal %d, agent is shutting down.", sig)
    _release_lock()
    sys.exit(0)


def _register_with_retry(max_wait: int = 300) -> None:
    """Retry node registration with exponential backoff until it succeeds."""
    delay = 5
    attempt = 0
    while True:
        attempt += 1
        try:
            register_node()
            logger.info("Node registration succeeded on attempt %d.", attempt)
            return
        except Exception:
            logger.warning("Node registration failed on attempt %d, retrying in %ds.", attempt, delay)
            time.sleep(delay)
            delay = min(delay * 2, max_wait)


def _probe_and_report(task: dict, next_due: dict, lock: threading.Lock) -> None:
    """Run a single probe in the worker pool and report the result."""
    task_id = task["id"]
    schedule_key = (task_id, task["dns_server"])
    try:
        result = probe_dns(
            domain=task["domain"],
            dns_server=task["dns_server"],
            record_type=task["record_type"],
            timeout_seconds=task["timeout_seconds"],
            retries=task.get("retries", 0),
        )
        payload = {
            "task_id": task_id,
            "node_name": settings.agent_name,
            "probe_node": settings.agent_name,
            "dns_alias": task["dns_alias"],
            "dns_server": task["dns_server"],
            "domain": task["domain"],
            "record_type": task["record_type"],
            "status": result["status"],
            "latency_ms": result["latency_ms"],
            "result_snippet": result["result_snippet"],
            "error_message": result["error_message"],
        }
        report_record(payload)
        logger.debug(
            "Probe finished %s via %s -> %s (%dms)",
            task["domain"],
            task["dns_alias"],
            result["status"],
            result["latency_ms"],
        )
    except Exception:
        logger.exception(
            "Task %d failed during execution (%s via %s).",
            task_id,
            task.get("domain"),
            task.get("dns_alias"),
        )
    finally:
        with lock:
            next_due[schedule_key] = time.monotonic() + task["frequency_seconds"]


def run_agent() -> None:
    _acquire_lock()
    atexit.register(_release_lock)
    signal.signal(signal.SIGTERM, _signal_handler)

    _register_with_retry()
    last_heartbeat = 0.0
    next_due: dict[tuple[int, str], float] = {}
    lock = threading.Lock()
    recommended_workers = _recommended_probe_workers()

    if settings.probe_workers > recommended_workers:
        logger.warning(
            "DNS_PROBE_WORKERS=%d is higher than the recommended value %d for this node. Small nodes should lower it.",
            settings.probe_workers,
            recommended_workers,
        )

    logger.info(
        "Agent running. heartbeat=%ss pull=%ss workers=%d backend=%s",
        settings.heartbeat_interval_seconds,
        settings.pull_interval_seconds,
        settings.probe_workers,
        probe_backend_name(),
    )

    with ThreadPoolExecutor(max_workers=settings.probe_workers) as executor:
        while True:
            now = time.monotonic()

            if now - last_heartbeat >= settings.heartbeat_interval_seconds:
                try:
                    heartbeat()
                    last_heartbeat = now
                except Exception:
                    logger.exception("Heartbeat failed and will be retried in the next cycle.")

            try:
                task_list = pull_tasks()
            except Exception:
                logger.exception("Pulling tasks failed and will be retried in the next cycle.")
                time.sleep(settings.pull_interval_seconds)
                continue

            submitted = 0
            for task in task_list:
                schedule_key = (task["id"], task["dns_server"])
                with lock:
                    due_at = next_due.get(schedule_key)
                    if due_at is None:
                        next_due[schedule_key] = now + _initial_probe_delay(task)
                        continue
                    if now < due_at:
                        continue
                    next_due[schedule_key] = now + task["frequency_seconds"]
                executor.submit(_probe_and_report, task, next_due, lock)
                submitted += 1

            if submitted:
                logger.debug("Submitted %d probe jobs in this cycle.", submitted)

            time.sleep(settings.pull_interval_seconds)


if __name__ == "__main__":
    run_agent()
