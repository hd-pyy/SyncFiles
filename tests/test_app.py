from pathlib import Path
import tkinter as tk

import pytest

from syncfiles.app import (
    SyncMode,
    SyncFilesApp,
    build_operations_from_plan,
    default_language_label,
    folder_basename,
    folders_share_basename,
    phone_folder_to_choose,
)
from syncfiles.domain import (
    ConflictAction,
    FileRecord,
    SourceSide,
    build_sync_plan,
)
from syncfiles.i18n import Language, text
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


class FakeSftpSession:
    def __init__(self, records: list[FileRecord] | None = None) -> None:
        self.records = records or []
        self.scans: list[str] = []
        self.uploads: list[tuple[str, str]] = []
        self.downloads: list[tuple[str, str]] = []
        self.closed = False

    def __enter__(self) -> "FakeSftpSession":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.closed = True

    def scan_folder(self, remote_root: str, is_cancelled=None) -> list[FileRecord]:
        self.scans.append(remote_root)
        if is_cancelled is not None:
            assert is_cancelled() is False
        return self.records

    def upload_file(self, local_path: Path, remote_path: str) -> None:
        self.uploads.append((str(local_path), remote_path))

    def download_file(self, remote_path: str, local_path: Path) -> None:
        self.downloads.append((remote_path, str(local_path)))


class FakeSftpClient:
    def __init__(self, session: FakeSftpSession) -> None:
        self.session = session
        self.configs: list[object] = []

    def connect(self, config: object) -> FakeSftpSession:
        self.configs.append(config)
        return self.session


def configure_sftp_app(app: SyncFilesApp, remote_root: str = "/remote") -> None:
    app.sync_mode = SyncMode.SFTP
    app.sftp_host.set("example.com")
    app.sftp_port.set("22")
    app.sftp_username.set("alice")
    app.sftp_password.set("secret")
    app.phone_root.set(remote_root)
    app._refresh_mode_ui()


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


def test_folder_basename_handles_windows_paths() -> None:
    assert folder_basename("C:\\Users\\foo\\Photos") == "Photos"


def test_folder_basename_handles_unix_paths() -> None:
    assert folder_basename("/sdcard/Download") == "Download"


def test_folder_basename_strips_trailing_separators() -> None:
    assert folder_basename("/sdcard/Download/") == "Download"
    assert folder_basename("C:\\Users\\foo\\") == "foo"


def test_folder_basename_returns_empty_for_roots() -> None:
    assert folder_basename("/") == ""
    assert folder_basename("") == ""


def test_folders_share_basename_matches_same_name_across_os() -> None:
    assert folders_share_basename(
        "D:\\Photos\\Vacation2024", "/sdcard/DCIM/Vacation2024"
    )


def test_folders_share_basename_detects_mismatch() -> None:
    assert not folders_share_basename(
        "D:\\Photos\\Vacation2024", "/sdcard/DCIM/Camera"
    )


def test_folders_share_basename_passes_through_roots() -> None:
    assert folders_share_basename("/", "/sdcard")
    assert folders_share_basename("D:\\Photos", "")


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


def test_default_sync_mode_is_hard_drive_to_hard_drive() -> None:
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)

        assert app.sync_mode is SyncMode.HARD_DRIVE
        assert app.mode_label.get() == text("sync_mode_hard_drive", app.language)
        assert str(app.check_device_button["state"]) == "disabled"
        assert app.second_folder_label.cget("text") == text("label_right_folder", app.language)
        assert app.second_choose_button.cget("text") == text("button_choose", app.language)
        assert app.notebook.tab(app.phone_to_local_list._syncfiles_container, "text") == text(
            "tab_right_to_left",
            app.language,
        )
    finally:
        root.destroy()


