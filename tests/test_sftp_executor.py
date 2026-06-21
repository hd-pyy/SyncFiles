from __future__ import annotations

from pathlib import Path

import pytest

from syncfiles.domain import CopyOperation, OperationCancelled, SourceSide
from syncfiles.sftp_executor import SftpSyncExecutor


class FakeSftpTransfer:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, str]] = []
        self.downloads: list[tuple[str, str]] = []

    def upload_file(self, local_path: Path, remote_path: str) -> None:
        self.uploads.append((str(local_path), remote_path))

    def download_file(self, remote_path: str, local_path: Path) -> None:
        self.downloads.append((remote_path, str(local_path)))


def test_executes_local_to_sftp_operation(tmp_path: Path) -> None:
    transfer = FakeSftpTransfer()
    executor = SftpSyncExecutor(sftp=transfer, local_root=tmp_path / "local", remote_root="/remote")

    completed = executor.execute_operations(
        [
            CopyOperation(
                relative_path="docs/a.txt",
                source_side=SourceSide.LOCAL,
                destination_side=SourceSide.PHONE,
            )
        ]
    )

    assert transfer.uploads == [(str(tmp_path / "local" / "docs" / "a.txt"), "/remote/docs/a.txt")]
    assert completed == ["Uploaded docs/a.txt to SFTP"]


def test_executes_sftp_to_local_operation(tmp_path: Path) -> None:
    transfer = FakeSftpTransfer()
    executor = SftpSyncExecutor(sftp=transfer, local_root=tmp_path / "local", remote_root="/remote")

    completed = executor.execute_operations(
        [
            CopyOperation(
                relative_path="photos/b.jpg",
                source_side=SourceSide.PHONE,
                destination_side=SourceSide.LOCAL,
            )
        ]
    )

    assert transfer.downloads == [("/remote/photos/b.jpg", str(tmp_path / "local" / "photos" / "b.jpg"))]
    assert completed == ["Downloaded photos/b.jpg from SFTP"]


def test_sftp_executor_uses_destination_relative_path_and_callback(tmp_path: Path) -> None:
    transfer = FakeSftpTransfer()
    executor = SftpSyncExecutor(sftp=transfer, local_root=tmp_path / "local", remote_root="/remote")
    captured: list[tuple[str, float]] = []

    executor.execute_operations(
        [
            CopyOperation(
                relative_path="notes.txt",
                source_side=SourceSide.PHONE,
                destination_side=SourceSide.LOCAL,
                destination_relative_path="notes.txt.sync-conflict-sftp",
            )
        ],
        on_operation_complete=lambda operation, elapsed: captured.append((operation.relative_path, elapsed)),
    )

    assert transfer.downloads == [("/remote/notes.txt", str(tmp_path / "local" / "notes.txt.sync-conflict-sftp"))]
    assert captured[0][0] == "notes.txt"
    assert captured[0][1] >= 0.0


def test_sftp_executor_honors_cancellation(tmp_path: Path) -> None:
    transfer = FakeSftpTransfer()
    executor = SftpSyncExecutor(sftp=transfer, local_root=tmp_path / "local", remote_root="/remote")

    with pytest.raises(OperationCancelled):
        executor.execute_operations(
            [
                CopyOperation(
                    relative_path="docs/a.txt",
                    source_side=SourceSide.LOCAL,
                    destination_side=SourceSide.PHONE,
                )
            ],
            is_cancelled=lambda: True,
        )

    assert transfer.uploads == []
