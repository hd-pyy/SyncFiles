# Progress Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make SyncFiles progress reporting thread-safe, truthful on failure, and clearer during scan and sync operations.

**Architecture:** `syncfiles.progress` owns pure progress state and ETA math. `syncfiles.app` consumes immutable snapshots on the Tkinter main thread and renders labels/progressbar mode from snapshot data. `syncfiles.executor` continues to execute copy operations and call a completion hook after each operation.

**Tech Stack:** Python 3.11, Tkinter, pytest, standard-library dataclasses/enums/queues.

---

## File Structure

- Modify `src/syncfiles/progress.py`: add explicit progress state/mode and aggregate ETA fields.
- Modify `src/syncfiles/app.py`: render progressbar mode on the UI thread, call `succeed()`/`fail()`, and advance sync to the next path.
- Modify `src/syncfiles/i18n.py`: add translated failed-progress status.
- Modify `tests/test_progress.py`: update model tests for aggregate timing and state transitions.
- Modify `tests/test_app.py`: add UI-thread rendering and failure-state tests.

### Task 1: Progress Model State and ETA Aggregation

**Files:**
- Modify: `src/syncfiles/progress.py`
- Test: `tests/test_progress.py`

- [ ] **Step 1: Write failing tests for state, failure, and aggregate ETA**

Add imports in `tests/test_progress.py`:

```python
from syncfiles.progress import ProgressMode, ProgressReporter, ProgressSnapshot, ProgressState, format_duration
```

Replace assertions that inspect `elapsed_per_file` with aggregate fields. Add these tests:

```python
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


def test_reporter_emits_failed_snapshot_without_success_state() -> None:
    reporter = ProgressReporter(on_change=lambda snap: None)

    reporter.start(total=2, current_path="a")
    reporter.fail(current_path="a")

    snap = reporter.snapshot()
    assert snap.state is ProgressState.FAILED
    assert snap.current_path == "a"
    assert snap.fraction == 0.0
```

- [ ] **Step 2: Run progress tests to verify they fail**

Run:

```powershell
python -m pytest tests/test_progress.py -q
```

Expected: FAIL because `ProgressMode`, `ProgressState`, `elapsed_seconds`, `elapsed_samples`, `succeed()`, and `fail()` are not implemented yet.

- [ ] **Step 3: Implement explicit progress state and aggregate ETA**

Update `src/syncfiles/progress.py` with these public types and methods:

```python
from enum import StrEnum


class ProgressState(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ProgressMode(StrEnum):
    DETERMINATE = "determinate"
    INDETERMINATE = "indeterminate"
```

Change `ProgressSnapshot` fields to:

```python
total: int
completed: int
current_path: str | None
elapsed_seconds: float
elapsed_samples: int
state: ProgressState
mode: ProgressMode
```

Update timing properties:

```python
@property
def average_seconds_per_file(self) -> float:
    if self.elapsed_samples <= 0:
        return 0.0
    return self.elapsed_seconds / self.elapsed_samples
```

Update `ProgressReporter` state fields and methods:

```python
self._elapsed_seconds = 0.0
self._elapsed_samples = 0
self._state = ProgressState.IDLE
self._mode = ProgressMode.DETERMINATE
```

```python
def start(
    self,
    total: int,
    current_path: str | None = None,
    mode: ProgressMode = ProgressMode.DETERMINATE,
) -> None:
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
    now = self._clock()
    if self._current_started_at is not None:
        self._elapsed_seconds += now - self._current_started_at
        self._elapsed_samples += 1
    self._completed += 1
    self._current_path = current_path
    self._current_started_at = now if current_path is not None else None
    self._emit()


def succeed(self) -> None:
    self._state = ProgressState.SUCCEEDED
    self._current_path = None
    self._current_started_at = None
    self._mode = ProgressMode.DETERMINATE
    self._emit()


def fail(self, current_path: str | None = None) -> None:
    self._state = ProgressState.FAILED
    if current_path is not None:
        self._current_path = current_path
    self._current_started_at = None
    self._mode = ProgressMode.DETERMINATE
    self._emit()
```

Return the new fields from `snapshot()`.

- [ ] **Step 4: Run progress tests to verify they pass**

Run:

```powershell
python -m pytest tests/test_progress.py -q
```

Expected: PASS for all progress tests.

- [ ] **Step 5: Commit progress model changes**

Run:

```powershell
git add src/syncfiles/progress.py tests/test_progress.py
git commit -m "feat: refine progress state model"
```

### Task 2: UI Rendering Owns Progressbar Mode and Failure State