def test_changing_sync_mode_updates_labels_and_clears_plan() -> None:
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)
        app.plan = build_sync_plan(phone_files=[], local_files=[])
        app.conflict_choices = {"x": ConflictAction.SKIP}

        app.mode_label.set(text("sync_mode_phone", app.language))
        app.change_sync_mode()

        assert app.sync_mode is SyncMode.PHONE
        assert app.plan is None
        assert app.conflict_choices == {}
        assert str(app.check_device_button["state"]) == "normal"
        assert app.second_folder_label.cget("text") == text("label_phone_folder", app.language)
        assert app.second_choose_button.cget("text") == text("button_browse_phone", app.language)
        assert app.notebook.tab(app.phone_to_local_list._syncfiles_container, "text") == text(
            "tab_phone_to_local",
            app.language,
        )
    finally:
        root.destroy()


def test_sftp_sync_mode_updates_labels_and_shows_connection_fields() -> None:
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)

        app.mode_label.set(text("sync_mode_sftp", app.language))
        app.change_sync_mode()

        assert app.sync_mode is SyncMode.SFTP
        assert str(app.check_device_button["state"]) == "disabled"
        assert app.first_folder_label.cget("text") == text("label_local_folder", app.language)
        assert app.second_folder_label.cget("text") == text("label_sftp_remote_folder", app.language)
        assert str(app.second_choose_button["state"]) == "disabled"
        assert app.sftp_frame.winfo_manager() == "pack"
        assert app.notebook.tab(app.phone_to_local_list._syncfiles_container, "text") == text(
            "tab_sftp_to_local",
            app.language,
        )
    finally:
        root.destroy()


def test_sftp_config_validation_requires_credentials() -> None:
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)
        app.language = Language.ENGLISH
        app.sync_mode = SyncMode.SFTP
        app.phone_root.set("/remote")

        with pytest.raises(ValueError, match="Enter SFTP host, username, password, and remote folder."):
            app._sftp_config()
    finally:
        root.destroy()


def test_sftp_config_validation_rejects_invalid_port() -> None:
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)
        app.language = Language.ENGLISH
        app.sync_mode = SyncMode.SFTP
        app.sftp_host.set("example.com")
        app.sftp_port.set("abc")
        app.sftp_username.set("alice")
        app.sftp_password.set("secret")
        app.phone_root.set("/remote")

        with pytest.raises(ValueError, match="Enter an SFTP port between 1 and 65535."):
            app._sftp_config()
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


def test_conflict_action_labels_follow_sync_mode() -> None:
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)

        assert app._conflict_action_label(ConflictAction.USE_PHONE) == text("conflict_use_right", app.language)

        app.sync_mode = SyncMode.PHONE
        assert app._conflict_action_label(ConflictAction.USE_PHONE) == text("conflict_use_phone", app.language)
    finally:
        root.destroy()


def test_scan_worker_does_not_touch_progressbar_from_worker_thread(tmp_path: Path) -> None:
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)
        app.sync_mode = SyncMode.PHONE
        app._refresh_mode_ui()
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


def test_hard_drive_mode_scans_two_local_folders(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    (left / "left-only.txt").write_text("left", encoding="utf-8")
    (right / "right-only.txt").write_text("right", encoding="utf-8")
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)
        app.local_root.set(str(left))
        app.phone_root.set(str(right))

        app._scan_worker(left, str(right))

        assert app.plan is not None
        assert [item.relative_path for item in app.plan.local_to_phone] == ["left-only.txt"]
        assert [item.relative_path for item in app.plan.phone_to_local] == ["right-only.txt"]
    finally:
        root.destroy()


def test_phone_mode_scan_still_uses_adb(tmp_path: Path) -> None:
    left = tmp_path / "left"
    left.mkdir()
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)
        app.sync_mode = SyncMode.PHONE
        app._refresh_mode_ui()
        captured: list[str] = []
        app.adb.scan_phone_folder = lambda phone: captured.append(phone) or []  # type: ignore[method-assign]

        app._scan_worker(left, "/sdcard/DCIM")

        assert captured == ["/sdcard/DCIM"]
    finally:
        root.destroy()


