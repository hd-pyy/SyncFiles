from __future__ import annotations

from pathlib import Path

from syncfiles.domain import CopyOperation, SourceSide
from syncfiles.executor import SyncExecutor


class FakeAdb:
    def __init__(self) -> None:
        self.pushes: list[tuple[str, str]] = []
        self.pulls: list[tuple[str, str]] = []

    def push(self, local_path: str, phone_path: str) -> None:
        self.pushes.append((local_path, phone_path))

    def pull(self, phone_path: str, local_path: str) -> None:
        self.pulls.append((phone_path, local_path))


def test_executor_invokes_callback_per_operation_in_order(tmp_path: Path) -> None:
    adb = FakeAdb()
    executor = SyncExecutor(adb=adb, local_root=tmp_path / "local", phone_root="/sdcard/Test")

    captured: list[tuple[str, SourceSide, SourceSide, float]] = []

    def hook(op: CopyOperation, elapsed: float) -> None:
        captured.append((op.relative_path, op.source_side, op.destination_side, elapsed))

    operations = [
        CopyOperation(
            relative_path="docs/a.txt",
            source_side=SourceSide.LOCAL,
            destination_side=SourceSide.PHONE,
        ),
        CopyOperation(
            relative_path="photos/a.jpg",
            source_side=SourceSide.PHONE,
            destination_side=SourceSide.LOCAL,
        ),
        CopyOperation(
            relative_path="notes.txt",
            source_side=SourceSide.PHONE,
            destination_side=SourceSide.LOCAL,
            destination_relative_path="notes.txt.sync-conflict-phone",
        ),
    ]

    executor.execute_operations(operations, on_operation_complete=hook)

    assert [item[0] for item in captured] == ["docs/a.txt", "photos/a.jpg", "notes.txt"]
    assert [item[1] for item in captured] == [
        SourceSide.LOCAL,
        SourceSide.PHONE,
        SourceSide.PHONE,
    ]
    assert [item[2] for item in captured] == [
        SourceSide.PHONE,
        SourceSide.LOCAL,
        SourceSide.LOCAL,
    ]
    # Elapsed is a non-negative float perf_counter delta.
    for _, _, _, elapsed in captured:
        assert elapsed >= 0.0


def test_executor_returns_completed_list_unchanged_when_callback_present(tmp_path: Path) -> None:
    adb = FakeAdb()
    executor = SyncExecutor(adb=adb, local_root=tmp_path / "local", phone_root="/sdcard/Test")

    completed = executor.execute_operations(
        [
            CopyOperation(
                relative_path="a.txt",
                source_side=SourceSide.PHONE,
                destination_side=SourceSide.LOCAL,
            )
        ],
        on_operation_complete=lambda op, elapsed: None,
    )

    assert completed == ["Pulled a.txt"]
