from syncfiles.domain import (
    ConflictAction,
    ConflictDecision,
    FileRecord,
    SourceSide,
    build_sync_plan,
    resolve_conflicts,
)


def record(
    path: str,
    size: int = 10,
    modified: int = 100,
    side: SourceSide = SourceSide.PHONE,
) -> FileRecord:
    return FileRecord(relative_path=path, size=size, modified_time=modified, side=side)


def test_plans_files_missing_on_hard_drive_as_phone_to_local() -> None:
    plan = build_sync_plan(
        phone_files=[record("photos/a.jpg", side=SourceSide.PHONE)],
        local_files=[],
    )

    assert [item.relative_path for item in plan.phone_to_local] == ["photos/a.jpg"]
    assert plan.local_to_phone == []
    assert plan.conflicts == []


def test_plans_files_missing_on_phone_as_local_to_phone() -> None:
    plan = build_sync_plan(
        phone_files=[],
        local_files=[record("docs/readme.txt", side=SourceSide.LOCAL)],
    )

    assert [item.relative_path for item in plan.local_to_phone] == ["docs/readme.txt"]
    assert plan.phone_to_local == []
    assert plan.conflicts == []


def test_skips_identical_files_present_on_both_sides() -> None:
    plan = build_sync_plan(
        phone_files=[record("same.bin", size=42, modified=200, side=SourceSide.PHONE)],
        local_files=[record("same.bin", size=42, modified=200, side=SourceSide.LOCAL)],
    )

    assert plan.phone_to_local == []
    assert plan.local_to_phone == []
    assert plan.conflicts == []


def test_marks_same_relative_path_with_different_metadata_as_conflict() -> None:
    plan = build_sync_plan(
        phone_files=[record("notes.txt", size=10, modified=100, side=SourceSide.PHONE)],
        local_files=[record("notes.txt", size=20, modified=100, side=SourceSide.LOCAL)],
    )

    assert len(plan.conflicts) == 1
    assert plan.conflicts[0].relative_path == "notes.txt"
    assert plan.conflicts[0].phone.size == 10
    assert plan.conflicts[0].local.size == 20


def test_resolves_conflict_decisions_to_copy_and_skip_operations() -> None:
    plan = build_sync_plan(
        phone_files=[record("notes.txt", size=10, modified=100, side=SourceSide.PHONE)],
        local_files=[record("notes.txt", size=20, modified=100, side=SourceSide.LOCAL)],
    )

    operations = resolve_conflicts(
        plan.conflicts,
        {
            "notes.txt": ConflictDecision(
                relative_path="notes.txt",
                action=ConflictAction.USE_PHONE,
            )
        },
    )

    assert len(operations) == 1
    assert operations[0].source_side is SourceSide.PHONE
    assert operations[0].destination_side is SourceSide.LOCAL
    assert operations[0].relative_path == "notes.txt"


def test_keep_both_conflict_decision_creates_conflict_copy_operation() -> None:
    plan = build_sync_plan(
        phone_files=[record("notes.txt", size=10, modified=100, side=SourceSide.PHONE)],
        local_files=[record("notes.txt", size=20, modified=100, side=SourceSide.LOCAL)],
    )

    operations = resolve_conflicts(
        plan.conflicts,
        {
            "notes.txt": ConflictDecision(
                relative_path="notes.txt",
                action=ConflictAction.KEEP_BOTH,
            )
        },
    )

    assert len(operations) == 2
    assert {operation.destination_relative_path for operation in operations} == {
        "notes.txt.sync-conflict-phone",
        "notes.txt.sync-conflict-local",
    }