def test_sftp_mode_scan_uses_sftp_client_not_adb(tmp_path: Path) -> None:
    left = tmp_path / "left"
    left.mkdir()
    (left / "local-only.txt").write_text("left", encoding="utf-8")
    session = FakeSftpSession(
        [
            record("remote-only.txt", 6, 1, SourceSide.PHONE),
        ]
    )
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)
        configure_sftp_app(app)
        app.sftp_client = FakeSftpClient(session)  # type: ignore[assignment]
        app.adb.scan_phone_folder = lambda _path: (_ for _ in ()).throw(AssertionError("ADB was used"))  # type: ignore[method-assign]

        app._scan_worker(left, "/remote")

        assert session.scans == ["/remote"]
        assert session.closed is True
        assert app.plan is not None
        assert [item.relative_path for item in app.plan.local_to_phone] == ["local-only.txt"]
        assert [item.relative_path for item in app.plan.phone_to_local] == ["remote-only.txt"]
    finally:
        root.destroy()


def test_hard_drive_mode_sync_copies_between_local_roots(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    (left / "left-only.txt").write_text("left", encoding="utf-8")
    (right / "right-only.txt").write_text("right", encoding="utf-8")
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)
        app.sync_mode = SyncMode.HARD_DRIVE
        app.plan = build_sync_plan(
            phone_files=[
                record("right-only.txt", 5, 1, SourceSide.PHONE),
            ],
            local_files=[
                record("left-only.txt", 4, 1, SourceSide.LOCAL),
            ],
        )
        app.conflict_choices = {}

        app._sync_worker(left, str(right))

        assert (right / "left-only.txt").read_text(encoding="utf-8") == "left"
        assert (left / "right-only.txt").read_text(encoding="utf-8") == "right"
    finally:
        root.destroy()


def test_sftp_mode_sync_uses_sftp_executor(tmp_path: Path) -> None:
    left = tmp_path / "left"
    left.mkdir()
    (left / "local-only.txt").write_text("left", encoding="utf-8")
    session = FakeSftpSession()
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)
        configure_sftp_app(app)
        app.sftp_client = FakeSftpClient(session)  # type: ignore[assignment]
        app.plan = build_sync_plan(
            phone_files=[
                record("remote-only.txt", 6, 1, SourceSide.PHONE),
            ],
            local_files=[
                record("local-only.txt", 4, 1, SourceSide.LOCAL),
            ],
        )
        app.conflict_choices = {}

        app._sync_worker(left, "/remote")

        assert session.downloads == [("/remote/remote-only.txt", str(left / "remote-only.txt"))]
        assert session.uploads == [(str(left / "local-only.txt"), "/remote/local-only.txt")]
        assert session.closed is True
    finally:
        root.destroy()


def test_phone_mode_sync_still_uses_adb_transfer(tmp_path: Path) -> None:
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)
        app.sync_mode = SyncMode.PHONE
        app.adb = FakeTransfer()  # type: ignore[assignment]
        app.plan = build_sync_plan(
            phone_files=[
                record("phone-only.txt", 5, 1, SourceSide.PHONE),
            ],
            local_files=[
                record("local-only.txt", 4, 1, SourceSide.LOCAL),
            ],
        )
        app.conflict_choices = {}

        app._sync_worker(tmp_path / "local", "/sdcard/Test")

        assert app.adb.pulls == [("/sdcard/Test/phone-only.txt", str(tmp_path / "local" / "phone-only.txt"))]
        assert app.adb.pushes == [(str(tmp_path / "local" / "local-only.txt"), "/sdcard/Test/local-only.txt")]
    finally:
        root.destroy()


def test_sftp_conflict_action_labels_follow_sync_mode() -> None:
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)
        app.sync_mode = SyncMode.SFTP

        assert app._conflict_action_label(ConflictAction.USE_PHONE) == text("conflict_use_sftp", app.language)
        assert app._conflict_action_label(ConflictAction.USE_LOCAL) == text("conflict_use_hard_drive", app.language)
    finally:
        root.destroy()


def test_sync_progress_moves_current_path_to_next_operation(tmp_path: Path) -> None:
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)
        app.sync_mode = SyncMode.PHONE
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
