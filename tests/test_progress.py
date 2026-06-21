from __future__ import annotations

import queue
import threading

from syncfiles.progress import ProgressMode, ProgressReporter, ProgressSnapshot, ProgressState, format_duration


class FakeClock:
    """Manually-advanced monotonic clock for deterministic tests."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_format_duration_boundaries() -> None:
    assert format_duration(0.0) == "<1s"
    assert format_duration(0.99) == "<1s"
    assert format_duration(1.0) == "1s"
    assert format_duration(59) == "59s"
    assert format_duration(60) == "1m 00s"
    assert format_duration(80) == "1m 20s"
    assert format_duration(3600) == "1h 00m"
    assert format_duration(3725) == "1h 02m"


def test_reporter_emits_snapshot_on_start() -> None:
    snapshots: list[ProgressSnapshot] = []
    reporter = ProgressReporter(on_change=snapshots.append)

    reporter.start(total=3, current_path="a")

    assert len(snapshots) == 1
    snap = snapshots[0]
    assert snap.total == 3
    assert snap.completed == 0
    assert snap.current_path == "a"
    assert snap.state is ProgressState.RUNNING
    assert snap.mode is ProgressMode.DETERMINATE
    assert snap.elapsed_seconds == 0.0
    assert snap.elapsed_samples == 0


def test_reporter_records_elapsed_totals_per_advance() -> None:
    clock = FakeClock()
    snapshots: list[ProgressSnapshot] = []
    reporter = ProgressReporter(on_change=snapshots.append, clock=clock)

    reporter.start(total=3, current_path="a")
    clock.advance(1.0)
    reporter.advance(current_path="b")
    clock.advance(2.0)
    reporter.advance(current_path="c")
    clock.advance(3.0)
    reporter.advance()
    reporter.succeed()

    final = snapshots[-1]
    assert final.completed == 3
    assert final.current_path is None
    assert final.state is ProgressState.SUCCEEDED
    assert final.elapsed_seconds == 6.0
    assert final.elapsed_samples == 3
    assert final.fraction == 1.0
    assert final.remaining == 0


def test_eta_uses_average_of_aggregate_completed_deltas() -> None:
    clock = FakeClock()
    reporter = ProgressReporter(on_change=lambda snap: None, clock=clock)

    reporter.start(total=5, current_path="a")
    clock.advance(1.0)
    reporter.advance(current_path="b")
    clock.advance(3.0)
    reporter.advance(current_path="c")

    snap = reporter.snapshot()
    assert snap.elapsed_seconds == 4.0
    assert snap.elapsed_samples == 2
    assert snap.average_seconds_per_file == 2.0
    assert snap.remaining == 3
    assert snap.eta_seconds == 6.0


def test_eta_is_zero_before_any_completion() -> None:
    reporter = ProgressReporter(on_change=lambda snap: None)
    reporter.start(total=5, current_path="first")

    snap = reporter.snapshot()
    assert snap.completed == 0
    assert snap.average_seconds_per_file == 0.0
    assert snap.eta_seconds == 0.0


def test_zero_total_does_not_divide() -> None:
    reporter = ProgressReporter(on_change=lambda snap: None)
    reporter.start(total=0)

    snap = reporter.snapshot()
    assert snap.fraction == 0.0
    assert snap.remaining == 0
    assert snap.eta_seconds == 0.0


def test_zero_elapsed_delta_does_not_divide() -> None:
    clock = FakeClock()
    reporter = ProgressReporter(on_change=lambda snap: None, clock=clock)

    reporter.start(total=2)
    # No clock advance: elapsed delta is 0.0.
    reporter.advance()

    snap = reporter.snapshot()
    assert snap.elapsed_seconds == 0.0
    assert snap.elapsed_samples == 1
    assert snap.average_seconds_per_file == 0.0
    assert snap.eta_seconds == 0.0


def test_snapshot_is_immutable_after_further_advances() -> None:
    clock = FakeClock()
    reporter = ProgressReporter(on_change=lambda snap: None, clock=clock)

    reporter.start(total=3, current_path="a")
    clock.advance(1.0)
    reporter.advance(current_path="b")

    captured = reporter.snapshot()
    captured_completed = captured.completed
    captured_path = captured.current_path
    captured_elapsed = captured.elapsed_seconds
    captured_samples = captured.elapsed_samples

    clock.advance(1.0)
    reporter.advance(current_path="c")
    reporter.succeed()

    # The earlier snapshot must not reflect later state changes.
    assert captured.completed == captured_completed
    assert captured.current_path == captured_path
    assert captured.elapsed_seconds == captured_elapsed
    assert captured.elapsed_samples == captured_samples
    assert captured.state is ProgressState.RUNNING


def test_snapshot_is_frozen_dataclass() -> None:
    snap = ProgressSnapshot(
        total=1,
        completed=0,
        current_path="x",
        elapsed_seconds=0.1,
        elapsed_samples=1,
        state=ProgressState.RUNNING,
        mode=ProgressMode.DETERMINATE,
    )
    # Mutating a frozen dataclass raises.
    try:
        snap.completed = 5  # type: ignore[misc]
    except Exception as exc:  # noqa: BLE001 - we only care that mutation fails
        assert "frozen" in str(exc).lower() or "FrozenInstanceError" in type(exc).__name__
    else:
        raise AssertionError("Expected ProgressSnapshot to be immutable")


def test_start_resets_state_between_runs() -> None:
    clock = FakeClock()
    reporter = ProgressReporter(on_change=lambda snap: None, clock=clock)

    reporter.start(total=2, current_path="first")
    clock.advance(1.0)
    reporter.advance(current_path="second")
    reporter.succeed()

    reporter.start(total=5, current_path="new-first")

    snap = reporter.snapshot()
    assert snap.total == 5
    assert snap.completed == 0
    assert snap.current_path == "new-first"
    assert snap.elapsed_seconds == 0.0
    assert snap.elapsed_samples == 0
    assert snap.state is ProgressState.RUNNING


def test_reporter_emits_failed_snapshot_without_success_state() -> None:
    reporter = ProgressReporter(on_change=lambda snap: None)

    reporter.start(total=2, current_path="a")
    reporter.fail(current_path="a")

    snap = reporter.snapshot()
    assert snap.state is ProgressState.FAILED
    assert snap.current_path == "a"
    assert snap.fraction == 0.0


def test_reporter_is_safe_under_concurrent_advance() -> None:
    """Many threads calling advance() concurrently must not corrupt state.

    The reporter relies on frozen snapshots and a single writer per call,
    but real-world ADB workers may not be the only thread invoking
    ``advance`` if a future caller fans out work. This is a smoke test:
    each thread performs the same number of advances; afterwards the
    completed count equals total advances.
    """

    snapshots: queue.Queue[ProgressSnapshot] = queue.Queue()
    reporter = ProgressReporter(on_change=snapshots.put)

    reporter.start(total=10 * 100, current_path="seed")

    def worker() -> None:
        for _ in range(100):
            reporter.advance()

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    final = reporter.snapshot()
    assert final.completed == 10 * 100
    assert final.total == 10 * 100
    assert final.fraction == 1.0
    # Every queued snapshot must be a valid frozen snapshot.
    drained: list[ProgressSnapshot] = []
    while not snapshots.empty():
        drained.append(snapshots.get_nowait())
    assert drained, "expected snapshots to be emitted"
    for snap in drained:
        assert isinstance(snap, ProgressSnapshot)
        assert 0 <= snap.completed <= final.total
