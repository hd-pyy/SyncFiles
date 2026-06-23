from pathlib import Path
import tkinter as tk

from syncfiles.app import (
    SyncMode,
    SyncFilesApp,
    _build_row_views,
    _direction_glyph_for_row,
    build_operations_from_plan,
    build_operations_from_view,
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
        # The new dual-pane UI uses one header label per side, not notebook tabs.
        assert app.left_pane_header.cget("text") == text("pane_left_header_hard_drive", app.language)
        assert app.right_pane_header.cget("text") == text("pane_right_header_hard_drive", app.language)
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
        assert app.left_pane_header.cget("text") == text("pane_left_header_phone", app.language)
        assert app.right_pane_header.cget("text") == text("pane_right_header_phone", app.language)
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


# --- New dual-pane UI tests --------------------------------------------------


def _file(path: str, size: int = 10, modified: int = 100, side: SourceSide = SourceSide.PHONE) -> FileRecord:
    return FileRecord(relative_path=path, size=size, modified_time=modified, side=side)


def test_grouped_treeview_shows_different_and_conflict_sections() -> None:
    """A scan with phone-only, local-only, and conflict rows produces
    two group headers (different + conflict) with one band row each and
    the data rows as children of their group."""
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)
        app.plan = build_sync_plan(
            phone_files=[
                _file("phone-only.jpg", 1, 10, SourceSide.PHONE),
                _file("conflict.txt", 1, 10, SourceSide.PHONE),
            ],
            local_files=[
                _file("local-only.txt", 2, 20, SourceSide.LOCAL),
                _file("conflict.txt", 3, 30, SourceSide.LOCAL),
            ],
        )
        app._render_plan()

        top_level = list(app.tree.get_children(""))
        assert "group_different" in top_level
        assert "group_conflict" in top_level
        assert "group_identical" not in top_level  # toggle off by default

        diff_children = list(app.tree.get_children("group_different"))
        assert "__group_different_band" in diff_children
        assert "phone-only.jpg" in diff_children
        assert "local-only.txt" in diff_children
        assert "conflict.txt" not in diff_children  # lives under the conflict group

        conflict_children = list(app.tree.get_children("group_conflict"))
        assert "__group_conflict_band" in conflict_children
        assert "conflict.txt" in conflict_children
    finally:
        root.destroy()


def test_grouped_treeview_collapses_and_expands() -> None:
    """Each group header toggles independently via tree.item(iid, open=...)."""
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)
        app.plan = build_sync_plan(
            phone_files=[_file("phone-only.jpg", 1, 10, SourceSide.PHONE)],
            local_files=[_file("local-only.txt", 2, 20, SourceSide.LOCAL)],
        )
        app._render_plan()

        assert bool(app.tree.item("group_different", "open")) is True
        app.tree.item("group_different", open=False)
        assert bool(app.tree.item("group_different", "open")) is False
        app.tree.item("group_different", open=True)
        assert bool(app.tree.item("group_different", "open")) is True
    finally:
        root.destroy()


def test_compact_row_shows_filename_and_meta_on_two_lines() -> None:
    """Each cell contains 'filename\\ndate · size' so the two lines render
    in the row's taller rowheight."""
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)
        app.plan = build_sync_plan(
            phone_files=[],
            local_files=[_file("docs/readme.txt", 2_500_000, 1719080400, SourceSide.LOCAL)],
        )
        app._render_plan()

        values = app.tree.item("docs/readme.txt", "values")
        # local-only row → left cell has filename\nmeta, right cell empty.
        assert values[0].startswith("docs/readme.txt\n")
        assert "2024-06-22" in values[0]  # _format_mtime of epoch 1719080400 UTC
        assert "2.4 MB" in values[0]
        assert values[2] == ""  # right (phone) cell is empty
    finally:
        root.destroy()


