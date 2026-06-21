from __future__ import annotations

from pathlib import Path

from syncfiles.domain import CopyOperation, SourceSide
from syncfiles.local_executor import LocalSyncExecutor


def test_executes_left_to_right_operation(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    (left / "docs").mkdir(parents=True)
    (left / "docs" / "a.txt").write_text("left", encoding="utf-8")
    executor = LocalSyncExecutor(left_root=left, right_root=right)

    completed = executor.execute_operations(
        [
            CopyOperation(
                relative_path="docs/a.txt",
                source_side=SourceSide.LOCAL,
                destination_side=SourceSide.PHONE,
            )
        ]
    )

    assert (right / "docs" / "a.txt").read_text(encoding="utf-8") == "left"
    assert completed == ["Copied docs/a.txt left to right"]


def test_executes_right_to_left_operation(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    (right / "photos").mkdir(parents=True)
    (right / "photos" / "b.jpg").write_bytes(b"right")
    executor = LocalSyncExecutor(left_root=left, right_root=right)

    completed = executor.execute_operations(
        [
            CopyOperation(
                relative_path="photos/b.jpg",
                source_side=SourceSide.PHONE,
                destination_side=SourceSide.LOCAL,
            )
        ]
    )

    assert (left / "photos" / "b.jpg").read_bytes() == b"right"
    assert completed == ["Copied photos/b.jpg right to left"]


def test_local_executor_uses_destination_relative_path_and_callback(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    right.mkdir(parents=True)
    (right / "notes.txt").write_text("right", encoding="utf-8")
    executor = LocalSyncExecutor(left_root=left, right_root=right)
    captured: list[tuple[str, float]] = []

    executor.execute_operations(
        [
            CopyOperation(
                relative_path="notes.txt",
                source_side=SourceSide.PHONE,
                destination_side=SourceSide.LOCAL,
                destination_relative_path="notes.txt.sync-conflict-right",
            )
        ],
        on_operation_complete=lambda operation, elapsed: captured.append(
            (operation.relative_path, elapsed)
        ),
    )

    assert (left / "notes.txt.sync-conflict-right").read_text(encoding="utf-8") == "right"
    assert captured[0][0] == "notes.txt"
    assert captured[0][1] >= 0.0
