from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Callable


Clock = Callable[[], float]


class ProgressState(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ProgressMode(StrEnum):
    DETERMINATE = "determinate"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True, slots=True)
class ProgressSnapshot:
    """Immutable view of progress state at a point in time.

    The reporter is the sole writer; the UI thread only consumes these
    snapshots via a queue, so frozen-ness here is what gives us safe
    cross-thread visibility without explicit locks.
    """

    total: int
    completed: int
    current_path: str | None
    elapsed_seconds: float
    elapsed_samples: int
    state: ProgressState
    mode: ProgressMode

    @property
    def fraction(self) -> float:
        if self.total <= 0:
            return 0.0
        return min(1.0, self.completed / self.total)

    @property
    def remaining(self) -> int:
        return max(0, self.total - self.completed)

    @property
    def average_seconds_per_file(self) -> float:
        if self.elapsed_samples <= 0:
            return 0.0
        return self.elapsed_seconds / self.elapsed_samples

    @property
    def eta_seconds(self) -> float:
        return self.average_seconds_per_file * self.remaining


class ProgressReporter:
    """Tracks per-file progress and emits snapshots on each state change.

    Designed so the worker is the only writer and the UI only reads
    snapshots through an injected ``on_change`` callback (typically a
    ``queue.Queue.put``). No locking is required.
    """

    def __init__(
        self,
        on_change: Callable[[ProgressSnapshot], None],
        *,
        clock: Clock = time.perf_counter,
    ) -> None:
        self._on_change = on_change
        self._clock = clock
        self._total = 0
        self._completed = 0
        self._current_path: str | None = None
        self._current_started_at: float | None = None
        self._elapsed_seconds = 0.0
        self._elapsed_samples = 0
        self._state = ProgressState.IDLE
        self._mode = ProgressMode.DETERMINATE

    def start(
        self,
        total: int,
        current_path: str | None = None,
        mode: ProgressMode = ProgressMode.DETERMINATE,
    ) -> None:
        """Begin a new run, resetting all state.

        The first item's timer is started immediately so that even the
        very first ``advance()`` records an elapsed delta — callers can
        omit ``current_path`` for "indeterminate phase" runs and still
        get accurate per-step timings.
        """
        now = self._clock()
        self._total = max(0, total)
        self._completed = 0
        self._current_path = current_path
        self._current_started_at = now
        self._elapsed_seconds = 0.0
        self._elapsed_samples = 0
        self._state = ProgressState.RUNNING
        self._mode = mode
        self._emit()

    def advance(self, current_path: str | None = None) -> None:
        """Mark the in-flight item as completed and move to the next."""
        now = self._clock()
        if self._current_started_at is not None:
            self._elapsed_seconds += now - self._current_started_at
            self._elapsed_samples += 1
        self._completed += 1
        self._current_path = current_path
        self._current_started_at = now if current_path is not None else None
        self._emit()

    def succeed(self) -> None:
        """Mark the run as successfully complete."""
        self._state = ProgressState.SUCCEEDED
        self._current_path = None
        self._current_started_at = None
        self._mode = ProgressMode.DETERMINATE
        self._emit()

    def fail(self, current_path: str | None = None) -> None:
        """Mark the run as failed without implying completion."""
        self._state = ProgressState.FAILED
        if current_path is not None:
            self._current_path = current_path
        self._current_started_at = None
        self._mode = ProgressMode.DETERMINATE
        self._emit()

    def finish(self) -> None:
        """Compatibility alias for successful completion."""
        self.succeed()

    def snapshot(self) -> ProgressSnapshot:
        return ProgressSnapshot(
            total=self._total,
            completed=self._completed,
            current_path=self._current_path,
            elapsed_seconds=self._elapsed_seconds,
            elapsed_samples=self._elapsed_samples,
            state=self._state,
            mode=self._mode,
        )

    def _emit(self) -> None:
        self._on_change(self.snapshot())


def format_duration(seconds: float) -> str:
    """Format a duration using Windows-style compact tokens.

    Returns ``"<1s"`` for sub-second values, ``"42s"`` under a minute,
    ``"1m 20s"`` under an hour, and ``"1h 02m"`` beyond. The numeric
    tokens are language-neutral; callers wrap them in a translated label.
    """
    if seconds < 1.0:
        return "<1s"
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins:02d}m"
