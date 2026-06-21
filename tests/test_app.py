from pathlib import Path
import tkinter as tk

from syncfiles.app import (
    SyncFilesApp,
    build_operations_from_plan,
    default_language_label,
    phone_folder_to_choose,
)
from syncfiles.domain import (
    ConflictAction,
    FileRecord,
    SourceSide,
    build_sync_plan,
)
from syncfiles.i18n import text
from syncfiles.progress import ProgressMode, ProgressSnapshot, ProgressState


class ExplodingProgressBar:
    def configure(self, **_kwargs: object) -> None:
        raise AssertionError("worker must not configure progressbar directly")

    def start(self, _interval: int | None = None) -> None:
        raise AssertionError("worker must not start progressbar directly")

    def stop(self) -> None:
        raise AssertionError("worker must not stop progressbar directly")


class FakeTransfer:
    def __init__(self) -> None:
        self.pushes: list[tuple[str, str]] = []
        self.pulls: list[tuple[str, str]] = []

    def push(self, local_path: str, phone_path: str) -> None:
        self.pushes.append((local_path, phone_path))

    def pull(self, phone_path: str, local_path: str) -> None:
        self.pulls.append((phone_path, local_path))


def record(path: str, size: int, modified: int, side: SourceSide) -> FileRecord:
    return FileRecord(relative_path=path, size=size, modified_time=modified, side=side)


def test_build_operations_from_plan_includes_missing_files_and_conflict_choices() -> None:
    plan = build_sync_plan(
        phone_files=[
            record("phone-only.jpg", 1, 10, SourceSide.PHONE),
            record("conflict.txt", 1, 10, SourceSide.PHONE),
        ],
        local_files=[
            record("local-only.txt", 2, 20, SourceSide.LOCAL),
            record("conflict.txt", 3, 30, SourceSide.LOCAL),
        ],
    )

    operations = build_operations_from_plan(
        plan,
        {"conflict.txt": ConflictAction.USE_LOCAL},
    )

    assert [(operation.relative_path, operation.source_side, operation.destination_side) for operation in operations] == [
        ("phone-only.jpg", SourceSide.PHONE, SourceSide.LOCAL),
        ("local-only.txt", SourceSide.LOCAL, SourceSide.PHONE),
        ("conflict.txt", SourceSide.LOCAL, SourceSide.PHONE),
    ]


def test_default_language_label_is_chinese() -> None:
    assert default_language_label() == "中文"


def test_busy_state_disables_folder_and_action_buttons() -> None:
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)

        assert str(app.phone_browse_button["state"]) == "normal"
        assert str(app.scan_button["state"]) == "normal"

        app._set_busy(True)

        assert str(app.phone_browse_button["state"]) == "disabled"
        assert str(app.scan_button["state"]) == "disabled"
        assert str(app.sync_button["state"]) == "disabled"

        app._set_busy(False)

        assert str(app.phone_browse_button["state"]) == "normal"
        assert str(app.scan_button["state"]) == "normal"
        assert str(app.sync_button["state"]) == "normal"
    finally:
        root.destroy()


def test_phone_folder_choose_prefers_highlighted_directory() -> None:
    assert phone_folder_to_choose("/sdcard", "/sdcard/Download") == "/sdcard/Download"


def test_phone_folder_choose_uses_current_directory_without_highlight() -> None:
    assert phone_folder_to_choose("/sdcard/Documents", None) == "/sdcard/Documents"
    assert phone_folder_to_choose("/sdcard/Documents", "..") == "/sdcard/Documents"


def test_progress_widgets_exist_in_idle_state() -> None:
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)
        idle = text("progress_idle", app.language)

        assert float(app.progress_bar["value"]) == 0.0
        assert app.progress_status_label.cget("text") == idle
        assert app.progress_eta_label.cget("text") == idle
        assert app.progress_current_label.cget("text") == idle
    finally:
        root.destroy()


def test_drain_progress_queue_renders_injected_snapshot() -> None:
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)
        snap = ProgressSnapshot(
            total=4,
            completed=1,
            current_path="a/b.jpg",
            elapsed_seconds=0.1,
            elapsed_samples=1,
            state=ProgressState.RUNNING,
            mode=ProgressMode.DETERMINATE,
        )
        app.progress_queue.put(snap)
        app._drain_progress_queue()

        assert app._current_snapshot == snap
        assert float(app.progress_bar["value"]) == snap.fraction
        assert (
            app.progress_status_label.cget("text")
            == text("progress_x_of_n", app.language, index=1, total=4)
        )
        assert (
            app.progress_eta_label.cget("text")
            == text("progress_eta_remaining", app.language, eta="<1s")
        )
        assert (
            app.progress_current_label.cget("text")
            == text("progress_current_file", app.language, path="a/b.jpg")
        )
    finally:
        root.destroy()


def test_drain_progress_queue_coalesces_multiple_snapshots() -> None:
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)
        for completed, total in [(1, 4), (2, 4), (3, 4)]:
            app.progress_queue.put(
                ProgressSnapshot(
                    total=total,
                    completed=completed,
                    current_path=f"file-{completed}",
                    elapsed_seconds=0.1 * completed,
                    elapsed_samples=completed,
                    state=ProgressState.RUNNING,
                    mode=ProgressMode.DETERMINATE,
                )
            )
        app._drain_progress_queue()

        assert app._current_snapshot is not None
        assert app._current_snapshot.completed == 3
        assert float(app.progress_bar["value"]) == 0.75
    finally:
        root.destroy()


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
