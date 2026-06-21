from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Protocol

from syncfiles.domain import CopyOperation, OperationCancelled, SourceSide
from syncfiles.local_fs import ensure_parent_directory


class PhoneTransfer(Protocol):
    def push(self, local_path: str, phone_path: str) -> None:
        ...

    def pull(self, phone_path: str, local_path: str) -> None:
        ...


OperationCallback = Callable[[CopyOperation, float], None]
CancellationCheck = Callable[[], bool]


# Re-export for callers that import the exception from here. Defined in
# ``domain`` so ``local_fs`` can raise it without a circular import.
__all__ = ["CancellationCheck", "OperationCallback", "OperationCancelled", "PhoneTransfer", "SyncExecutor"]


class SyncExecutor:
    def __init__(self, adb: PhoneTransfer, local_root: Path, phone_root: str) -> None:
        self.adb = adb
        self.local_root = local_root
        self.phone_root = phone_root.rstrip("/")

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
            destination_relative = operation.final_destination_relative_path
            started = time.perf_counter()
            if operation.source_side is SourceSide.LOCAL and operation.destination_side is SourceSide.PHONE:
                local_path = self.local_root / Path(operation.relative_path)
                phone_path = self._phone_path(destination_relative)
                self.adb.push(str(local_path), phone_path)
                completed.append(f"Pushed {operation.relative_path}")
            elif operation.source_side is SourceSide.PHONE and operation.destination_side is SourceSide.LOCAL:
                phone_path = self._phone_path(operation.relative_path)
                local_path = self.local_root / Path(destination_relative)
                ensure_parent_directory(local_path)
                self.adb.pull(phone_path, str(local_path))
                completed.append(f"Pulled {operation.relative_path}")
            if on_operation_complete is not None:
                on_operation_complete(operation, time.perf_counter() - started)
        return completed

    def _phone_path(self, relative_path: str) -> str:
        normalized = relative_path.replace("\\", "/")
        return f"{self.phone_root}/{normalized}"