**Files:**
- Modify: `src/syncfiles/app.py`
- Modify: `src/syncfiles/i18n.py`
- Test: `tests/test_app.py`

- [ ] **Step 1: Write failing app tests for failed rendering and worker-thread isolation**

Add imports in `tests/test_app.py`:

```python
from syncfiles.progress import ProgressMode, ProgressSnapshot, ProgressState
```

Add this fake progressbar:

```python
class ExplodingProgressBar:
    def configure(self, **_kwargs: object) -> None:
        raise AssertionError("worker must not configure progressbar directly")

    def start(self, _interval: int | None = None) -> None:
        raise AssertionError("worker must not start progressbar directly")

    def stop(self) -> None:
        raise AssertionError("worker must not stop progressbar directly")
```

Add these tests:

```python
def test_render_failed_progress_does_not_show_done() -> None:
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)
        snap = ProgressSnapshot(
            total=4,
            completed=1,
            current_path="a/b.jpg",
            elapsed_seconds=0.5,
            elapsed_samples=1,
            state=ProgressState.FAILED,
            mode=ProgressMode.DETERMINATE,
        )

        app._render_progress(snap)

        assert app.progress_status_label.cget("text") == text("progress_failed", app.language)
        assert app.progress_status_label.cget("text") != text("progress_complete", app.language)
        assert float(app.progress_bar["value"]) == snap.fraction
    finally:
        root.destroy()


def test_scan_worker_does_not_touch_progressbar_from_worker_thread(tmp_path: Path) -> None:
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)
        app.progress_bar = ExplodingProgressBar()  # type: ignore[assignment]
        local = tmp_path / "local"
        local.mkdir()
        app.adb.scan_phone_folder = lambda _phone: []  # type: ignore[method-assign]

        app._scan_worker(local, "/sdcard/Test")

        drained: list[ProgressSnapshot] = []
        while not app.progress_queue.empty():
            drained.append(app.progress_queue.get_nowait())
        assert drained[-1].state is ProgressState.SUCCEEDED
    finally:
        root.destroy()
```

- [ ] **Step 2: Run app tests to verify they fail**

Run:

```powershell
python -m pytest tests/test_app.py -q
```

Expected: FAIL because `progress_failed` is missing and `_scan_worker()` still touches `progress_bar` directly.

- [ ] **Step 3: Add failed-progress translations**

Add to `TRANSLATIONS` in `src/syncfiles/i18n.py`:

```python
"progress_failed": {
    Language.CHINESE: "失败",
    Language.ENGLISH: "Failed",
},
```

- [ ] **Step 4: Move progressbar mode rendering into `_render_progress()`**

Update imports in `src/syncfiles/app.py`:

```python
from syncfiles.progress import ProgressMode, ProgressReporter, ProgressSnapshot, ProgressState, format_duration
```

Initialize a mode tracker after creating the reporter:

```python
self._rendered_progress_mode = ProgressMode.DETERMINATE
```

In `_scan_worker()`, remove direct calls to `self.progress_bar.configure()`, `self.progress_bar.start()`, and `self.progress_bar.stop()`. Start progress with:

```python
self.progress.start(
    total=2,
    current_path=self._tr("progress_current_local"),
    mode=ProgressMode.INDETERMINATE,
)
```

On scan success call:

```python
self.progress.succeed()
```

In `_render_progress()`, set progressbar mode from the snapshot before labels:

```python
def _set_progress_mode(self, mode: ProgressMode) -> None:
    if self._rendered_progress_mode is mode:
        return
    self.progress_bar.stop()
    self.progress_bar.configure(mode=mode.value)
    if mode is ProgressMode.INDETERMINATE:
        self.progress_bar.start(80)
    self._rendered_progress_mode = mode
```

Call `_set_progress_mode(snapshot.mode)` at the start of `_render_progress()`. For non-running states, call `_set_progress_mode(ProgressMode.DETERMINATE)` and render failed separately:

```python
if snapshot.state is ProgressState.FAILED:
    self.progress_status_label.configure(text=self._tr("progress_failed"))
    self.progress_eta_label.configure(text="")
    if snapshot.current_path:
        self.progress_current_label.configure(
            text=self._tr("progress_current_file", path=snapshot.current_path)
        )
    else:
        self.progress_current_label.configure(text="")
    self.progress_bar.configure(value=snapshot.fraction)
    return
```

- [ ] **Step 5: Update background error handling to call `fail()`**

In `_run_background()`, replace defensive teardown `self.progress.finish()` with:

```python
except Exception as exc:
    message = str(exc)
    self.progress.fail()
    self._log(self._tr("log_error", message=message))
    self.root.after(0, lambda: messagebox.showerror(self._tr("dialog_error_title"), message))
finally:
    self.root.after(0, lambda: self._set_busy(False))
```