def test_show_identical_toggle_creates_identical_group() -> None:
    """Toggling the checkbox creates a third 'identical' group with the
    gray band and the matching file as a child."""
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)
        app.plan = build_sync_plan(
            phone_files=[_file("same.bin", 42, 200, SourceSide.PHONE)],
            local_files=[_file("same.bin", 42, 200, SourceSide.LOCAL)],
        )
        app._render_plan()
        assert "group_identical" not in list(app.tree.get_children(""))

        app.show_identical_var.set(True)
        app._on_show_identical_toggle()
        top_level = list(app.tree.get_children(""))
        assert "group_identical" in top_level
        same_children = list(app.tree.get_children("group_identical"))
        assert "same.bin" in same_children
        assert "__group_identical_band" in same_children
    finally:
        root.destroy()


def test_arrow_button_flips_direction_and_bolds_active() -> None:
    """Flipping the direction updates the sync_direction state and bolds
    the newly active button (style="Active.TButton")."""
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)
        assert app.sync_direction == "right_to_left"
        # Default: right-to-left button is active.
        assert app.right_to_left_button.cget("style") == "Active.TButton"
        assert app.left_to_right_button.cget("style") == "TButton"

        app._flip_to_left_to_right()
        assert app.sync_direction == "left_to_right"
        assert app.left_to_right_button.cget("style") == "Active.TButton"
        assert app.right_to_left_button.cget("style") == "TButton"
    finally:
        root.destroy()


def test_conflict_row_double_click_opens_popup(monkeypatch) -> None:
    """Double-clicking a conflict row routes to _open_conflict_popup with
    the row's _RowView. The popup is replaced by a fake that records the
    relative_path and applies USE_LOCAL."""
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)
        app.plan = build_sync_plan(
            phone_files=[_file("a.txt", 1, 10, SourceSide.PHONE)],
            local_files=[_file("a.txt", 3, 30, SourceSide.LOCAL)],
        )
        app._render_plan()

        captured: dict[str, str] = {}

        def fake_popup(view_or_conflict):
            # New design passes the _RowView directly.
            captured["rel"] = view_or_conflict.relative_path
            app.conflict_choices[view_or_conflict.relative_path] = ConflictAction.USE_LOCAL
            app._render_plan()

        monkeypatch.setattr(app, "_open_conflict_popup", fake_popup)

        # Synthesize a double-click event on the conflict row.
        class _FakeEvent:
            y = 0

        # The click must identify the iid, which depends on bbox; we
        # exercise the data-row branch by directly calling the handler
        # with an event whose y lands on the row. Simpler: directly call
        # _on_tree_double_click with a row whose identify_row would
        # return the conflict iid. Use the public test seam instead:
        # verify the popup is called when we route through identify_row
        # for an existing iid. We monkey-patch identify_row to lie.
        monkeypatch.setattr(
            app.tree, "identify_row", lambda _y: "a.txt"
        )
        app._on_tree_double_click(_FakeEvent())

        assert captured == {"rel": "a.txt"}
        assert app.conflict_choices["a.txt"] is ConflictAction.USE_LOCAL
        # Resolved conflict renders green, not red.
        assert app.tree.item("a.txt", "tags") == ("row_conflict_resolved",)
    finally:
        root.destroy()


def test_operations_reflect_phone_only_and_local_only_rows() -> None:
    plan = build_sync_plan(
        phone_files=[_file("phone_only.txt", side=SourceSide.PHONE)],
        local_files=[_file("local_only.txt", side=SourceSide.LOCAL)],
    )
    rows = _build_row_views(plan)
    ops = build_operations_from_view(rows, conflict_choices={})
    # Two operations, one per status, in the right direction.
    by_path = {op.relative_path: op for op in ops}
    assert by_path["phone_only.txt"].source_side is SourceSide.PHONE
    assert by_path["phone_only.txt"].destination_side is SourceSide.LOCAL
    assert by_path["local_only.txt"].source_side is SourceSide.LOCAL
    assert by_path["local_only.txt"].destination_side is SourceSide.PHONE
