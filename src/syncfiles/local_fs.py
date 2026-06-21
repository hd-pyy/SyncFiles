from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from syncfiles.domain import FileRecord, OperationCancelled, SourceSide


def scan_local_folder(
    root: Path,
    is_cancelled: Callable[[], bool] | None = None,
) -> list[FileRecord]:
    records: list[FileRecord] = []
    # rglob() returns one big sorted list; check between items so a large
    # tree can still be interrupted in roughly O(per-entry) time.
    for path in sorted(root.rglob("*")):
        if is_cancelled is not None and is_cancelled():
            raise OperationCancelled
        if not path.is_file():
            continue
        stat = path.stat()
        records.append(
            FileRecord(
                relative_path=path.relative_to(root).as_posix(),
                size=stat.st_size,
                modified_time=int(stat.st_mtime),
                side=SourceSide.LOCAL,
            )
        )
    return records


def ensure_parent_directory(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)


def copy_local_file(source: Path, destination: Path) -> None:
    ensure_parent_directory(destination)
    shutil.copy2(source, destination)
