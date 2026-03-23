from __future__ import annotations

from enum import Enum


class DnsCategory(str, Enum):
    INTERNAL = "internal"
    PUBLIC = "public"


class TaskCategory(str, Enum):
    CORE = "core"
    NORMAL = "normal"
    TEST = "test"


class RecordType(str, Enum):
    A = "A"
    AAAA = "AAAA"
    CNAME = "CNAME"


class ProbeStatus(str, Enum):
    NOERROR = "NOERROR"
    TIMEOUT = "TIMEOUT"
    SERVFAIL = "SERVFAIL"
    NXDOMAIN = "NXDOMAIN"
    REFUSED = "REFUSED"
    ERROR = "ERROR"


class NodeStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"


class AlertRuleType(str, Enum):
    CONSECUTIVE_FAILURES = "consecutive_failures"
    FAILURE_RATE = "failure_rate"


class AlertStatus(str, Enum):
    OPEN = "open"
    RECOVERED = "recovered"


class YunzhiLevel(str, Enum):
    OK = "OK"
    MINOR = "Minor"
    MODERATE = "Moderate"
    MAJOR = "Major"
    CRITICAL = "Critical"