Remove duplicate `progress.succeed()` or old `finish()` calls from worker `finally` blocks. Success paths should call `succeed()` exactly once.

- [ ] **Step 6: Run app tests to verify they pass**

Run:

```powershell
python -m pytest tests/test_app.py -q
```

Expected: PASS for app tests.

- [ ] **Step 7: Commit app rendering changes**

Run:

```powershell
git add src/syncfiles/app.py src/syncfiles/i18n.py tests/test_app.py
git commit -m "fix: render progress state on ui thread"
```

### Task 3: Sync Current Path Advances to the Next Operation

**Files:**
- Modify: `src/syncfiles/app.py`
- Test: `tests/test_app.py`

- [ ] **Step 1: Write failing test for sync current path order**

Add this test to `tests/test_app.py`:

```python
class FakeTransfer:
    def __init__(self) -> None:
        self.pushes: list[tuple[str, str]] = []
        self.pulls: list[tuple[str, str]] = []

    def push(self, local_path: str, phone_path: str) -> None:
        self.pushes.append((local_path, phone_path))

    def pull(self, phone_path: str, local_path: str) -> None:
        self.pulls.append((phone_path, local_path))


def test_sync_progress_moves_current_path_to_next_operation(tmp_path: Path) -> None:
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)
        app.adb = FakeTransfer()  # type: ignore[assignment]
        app.plan = build_sync_plan(
            phone_files=[
                record("phone-a.jpg", 1, 10, SourceSide.PHONE),
                record("phone-b.jpg", 1, 10, SourceSide.PHONE),
            ],
            local_files=[],
        )
        app.conflict_choices = {}

        app._sync_worker(tmp_path / "local", "/sdcard/Test")

        snapshots: list[ProgressSnapshot] = []
        while not app.progress_queue.empty():
            snapshots.append(app.progress_queue.get_nowait())
        running_paths = [
            snap.current_path
            for snap in snapshots
            if snap.state is ProgressState.RUNNING
        ]

        assert running_paths == ["phone-a.jpg", "phone-b.jpg", None]
    finally:
        root.destroy()
```

- [ ] **Step 2: Run the new test to verify it fails**

Run:

```powershell
python -m pytest tests/test_app.py::test_sync_progress_moves_current_path_to_next_operation -q
```

Expected: FAIL because the completion hook repeats the completed operation path.

- [ ] **Step 3: Update sync hook to publish the next path**

Change the hook in `_sync_worker()`:

```python
completed_count = 0

def hook(_operation: CopyOperation, _elapsed: float) -> None:
    nonlocal completed_count
    completed_count += 1
    next_path = (
        operations[completed_count].relative_path
        if completed_count < len(operations)
        else None
    )
    self.progress.advance(current_path=next_path)
```

On sync success, call:

```python
self.progress.succeed()
```

For zero operations, start progress, then immediately call `succeed()` so rendering returns to idle according to the progress renderer rule for `total <= 0`.

- [ ] **Step 4: Run the sync-path test to verify it passes**

Run:

```powershell
python -m pytest tests/test_app.py::test_sync_progress_moves_current_path_to_next_operation -q
```

Expected: PASS.

- [ ] **Step 5: Commit sync current-path fix**

Run:

```powershell
git add src/syncfiles/app.py tests/test_app.py
git commit -m "fix: show next sync item in progress"
```

### Task 4: Final Verification

**Files:**
- Verify: all modified files

- [ ] **Step 1: Run full test suite**

Run:

```powershell
python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 2: Run import smoke check**

Run:

```powershell
python -c "from syncfiles.progress import ProgressReporter, ProgressState; from syncfiles.app import SyncFilesApp; print('imports ok')"
```

Expected output:

```text
imports ok
```

- [ ] **Step 3: Inspect git status**

Run:

```powershell
git status --short
```

Expected: only intentional progress optimization files are modified.

## Self-Review Notes

Spec coverage:

- Thread-safe Tkinter rendering is covered by Task 2.
- Failure state semantics are covered by Tasks 1 and 2.
- Sync current-file correctness is covered by Task 3.
- Aggregate ETA without per-file history is covered by Task 1.
- No cancel/pause/per-byte progress work is included.

Placeholder scan:

- The plan contains no unfilled markers or incomplete implementation steps.

Type consistency:

- `ProgressState` and `ProgressMode` are introduced in Task 1 and reused by Task 2 and Task 3.
- App code uses `succeed()` and `fail()` consistently instead of `finish()`.
