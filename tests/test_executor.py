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


def test_executes_local_to_phone_operation(tmp_path: Path) -> None:
    adb = FakeAdb()
    executor = SyncExecutor(adb=adb, local_root=tmp_path / "local", phone_root="/sdcard/Test")

    executor.execute_operations(
        [
            CopyOperation(
                relative_path="docs/a.txt",
                source_side=SourceSide.LOCAL,
                destination_side=SourceSide.PHONE,
            )
        ]
    )

    assert adb.pushes == [(str(tmp_path / "local" / "docs" / "a.txt"), "/sdcard/Test/docs/a.txt")]


def test_executes_phone_to_local_operation_and_creates_parent(tmp_path: Path) -> None:
    adb = FakeAdb()
    executor = SyncExecutor(adb=adb, local_root=tmp_path / "local", phone_root="/sdcard/Test")

    executor.execute_operations(
        [
            CopyOperation(
                relative_path="photos/a.jpg",
                source_side=SourceSide.PHONE,
                destination_side=SourceSide.LOCAL,
            )
        ]
    )

    expected_local = tmp_path / "local" / "photos" / "a.jpg"
    assert adb.pulls == [("/sdcard/Test/photos/a.jpg", str(expected_local))]
    assert expected_local.parent.is_dir()


def test_uses_destination_relative_path_for_keep_both_copy(tmp_path: Path) -> None:
    adb = FakeAdb()
    executor = SyncExecutor(adb=adb, local_root=tmp_path / "local", phone_root="/sdcard/Test")

    executor.execute_operations(
        [
            CopyOperation(
                relative_path="notes.txt",
                source_side=SourceSide.PHONE,
                destination_side=SourceSide.LOCAL,
                destination_relative_path="notes.txt.sync-conflict-phone",
            )
        ]
    )

    assert adb.pulls == [
        ("/sdcard/Test/notes.txt", str(tmp_path / "local" / "notes.txt.sync-conflict-phone"))
    ]
