from __future__ import annotations

from subprocess import CompletedProcess

from apps.agent.probe import run_dig


def _dig_success_output() -> str:
    return """; <<>> DiG 9.18 <<>> @10.0.0.53 example.com A +time=2 +tries=1
;; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1234
;; flags: qr rd ra; QUERY: 1, ANSWER: 1, AUTHORITY: 0, ADDITIONAL: 1

;; QUESTION SECTION:
;example.com.            IN  A

;; ANSWER SECTION:
example.com.     60  IN  A   1.1.1.1

;; Query time: 12 msec
"""


def _dig_multiple_a_output() -> str:
    return """; <<>> DiG 9.18 <<>> @223.5.5.5 cdn.csrcbank.com A +time=2 +tries=1
;; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 17750
;; flags: qr rd ra; QUERY: 1, ANSWER: 3, AUTHORITY: 0, ADDITIONAL: 1

;; QUESTION SECTION:
;cdn.csrcbank.com.              IN  A

;; ANSWER SECTION:
cdn.csrcbank.com.       557     IN  CNAME cdn.csrcbank.com.wswebpic.com.
cdn.csrcbank.com.wswebpic.com. 60  IN  A   123.126.74.240
cdn.csrcbank.com.wswebpic.com. 60  IN  A   123.117.133.190

;; Query time: 12 msec
"""


def _dig_cname_chain_output() -> str:
    return """; <<>> DiG 9.18 <<>> @10.0.0.53 eifsp.csrcbank.com A +time=2 +tries=1
;; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1234
;; flags: qr rd ra; QUERY: 1, ANSWER: 2, AUTHORITY: 0, ADDITIONAL: 1

;; QUESTION SECTION:
;eifsp.csrcbank.com.            IN  A

;; ANSWER SECTION:
eifsp.csrcbank.com.       600 IN  CNAME eifsp.gslb.csrcbank.com.
eifsp.gslb.csrcbank.com.   60 IN  A     218.4.224.139

;; Query time: 12 msec
"""


def _dig_cname_only_output() -> str:
    return """; <<>> DiG 9.18 <<>> @10.0.0.53 example.com A +time=2 +tries=1
;; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1234
;; flags: qr rd ra; QUERY: 1, ANSWER: 1, AUTHORITY: 0, ADDITIONAL: 1

;; QUESTION SECTION:
;example.com.            IN  A

;; ANSWER SECTION:
example.com.     60  IN  CNAME alias.example.com.

;; Query time: 12 msec
"""


def _dig_aaaa_cname_chain_output() -> str:
    return """; <<>> DiG 9.18 <<>> @10.0.0.53 example.com AAAA +time=2 +tries=1
;; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1234
;; flags: qr rd ra; QUERY: 1, ANSWER: 2, AUTHORITY: 0, ADDITIONAL: 1

;; QUESTION SECTION:
;example.com.            IN  AAAA

;; ANSWER SECTION:
example.com.         60  IN  CNAME ipv6.example.com.
ipv6.example.com.    60  IN  AAAA  2408:8000:1234::1

;; Query time: 12 msec
"""


def _dig_aaaa_cname_only_output() -> str:
    return """; <<>> DiG 9.18 <<>> @10.0.0.53 example.com AAAA +time=2 +tries=1
;; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1234
;; flags: qr rd ra; QUERY: 1, ANSWER: 1, AUTHORITY: 0, ADDITIONAL: 1

;; QUESTION SECTION:
;example.com.            IN  AAAA

;; ANSWER SECTION:
example.com.         60  IN  CNAME ipv6.example.com.

;; Query time: 12 msec
"""


def _dig_status_output(status: str) -> str:
    return f"""; <<>> DiG 9.18 <<>> @10.0.0.53 example.com A +time=2 +tries=1
;; ->>HEADER<<- opcode: QUERY, status: {status}, id: 1234
;; flags: qr rd ra; QUERY: 1, ANSWER: 0, AUTHORITY: 0, ADDITIONAL: 1

;; QUESTION SECTION:
;example.com.            IN  A
"""


