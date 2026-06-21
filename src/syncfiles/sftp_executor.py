from __future__ import annotations

import time
from pathlib import Path
from typing import Protocol

from syncfiles.domain import CopyOperation, SourceSide
from syncfiles.executor import CancellationCheck, OperationCallback, OperationCancelled
from syncfiles.sftp import join_remote_path


class SftpTransfer(Protocol):
    def upload_file(self, local_path: Path, remote_path: str) -> None:
        ...

    def download_file(self, remote_path: str, local_path: Path) -> None:
        ...


class SftpSyncExecutor:
    def __init__(self, sftp: SftpTransfer, local_root: Path, remote_root: str) -> None:
        self.sftp = sftp
        self.local_root = local_root
        self.remote_root = remote_root

    def execute_operations(
        self,
        operations: list[CopyOperation],
        on_operation_complete: OperationCallback | None = None,
        is_cancelled: CancellationCheck | None = None,
    ) -> list[str]:
        completed: list[str] = []
        for operation in operations:
            if is_cancelled is not None and is_cancelled():
                raise OperationCancelled
            started = time.perf_counter()
            destination_relative = operation.final_destination_relative_path
            if operation.source_side is SourceSide.LOCAL and operation.destination_side is SourceSide.PHONE:
                self.sftp.upload_file(
                    self.local_root / Path(operation.relative_path),
                    join_remote_path(self.remote_root, destination_relative),
                )
                completed.append(f"Uploaded {operation.relative_path} to SFTP")
            elif operation.source_side is SourceSide.PHONE and operation.destination_side is SourceSide.LOCAL:
                self.sftp.download_file(
                    join_remote_path(self.remote_root, operation.relative_path),
                    self.local_root / Path(destination_relative),
                )
                completed.append(f"Downloaded {operation.relative_path} from SFTP")
            if on_operation_complete is not None:
                on_operation_complete(operation, time.perf_counter() - started)
        return completed
