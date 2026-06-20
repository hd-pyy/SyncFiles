from syncfiles.app import build_operations_from_plan
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