def test_run_dig_timeout_output_is_not_treated_as_noerror(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        return CompletedProcess(
            args=args[0],
            returncode=9,
            stdout=";; communications error to 10.0.0.53#53: timed out\n;; no servers could be reached\n",
            stderr="",
        )

    monkeypatch.setattr("apps.agent.probe.subprocess.run", fake_run)

    result = run_dig("example.com", "10.0.0.53", "A", 2)

    assert result["status"] == "TIMEOUT"
    assert result["result_snippet"] == ""
    assert "timed out" in result["error_message"].lower()


def test_run_dig_success_returns_noerror(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        return CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=_dig_success_output(),
            stderr="",
        )

    monkeypatch.setattr("apps.agent.probe.subprocess.run", fake_run)

    result = run_dig("example.com", "10.0.0.53", "A", 2)

    assert result["status"] == "NOERROR"
    assert result["result_snippet"] == "1.1.1.1"
    assert result["error_message"] == ""


def test_run_dig_a_record_with_cname_chain_returns_final_a(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        return CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=_dig_cname_chain_output(),
            stderr="",
        )

    monkeypatch.setattr("apps.agent.probe.subprocess.run", fake_run)

    result = run_dig("eifsp.csrcbank.com", "10.0.0.53", "A", 2)

    assert result["status"] == "NOERROR"
    assert result["result_snippet"] == "218.4.224.139"
    assert result["error_message"] == ""


def test_run_dig_a_record_without_final_a_is_error(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        return CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=_dig_cname_only_output(),
            stderr="",
        )

    monkeypatch.setattr("apps.agent.probe.subprocess.run", fake_run)

    result = run_dig("example.com", "10.0.0.53", "A", 2)

    assert result["status"] == "ERROR"
    assert result["result_snippet"] == ""


def test_run_dig_aaaa_record_with_cname_chain_returns_final_aaaa(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        return CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=_dig_aaaa_cname_chain_output(),
            stderr="",
        )

    monkeypatch.setattr("apps.agent.probe.subprocess.run", fake_run)

    result = run_dig("example.com", "10.0.0.53", "AAAA", 2)

    assert result["status"] == "NOERROR"
    assert result["result_snippet"] == "2408:8000:1234::1"
    assert result["error_message"] == ""


def test_run_dig_aaaa_record_without_final_aaaa_is_error(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        return CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=_dig_aaaa_cname_only_output(),
            stderr="",
        )

    monkeypatch.setattr("apps.agent.probe.subprocess.run", fake_run)

    result = run_dig("example.com", "10.0.0.53", "AAAA", 2)

    assert result["status"] == "ERROR"
    assert result["result_snippet"] == ""


def test_run_dig_nxdomain(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        return CompletedProcess(args=args[0], returncode=0, stdout=_dig_status_output("NXDOMAIN"), stderr="")

    monkeypatch.setattr("apps.agent.probe.subprocess.run", fake_run)

    result = run_dig("not-exists.example.com", "10.0.0.53", "A", 2)

    assert result["status"] == "NXDOMAIN"
    assert result["result_snippet"] == ""


def test_run_dig_servfail(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        return CompletedProcess(args=args[0], returncode=0, stdout=_dig_status_output("SERVFAIL"), stderr="")

    monkeypatch.setattr("apps.agent.probe.subprocess.run", fake_run)

    result = run_dig("broken.example.com", "10.0.0.53", "A", 2)

    assert result["status"] == "SERVFAIL"
    assert result["result_snippet"] == ""


def test_run_dig_refused(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        return CompletedProcess(args=args[0], returncode=0, stdout=_dig_status_output("REFUSED"), stderr="")

    monkeypatch.setattr("apps.agent.probe.subprocess.run", fake_run)

    result = run_dig("private.example.com", "10.0.0.53", "A", 2)

    assert result["status"] == "REFUSED"
    assert result["result_snippet"] == ""


def test_run_dig_noerror_without_answer_is_error(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        return CompletedProcess(args=args[0], returncode=0, stdout=_dig_status_output("NOERROR"), stderr="")

    monkeypatch.setattr("apps.agent.probe.subprocess.run", fake_run)

    result = run_dig("empty-answer.example.com", "10.0.0.53", "A", 2)

    assert result["status"] == "ERROR"
    assert result["result_snippet"] == ""


def test_run_dig_multiple_a_records_all_returned(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        return CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=_dig_multiple_a_output(),
            stderr="",
        )

    monkeypatch.setattr("apps.agent.probe.subprocess.run", fake_run)

    result = run_dig("cdn.csrcbank.com", "223.5.5.5", "A", 2)

    assert result["status"] == "NOERROR"
    assert "123.126.74.240" in result["result_snippet"]
    assert "123.117.133.190" in result["result_snippet"]
    assert result["error_message"] == ""
