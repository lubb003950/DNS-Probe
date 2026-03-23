from __future__ import annotations

import re
import shutil
import subprocess
import time
from functools import lru_cache
from typing import Callable

try:
    import dns.exception as dns_exception
    import dns.resolver as dns_resolver
except ImportError:
    dns_exception = None
    dns_resolver = None


ProbeRunner = Callable[[str, str, str, int], dict]


def _latency_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def extract_dig_answer_snippet(text: str, requested_record_type: str) -> str:
    match = re.search(r";;\s*ANSWER SECTION:\n(?P<section>.*?)(?:\n\n|\n;;|\Z)", text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    section = match.group("section").strip()
    if not section:
        return ""
    requested_record_type = (requested_record_type or "").upper()
    answers: list[tuple[str, str]] = []
    for line in section.splitlines():
        item = line.strip()
        if not item or item.startswith(";"):
            continue
        parts = item.split()
        if len(parts) >= 5:
            answers.append((parts[3].upper(), " ".join(parts[4:])))
            continue
        answers.append(("", item))
    matched = [snippet for record_type, snippet in answers if requested_record_type and record_type == requested_record_type and snippet]
    if matched:
        return ", ".join(matched)
    return answers[0][1] if answers and not requested_record_type else ""


def looks_like_dig_error(text: str) -> bool:
    lowered = text.lower()
    error_markers = (
        "timed out",
        "timeout",
        "no servers could be reached",
        "communications error",
        "connection refused",
        "network is unreachable",
        "no route to host",
        "connection reset",
        "refused",
        "servfail",
        "nxdomain",
    )
    return any(marker in lowered for marker in error_markers)


def parse_dig_result(stdout: str, stderr: str, returncode: int, requested_record_type: str) -> tuple[str, str, str]:
    combined_output = "\n".join(part.strip() for part in (stdout, stderr) if part and part.strip()).strip()
    if returncode != 0 or looks_like_dig_error(combined_output):
        status = classify_error(combined_output or "dig failed")
        return status, "", combined_output or "dig failed"

    status_match = re.search(r"status:\s*([A-Z]+)", stdout, flags=re.IGNORECASE)
    dig_status = status_match.group(1).upper() if status_match else ""
    snippet = extract_dig_answer_snippet(stdout, requested_record_type)

    if dig_status in {"NXDOMAIN", "SERVFAIL", "REFUSED"}:
        return dig_status, "", combined_output or dig_status
    if dig_status == "NOERROR":
        if snippet:
            return "NOERROR", snippet, ""
        return "ERROR", "", combined_output or "NOERROR but empty answer"
    if snippet:
        return "NOERROR", snippet, ""
    return classify_error(combined_output or "dig failed"), "", combined_output or "dig failed"


def run_native_dns(domain: str, dns_server: str, record_type: str, timeout_seconds: int) -> dict:
    if dns_resolver is None or dns_exception is None:
        return {
            "status": "ERROR",
            "latency_ms": 0,
            "result_snippet": "",
            "error_message": "dnspython is not available",
        }

    start = time.perf_counter()
    resolver = dns_resolver.Resolver(configure=False)
    resolver.nameservers = [dns_server]
    resolver.timeout = timeout_seconds
    resolver.lifetime = timeout_seconds
    if hasattr(resolver, "retry_servfail"):
        resolver.retry_servfail = False

    try:
        answer = resolver.resolve(
            domain,
            record_type,
            search=False,
            raise_on_no_answer=True,
        )
        snippet = ", ".join(str(item).strip() for item in answer if str(item).strip())
        latency_ms = _latency_ms(start)
        if snippet:
            return {
                "status": "NOERROR",
                "latency_ms": latency_ms,
                "result_snippet": snippet,
                "error_message": "",
            }
        return {
            "status": "ERROR",
            "latency_ms": latency_ms,
            "result_snippet": "",
            "error_message": "NOERROR but empty answer",
        }
    except dns_resolver.NXDOMAIN as exc:
        return {
            "status": "NXDOMAIN",
            "latency_ms": _latency_ms(start),
            "result_snippet": "",
            "error_message": str(exc) or "NXDOMAIN",
        }
    except dns_resolver.NoAnswer as exc:
        return {
            "status": "ERROR",
            "latency_ms": _latency_ms(start),
            "result_snippet": "",
            "error_message": str(exc) or "NOERROR but empty answer",
        }
    except dns_resolver.NoNameservers as exc:
        message = str(exc) or "No nameservers available"
        return {
            "status": classify_error(message),
            "latency_ms": _latency_ms(start),
            "result_snippet": "",
            "error_message": message,
        }
    except dns_exception.Timeout as exc:
        return {
            "status": "TIMEOUT",
            "latency_ms": timeout_seconds * 1000,
            "result_snippet": "",
            "error_message": str(exc) or "dns timeout",
        }
    except Exception as exc:
        message = str(exc) or "dns lookup failed"
        return {
            "status": classify_error(message),
            "latency_ms": _latency_ms(start),
            "result_snippet": "",
            "error_message": message,
        }


@lru_cache(maxsize=1)
def _probe_runner() -> ProbeRunner | None:
    if dns_resolver is not None and dns_exception is not None:
        return run_native_dns
    if shutil.which("dig"):
        return run_dig
    if shutil.which("nslookup"):
        return run_nslookup
    return None


def probe_backend_name() -> str:
    runner = _probe_runner()
    if runner is run_native_dns:
        return "dnspython"
    if runner is run_dig:
        return "dig"
    if runner is run_nslookup:
        return "nslookup"
    return "unavailable"


def probe_dns(
    *,
    domain: str,
    dns_server: str,
    record_type: str,
    timeout_seconds: int,
    retries: int = 0,
) -> dict:
    """Execute a DNS probe and retry failures when configured."""
    attempts = 1 + max(0, retries)
    runner = _probe_runner()
    if runner is None:
        return {
            "status": "ERROR",
            "latency_ms": 0,
            "result_snippet": "",
            "error_message": "Neither dnspython, dig nor nslookup is available",
        }

    result: dict = {}
    for attempt in range(attempts):
        result = runner(domain, dns_server, record_type, timeout_seconds)
        if result["status"] == "NOERROR":
            return result
        if attempt < attempts - 1:
            time.sleep(min(2 ** attempt, 8))
    return result


def run_dig(domain: str, dns_server: str, record_type: str, timeout_seconds: int) -> dict:
    start = time.perf_counter()
    cmd = [
        "dig",
        f"@{dns_server}",
        domain,
        record_type,
        f"+time={timeout_seconds}",
        "+tries=1",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds + 2, check=False)
        latency_ms = _latency_ms(start)
        status, result_snippet, error_message = parse_dig_result(
            result.stdout or "",
            result.stderr or "",
            result.returncode,
            record_type,
        )
        return {
            "status": status,
            "latency_ms": latency_ms,
            "result_snippet": result_snippet,
            "error_message": error_message,
        }
    except subprocess.TimeoutExpired:
        return {"status": "TIMEOUT", "latency_ms": timeout_seconds * 1000, "result_snippet": "", "error_message": "dig timeout"}


def run_nslookup(domain: str, dns_server: str, record_type: str, timeout_seconds: int) -> dict:
    start = time.perf_counter()
    cmd = ["nslookup", f"-type={record_type}", domain, dns_server]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds + 2, check=False)
        latency_ms = _latency_ms(start)
        output = (result.stdout or "") + "\n" + (result.stderr or "")
        if "Non-existent domain" in output:
            status = "NXDOMAIN"
        elif "timed out" in output.lower():
            status = "TIMEOUT"
        elif "refused" in output.lower():
            status = "REFUSED"
        elif result.returncode != 0:
            status = "ERROR"
        else:
            match = re.findall(r"Address(?:es)?:\s*([^\n]+)", output, flags=re.IGNORECASE)
            snippet = match[-1].strip() if match else ""
            status = "NOERROR" if snippet else "ERROR"
            return {"status": status, "latency_ms": latency_ms, "result_snippet": snippet, "error_message": ""}
        return {"status": status, "latency_ms": latency_ms, "result_snippet": "", "error_message": output.strip()}
    except subprocess.TimeoutExpired:
        return {
            "status": "TIMEOUT",
            "latency_ms": timeout_seconds * 1000,
            "result_snippet": "",
            "error_message": "nslookup timeout",
        }


def classify_error(text: str) -> str:
    lowered = text.lower()
    if "timed out" in lowered or "timeout" in lowered:
        return "TIMEOUT"
    if "nxdomain" in lowered or "non-existent" in lowered:
        return "NXDOMAIN"
    if "servfail" in lowered:
        return "SERVFAIL"
    if "refused" in lowered:
        return "REFUSED"
    return "ERROR"
