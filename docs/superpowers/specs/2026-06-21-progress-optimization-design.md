# Progress Optimization Design

## Goal

Improve SyncFiles progress reporting so it is thread-safe, truthful on failures, and clearer during scan and sync work. The change should preserve the existing Tkinter app shape and keep progress logic small enough for the current project.

## Current Issues

- Scan workers call `Progressbar.configure()`, `start()`, and `stop()` from a background thread. Tkinter UI updates should be scheduled on the main thread.
- Background teardown calls `progress.finish()` after exceptions, so an errored scan or sync can show a completed state.
- Sync progress advances after each operation but keeps the completed operation as the current path, so the UI can lag by one file.
- `ProgressSnapshot` carries the full elapsed history in every snapshot. This is simple, but it grows with file count and is copied into the queue repeatedly.

## Recommended Approach

Make a focused progress-state cleanup rather than a broad UI rewrite.

The progress model will distinguish active, succeeded, failed, and idle states. Snapshots will carry enough aggregate timing data to compute ETA without retaining the full elapsed list. The app will continue to drain progress snapshots on the Tkinter main thread, and any progressbar mode changes will be represented through snapshot state instead of direct worker-thread widget calls.

## Progress Model

`ProgressSnapshot` should include:

- `total`: number of known units.
- `completed`: number of completed units.
- `current_path`: current scan phase or file path.
- `elapsed_seconds`: cumulative elapsed time for completed units.
- `elapsed_samples`: number of timing samples.
- `state`: idle, running, succeeded, or failed.
- `mode`: determinate or indeterminate, for scan phases where exact file progress is not available.

ETA should use `elapsed_seconds / elapsed_samples * remaining` when at least one sample exists. Before that, the UI should show the existing calculating text.

`ProgressReporter` should provide:

- `start(total, current_path=None, mode="determinate")`
- `advance(current_path=None)`
- `succeed()`
- `fail(current_path=None)`
- `snapshot()`

The old `finish()` method will be removed from app usage. App code will use `succeed()` and `fail()` so error handling is unambiguous.

## App Behavior

Scan:

- Start with `total=2`, `current_path=Scanning hard drive folder`, and `mode=indeterminate`.
- After local scan, advance to `Scanning phone folder`.
- On success, render the plan and mark progress as succeeded.
- On exception, mark progress as failed and show the error dialog/log.
- Do not call Tkinter widget methods directly from the scan worker.

Sync:

- Build operations before starting progress.
- Start with the first operation as `current_path`.
- After each operation, advance to the next operation path. After the last operation, clear `current_path`.
- On success, mark progress as succeeded.
- On exception, mark progress as failed.

Rendering:

- Idle shows the existing idle text and a zero bar.
- Running determinate shows `Progress: completed / total`, ETA when available, and current path.
- Running indeterminate starts the progressbar animation from the main thread.
- Succeeded shows done and a full bar when there was work. Zero-work sync returns to idle because there is no transfer progress to display.
- Failed shows a translated failed status and keeps the bar at the last known fraction.

## Tests

Add or update tests for:

- Aggregated ETA calculation without storing per-file elapsed history.
- Explicit succeeded and failed snapshots.
- Failed progress rendering does not show done.
- Progressbar mode changes are rendered through `_render_progress()`.
- Sync hook advances to the next operation rather than repeating the completed operation.
- Existing queue coalescing behavior remains intact.

## Out Of Scope

- Cancel/pause controls.
- Per-byte transfer progress from ADB.
- Large visual redesign of the Tkinter window.
- Changing sync semantics or conflict handling.
