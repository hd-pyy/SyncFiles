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
