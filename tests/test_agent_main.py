from __future__ import annotations

from apps.agent import main


class _FakeEvent:
    def __init__(self, wait_results: list[bool]):
        self._wait_results = iter(wait_results)
        self.wait_calls: list[float] = []

    def is_set(self) -> bool:
        return False

    def wait(self, timeout: float) -> bool:
        self.wait_calls.append(timeout)
        return next(self._wait_results)


def test_heartbeat_loop_runs_on_its_own_schedule() -> None:
    calls: list[str] = []
    stop_event = _FakeEvent([False, True])
    monotonic_values = iter([0.0, 0.0, 60.0, 60.0])

    main._heartbeat_loop(
        stop_event,
        heartbeat_fn=lambda: calls.append("heartbeat"),
        interval_seconds=60,
        monotonic_fn=lambda: next(monotonic_values),
    )

    assert calls == ["heartbeat"]
    assert stop_event.wait_calls == [60.0, 60.0]


def test_heartbeat_loop_does_not_burst_after_a_delayed_cycle() -> None:
    calls: list[str] = []
    stop_event = _FakeEvent([False, True])
    monotonic_values = iter([0.0, 0.0, 150.0, 150.0])

    main._heartbeat_loop(
        stop_event,
        heartbeat_fn=lambda: calls.append("heartbeat"),
        interval_seconds=60,
        monotonic_fn=lambda: next(monotonic_values),
    )

    assert calls == ["heartbeat"]
    assert stop_event.wait_calls == [60.0, 60.0]
