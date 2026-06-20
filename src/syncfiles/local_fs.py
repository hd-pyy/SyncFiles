from __future__ import annotations

from pathlib import Path

from syncfiles.domain import FileRecord, SourceSide


def scan_local_folder(root: Path) -> list[FileRecord]:
    records: list[FileRecord] = []
    for path in sorted(root.rglob("*")):
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
