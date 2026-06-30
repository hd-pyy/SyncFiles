from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class OperationCancelled(Exception):
    """Raised when a sync or scan run is aborted by the user.

    Defined here in the lowest layer so both ``local_fs`` and the
    executors can raise it without a circular import.
    """


class AdbError(Exception):
    """Raised when the adb binary itself is missing or not runnable.

    Distinct from a non-zero return code (which means adb ran and
    reported an error). Use ``DeviceState.ADB_MISSING`` for the
    pre-flight check, and ``AdbError`` for the mid-run case where adb
    vanished (e.g. antivirus quarantined the bundled binary between
    app start and a sync run).

    When constructed from a non-zero adb invocation, ``detail`` carries
    adb's own stderr/stdout so the GUI can show the real reason instead
    of a generic "returned non-zero status" message.
    """

    def __init__(self, message: str, detail: str | None = None) -> None:
        super().__init__(message)
        self.detail = (detail or "").strip() or None

    def __str__(self) -> str:
        if self.detail:
            return f"{self.args[0]}\n\n{self.detail}"
        return self.args[0]


class SourceSide(StrEnum):
    PHONE = "phone"
    LOCAL = "local"


class ConflictAction(StrEnum):
    USE_PHONE = "use_phone"
    USE_LOCAL = "use_local"
    KEEP_BOTH = "keep_both"
    SKIP = "skip"


@dataclass(frozen=True, slots=True)
class FileRecord:
    relative_path: str
    size: int
    modified_time: int
    side: SourceSide


@dataclass(frozen=True, slots=True)
class Conflict:
    relative_path: str
    phone: FileRecord
    local: FileRecord


@dataclass(frozen=True, slots=True)
class CopyOperation:
    relative_path: str
    source_side: SourceSide
    destination_side: SourceSide
    destination_relative_path: str | None = None

    @property
    def final_destination_relative_path(self) -> str:
        return self.destination_relative_path or self.relative_path


@dataclass(frozen=True, slots=True)
class ConflictDecision:
    relative_path: str
    action: ConflictAction


@dataclass(slots=True)
class SyncPlan:
    phone_to_local: list[FileRecord] = field(default_factory=list)
    local_to_phone: list[FileRecord] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    identical: list[FileRecord] = field(default_factory=list)


def build_sync_plan(phone_files: list[FileRecord], local_files: list[FileRecord]) -> SyncPlan:
    phone_by_path = {file.relative_path: file for file in phone_files}
    local_by_path = {file.relative_path: file for file in local_files}
    plan = SyncPlan()

    for relative_path in sorted(phone_by_path.keys() | local_by_path.keys()):
        phone = phone_by_path.get(relative_path)
        local = local_by_path.get(relative_path)
        if phone is None and local is not None:
            plan.local_to_phone.append(local)
        elif local is None and phone is not None:
            plan.phone_to_local.append(phone)
        elif phone is not None and local is not None and _is_conflict(phone, local):
            plan.conflicts.append(Conflict(relative_path=relative_path, phone=phone, local=local))
        elif phone is not None and local is not None:
            # Same path on both sides and no diff — keep one copy so the UI
            # can show it (hidden by default, surfaced via a toggle).
            plan.identical.append(phone)

    return plan


def resolve_conflicts(
    conflicts: list[Conflict],
    decisions: dict[str, ConflictDecision],
) -> list[CopyOperation]:
    operations: list[CopyOperation] = []
    for conflict in conflicts:
        decision = decisions.get(conflict.relative_path)
        if decision is None or decision.action is ConflictAction.SKIP:
            continue
        if decision.action is ConflictAction.USE_PHONE:
            operations.append(
                CopyOperation(
                    relative_path=conflict.relative_path,
                    source_side=SourceSide.PHONE,
                    destination_side=SourceSide.LOCAL,
                )
            )
        elif decision.action is ConflictAction.USE_LOCAL:
            operations.append(
                CopyOperation(
                    relative_path=conflict.relative_path,
                    source_side=SourceSide.LOCAL,
                    destination_side=SourceSide.PHONE,
                )
            )
        elif decision.action is ConflictAction.KEEP_BOTH:
            operations.extend(
                [
                    CopyOperation(
                        relative_path=conflict.relative_path,
                        source_side=SourceSide.PHONE,
                        destination_side=SourceSide.LOCAL,
                        destination_relative_path=f"{conflict.relative_path}.sync-conflict-phone",
                    ),
                    CopyOperation(
                        relative_path=conflict.relative_path,
                        source_side=SourceSide.LOCAL,
                        destination_side=SourceSide.PHONE,
                        destination_relative_path=f"{conflict.relative_path}.sync-conflict-local",
                    ),
                ]
            )
    return operations


def _is_conflict(phone: FileRecord, local: FileRecord) -> bool:
    return phone.size != local.size or phone.modified_time != local.modified_time
