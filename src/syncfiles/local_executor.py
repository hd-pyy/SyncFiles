from __future__ import annotations

import time
from pathlib import Path

from syncfiles.domain import CopyOperation, SourceSide
from syncfiles.executor import CancellationCheck, OperationCallback, OperationCancelled
from syncfiles.local_fs import copy_local_file


class LocalSyncExecutor:
    def __init__(self, left_root: Path, right_root: Path) -> None:
        self.left_root = left_root
        self.right_root = right_root

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
                copy_local_file(
                    self.left_root / Path(operation.relative_path),
                    self.right_root / Path(destination_relative),
                )
                completed.append(f"Copied {operation.relative_path} left to right")
            elif operation.source_side is SourceSide.PHONE and operation.destination_side is SourceSide.LOCAL:
                copy_local_file(
                    self.right_root / Path(operation.relative_path),
                    self.left_root / Path(destination_relative),
                )
                completed.append(f"Copied {operation.relative_path} right to left")
            if on_operation_complete is not None:
                on_operation_complete(operation, time.perf_counter() - started)
        return completed
