# SyncFiles Desktop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Windows-first Tkinter desktop app that compares an Android ADB folder and a hard drive folder, previews missing files and conflicts, and performs user-confirmed bidirectional fill-in synchronization.

**Architecture:** The app separates pure planning logic from side effects. `syncfiles.domain` computes sync plans from file records, `syncfiles.local_fs` scans and writes local files, `syncfiles.adb` wraps ADB device, browse, scan, push, and pull operations, `syncfiles.executor` executes approved copy operations, and `syncfiles.app` contains the Tkinter UI.

**Tech Stack:** Python 3.11+, standard library Tkinter, pytest, subprocess-based ADB integration.

---

## File Structure

- Create `pyproject.toml`: package metadata and pytest config.
- Create `README.md`: setup and run instructions.
- Create `src/syncfiles/__init__.py`: package marker.
- Create `src/syncfiles/__main__.py`: `python -m syncfiles` entry point.
- Create `src/syncfiles/domain.py`: file records, sync plan generation, conflict decisions.
- Create `src/syncfiles/local_fs.py`: local folder scanning and local destination path handling.
- Create `src/syncfiles/adb.py`: ADB command runner, device status, phone browsing, phone scanning, push/pull.
- Create `src/syncfiles/executor.py`: execute planned file copies and conflict actions.
- Create `src/syncfiles/app.py`: Tkinter UI.
- Create `tests/test_domain.py`: pure planner tests.
- Create `tests/test_local_fs.py`: local scanner tests.
- Create `tests/test_adb.py`: fake-command ADB tests.
- Create `tests/test_executor.py`: fake adapter executor tests.

### Task 1: Project Skeleton

**Files:**
- Create: `pyproject.toml`
- Create: `README.md`
- Create: `src/syncfiles/__init__.py`
- Create: `src/syncfiles/__main__.py`
- Create: `src/syncfiles/app.py`

- [ ] **Step 1: Create a smoke test target by adding package files**

Create `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "syncfiles"
version = "0.1.0"
description = "Desktop ADB folder synchronization helper"
requires-python = ">=3.11"
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=8"]

[project.scripts]
syncfiles = "syncfiles.app:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

Create `src/syncfiles/__init__.py`:

```python
"""SyncFiles desktop folder synchronization package."""

__version__ = "0.1.0"
```

Create `src/syncfiles/__main__.py`:

```python
from syncfiles.app import main


if __name__ == "__main__":
    main()
```

Create `src/syncfiles/app.py`:

```python
def main() -> None:
    print("SyncFiles desktop app is not wired yet.")
```

Create `README.md`:

```markdown
# SyncFiles

SyncFiles is a Windows-first desktop helper for synchronizing one Android ADB folder with one local or external hard drive folder.

## Development

```powershell
python -m pip install -e .[dev]
pytest
python -m syncfiles
```
```

- [ ] **Step 2: Verify the smoke target runs**

Run: `python -m syncfiles`

Expected: exit code `0` and output `SyncFiles desktop app is not wired yet.`

- [ ] **Step 3: Commit**

```powershell
git add pyproject.toml README.md src/syncfiles/__init__.py src/syncfiles/__main__.py src/syncfiles/app.py
git commit -m "chore: scaffold SyncFiles desktop project"
```

### Task 2: Pure Sync Planner

**Files:**
- Create: `src/syncfiles/domain.py`
- Test: `tests/test_domain.py`

- [ ] **Step 1: Write failing planner tests**

Create `tests/test_domain.py`:

```python
from syncfiles.domain import (
    ConflictAction,
    ConflictDecision,
    FileRecord,
    SourceSide,
    build_sync_plan,
    resolve_conflicts,
)


def record(path: str, size: int = 10, modified: int = 100, side: SourceSide = SourceSide.PHONE) -> FileRecord:
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_domain.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'syncfiles.domain'`.

- [ ] **Step 3: Implement planner**

Create `src/syncfiles/domain.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_domain.py -v`

Expected: `6 passed`.

- [ ] **Step 5: Commit**

```powershell
git add src/syncfiles/domain.py tests/test_domain.py
git commit -m "feat: add sync planning domain"
```

### Task 3: Local Hard Drive Scanner

**Files:**
- Create: `src/syncfiles/local_fs.py`
- Test: `tests/test_local_fs.py`

- [ ] **Step 1: Write failing local scanner tests**

Create `tests/test_local_fs.py`:

```python
from pathlib import Path

from syncfiles.domain import SourceSide
from syncfiles.local_fs import ensure_parent_directory, scan_local_folder


def test_scans_local_folder_with_relative_paths(tmp_path: Path) -> None:
    root = tmp_path / "root"
    nested = root / "photos"
    nested.mkdir(parents=True)
    file_path = nested / "a.jpg"
    file_path.write_bytes(b"abc")

    records = scan_local_folder(root)

    assert len(records) == 1
    assert records[0].relative_path == "photos/a.jpg"
    assert records[0].size == 3
    assert records[0].side is SourceSide.LOCAL
    assert records[0].modified_time > 0


def test_scan_local_folder_ignores_directories(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "empty").mkdir(parents=True)

    assert scan_local_folder(root) == []


def test_ensure_parent_directory_creates_nested_destination(tmp_path: Path) -> None:
    destination = tmp_path / "root" / "nested" / "file.txt"

    ensure_parent_directory(destination)

    assert destination.parent.is_dir()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_local_fs.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'syncfiles.local_fs'`.

- [ ] **Step 3: Implement local scanner**

Create `src/syncfiles/local_fs.py`:

```python
from __future__ import annotations

from pathlib import Path

from syncfiles.domain import FileRecord, SourceSide


def scan_local_folder(root: Path) -> list[FileRecord]:
    records: list[FileRecord] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        stat = path.stat()
        relative_path = path.relative_to(root).as_posix()
        records.append(
            FileRecord(
                relative_path=relative_path,
                size=stat.st_size,
                modified_time=int(stat.st_mtime),
                side=SourceSide.LOCAL,
            )
        )
    return records


def ensure_parent_directory(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_local_fs.py tests/test_domain.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/syncfiles/local_fs.py tests/test_local_fs.py
git commit -m "feat: add local folder scanner"
```

### Task 4: ADB Adapter

**Files:**
- Create: `src/syncfiles/adb.py`
- Test: `tests/test_adb.py`

- [ ] **Step 1: Write failing ADB adapter tests**

Create `tests/test_adb.py`:

```python
import subprocess

from syncfiles.adb import AdbClient, DeviceState
from syncfiles.domain import SourceSide


class FakeRunner:
    def __init__(self, outputs: dict[tuple[str, ...], subprocess.CompletedProcess[str]]) -> None:
        self.outputs = outputs
        self.calls: list[tuple[str, ...]] = []

    def __call__(
        self,
        command: list[str],
        *,
        capture_output: bool = True,
        text: bool = True,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        key = tuple(command)
        self.calls.append(key)
        result = self.outputs[key]
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, command, result.stdout, result.stderr)
        return result


def completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_classifies_one_authorized_device_as_ready() -> None:
    runner = FakeRunner({("adb", "devices"): completed("List of devices attached\nabc123\tdevice\n")})

    status = AdbClient(runner=runner).get_device_status()

    assert status.state is DeviceState.READY
    assert status.serial == "abc123"


def test_classifies_unauthorized_device() -> None:
    runner = FakeRunner({("adb", "devices"): completed("List of devices attached\nabc123\tunauthorized\n")})

    status = AdbClient(runner=runner).get_device_status()

    assert status.state is DeviceState.UNAUTHORIZED


def test_lists_phone_directories() -> None:
    runner = FakeRunner(
        {
            ("adb", "shell", "find", "/sdcard", "-maxdepth", "1", "-mindepth", "1", "-type", "d"): completed(
                "/sdcard/DCIM\n/sdcard/Documents\n"
            )
        }
    )

    directories = AdbClient(runner=runner).list_directories("/sdcard")

    assert directories == ["/sdcard/DCIM", "/sdcard/Documents"]


def test_scans_phone_folder_to_file_records() -> None:
    runner = FakeRunner(
        {
            ("adb", "shell", "find", "/sdcard/Test", "-type", "f", "-printf", "%P\t%s\t%T@\n"): completed(
                "a.txt\t3\t1710000000.0\nnested/b.jpg\t5\t1710000001.0\n"
            )
        }
    )

    records = AdbClient(runner=runner).scan_phone_folder("/sdcard/Test")

    assert [record.relative_path for record in records] == ["a.txt", "nested/b.jpg"]
    assert records[0].size == 3
    assert records[0].modified_time == 1710000000
    assert records[0].side is SourceSide.PHONE


def test_push_and_pull_call_adb_with_expected_paths() -> None:
    runner = FakeRunner(
        {
            ("adb", "push", "C:/local/a.txt", "/sdcard/Test/a.txt"): completed(),
            ("adb", "pull", "/sdcard/Test/b.txt", "D:/Backup/b.txt"): completed(),
        }
    )
    client = AdbClient(runner=runner)

    client.push("C:/local/a.txt", "/sdcard/Test/a.txt")
    client.pull("/sdcard/Test/b.txt", "D:/Backup/b.txt")

    assert ("adb", "push", "C:/local/a.txt", "/sdcard/Test/a.txt") in runner.calls
    assert ("adb", "pull", "/sdcard/Test/b.txt", "D:/Backup/b.txt") in runner.calls
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_adb.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'syncfiles.adb'`.

- [ ] **Step 3: Implement ADB adapter**

Create `src/syncfiles/adb.py`:

```python
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from typing import Callable

from syncfiles.domain import FileRecord, SourceSide

Runner = Callable[..., subprocess.CompletedProcess[str]]


class DeviceState(StrEnum):
    ADB_MISSING = "adb_missing"
    NO_DEVICE = "no_device"
    UNAUTHORIZED = "unauthorized"
    MULTIPLE_DEVICES = "multiple_devices"
    READY = "ready"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class DeviceStatus:
    state: DeviceState
    message: str
    serial: str | None = None


class AdbClient:
    def __init__(self, adb_path: str = "adb", runner: Runner = subprocess.run) -> None:
        self.adb_path = adb_path
        self.runner = runner

    def get_device_status(self) -> DeviceStatus:
        if self.adb_path == "adb" and shutil.which("adb") is None and self.runner is subprocess.run:
            return DeviceStatus(DeviceState.ADB_MISSING, "ADB is not installed or not on PATH.")
        try:
            result = self.runner([self.adb_path, "devices"], capture_output=True, text=True, check=False)
        except FileNotFoundError:
            return DeviceStatus(DeviceState.ADB_MISSING, "ADB is not installed or not on PATH.")

        if result.returncode != 0:
            return DeviceStatus(DeviceState.ERROR, result.stderr.strip() or "ADB returned an error.")

        devices = _parse_devices(result.stdout)
        if not devices:
            return DeviceStatus(DeviceState.NO_DEVICE, "No Android device is connected.")
        if len(devices) > 1:
            return DeviceStatus(DeviceState.MULTIPLE_DEVICES, "Connect exactly one Android device.")
        serial, state = devices[0]
        if state == "device":
            return DeviceStatus(DeviceState.READY, "One authorized Android device is ready.", serial=serial)
        if state == "unauthorized":
            return DeviceStatus(DeviceState.UNAUTHORIZED, "Authorize USB debugging on the phone.", serial=serial)
        return DeviceStatus(DeviceState.ERROR, f"Unsupported device state: {state}", serial=serial)

    def list_directories(self, phone_path: str) -> list[str]:
        result = self.runner(
            [self.adb_path, "shell", "find", phone_path, "-maxdepth", "1", "-mindepth", "1", "-type", "d"],
            capture_output=True,
            text=True,
            check=True,
        )
        return sorted(line.strip() for line in result.stdout.splitlines() if line.strip())

    def scan_phone_folder(self, phone_root: str) -> list[FileRecord]:
        result = self.runner(
            [self.adb_path, "shell", "find", phone_root, "-type", "f", "-printf", "%P\t%s\t%T@\n"],
            capture_output=True,
            text=True,
            check=True,
        )
        records: list[FileRecord] = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            relative_path, size, modified = line.split("\t", 2)
            records.append(
                FileRecord(
                    relative_path=relative_path.replace("\\", "/"),
                    size=int(size),
                    modified_time=int(float(modified)),
                    side=SourceSide.PHONE,
                )
            )
        return sorted(records, key=lambda record: record.relative_path)

    def push(self, local_path: str, phone_path: str) -> None:
        self.runner([self.adb_path, "push", local_path, phone_path], capture_output=True, text=True, check=True)

    def pull(self, phone_path: str, local_path: str) -> None:
        self.runner([self.adb_path, "pull", phone_path, local_path], capture_output=True, text=True, check=True)


def _parse_devices(output: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for line in output.splitlines()[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) >= 2:
            rows.append((parts[0], parts[1]))
    return rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_adb.py tests/test_domain.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/syncfiles/adb.py tests/test_adb.py
git commit -m "feat: add adb phone adapter"
```

### Task 5: Sync Executor

**Files:**
- Create: `src/syncfiles/executor.py`
- Test: `tests/test_executor.py`

- [ ] **Step 1: Write failing executor tests**

Create `tests/test_executor.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_executor.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'syncfiles.executor'`.

- [ ] **Step 3: Implement executor**

Create `src/syncfiles/executor.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from syncfiles.domain import CopyOperation, SourceSide
from syncfiles.local_fs import ensure_parent_directory


class PhoneTransfer(Protocol):
    def push(self, local_path: str, phone_path: str) -> None:
        ...

    def pull(self, phone_path: str, local_path: str) -> None:
        ...


class SyncExecutor:
    def __init__(self, adb: PhoneTransfer, local_root: Path, phone_root: str) -> None:
        self.adb = adb
        self.local_root = local_root
        self.phone_root = phone_root.rstrip("/")

    def execute_operations(self, operations: list[CopyOperation]) -> list[str]:
        completed: list[str] = []
        for operation in operations:
            destination_relative = operation.final_destination_relative_path
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
        return completed

    def _phone_path(self, relative_path: str) -> str:
        normalized = relative_path.replace("\\", "/")
        return f"{self.phone_root}/{normalized}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_executor.py tests/test_domain.py tests/test_local_fs.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/syncfiles/executor.py tests/test_executor.py
git commit -m "feat: add sync executor"
```

### Task 6: Desktop UI

**Files:**
- Modify: `src/syncfiles/app.py`
- Test: all existing tests

- [ ] **Step 1: Replace smoke app with Tkinter desktop shell**

Modify `src/syncfiles/app.py`:

```python
from __future__ import annotations

import queue
import threading
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, X, Button, Frame, Label, Listbox, StringVar, Tk, Toplevel, filedialog, messagebox, ttk

from syncfiles.adb import AdbClient, DeviceState
from syncfiles.domain import (
    ConflictAction,
    ConflictDecision,
    CopyOperation,
    SourceSide,
    build_sync_plan,
    resolve_conflicts,
)
from syncfiles.executor import SyncExecutor
from syncfiles.local_fs import scan_local_folder


class SyncFilesApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("SyncFiles")
        self.adb = AdbClient()
        self.local_root = StringVar()
        self.phone_root = StringVar(value="/sdcard")
        self.status = StringVar(value="Device status: unchecked")
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.plan = None
        self.conflict_choices: dict[str, ConflictAction] = {}
        self._build_ui()
        self.root.after(100, self._drain_log_queue)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill=BOTH, expand=True)

        ttk.Label(outer, textvariable=self.status).pack(anchor="w", fill=X)
        ttk.Button(outer, text="Check device", command=self.check_device).pack(anchor="w", pady=(4, 10))

        local_row = ttk.Frame(outer)
        local_row.pack(fill=X, pady=4)
        ttk.Label(local_row, text="Hard drive folder").pack(side=LEFT)
        ttk.Entry(local_row, textvariable=self.local_root).pack(side=LEFT, fill=X, expand=True, padx=8)
        ttk.Button(local_row, text="Choose", command=self.choose_local_folder).pack(side=RIGHT)

        phone_row = ttk.Frame(outer)
        phone_row.pack(fill=X, pady=4)
        ttk.Label(phone_row, text="Phone folder").pack(side=LEFT)
        ttk.Entry(phone_row, textvariable=self.phone_root).pack(side=LEFT, fill=X, expand=True, padx=8)
        ttk.Button(phone_row, text="Browse phone", command=self.open_phone_browser).pack(side=RIGHT)

        actions = ttk.Frame(outer)
        actions.pack(fill=X, pady=8)
        ttk.Button(actions, text="Scan differences", command=self.scan_differences).pack(side=LEFT)
        ttk.Button(actions, text="Start sync", command=self.start_sync).pack(side=LEFT, padx=8)

        notebook = ttk.Notebook(outer)
        notebook.pack(fill=BOTH, expand=True)
        self.phone_to_local_list = Listbox(notebook)
        self.local_to_phone_list = Listbox(notebook)
        self.conflict_list = Listbox(notebook)
        self.conflict_list.bind("<Double-Button-1>", self.choose_conflict_action)
        notebook.add(self.phone_to_local_list, text="Phone -> hard drive")
        notebook.add(self.local_to_phone_list, text="Hard drive -> phone")
        notebook.add(self.conflict_list, text="Conflicts")

        ttk.Label(outer, text="Log").pack(anchor="w", pady=(8, 0))
        self.log_list = Listbox(outer, height=8)
        self.log_list.pack(fill=BOTH, expand=False)

    def check_device(self) -> None:
        status = self.adb.get_device_status()
        self.status.set(f"Device status: {status.message}")

    def choose_local_folder(self) -> None:
        selected = filedialog.askdirectory(title="Choose hard drive folder")
        if selected:
            self.local_root.set(selected)

    def open_phone_browser(self) -> None:
        browser = Toplevel(self.root)
        browser.title("Choose phone folder")
        current = StringVar(value=self.phone_root.get() or "/sdcard")
        label = ttk.Label(browser, textvariable=current)
        label.pack(fill=X, padx=8, pady=8)
        listing = Listbox(browser, width=80, height=20)
        listing.pack(fill=BOTH, expand=True, padx=8, pady=8)

        def load(path: str) -> None:
            current.set(path)
            listing.delete(0, END)
            if path != "/sdcard":
                listing.insert(END, "..")
            try:
                for directory in self.adb.list_directories(path):
                    listing.insert(END, directory)
            except Exception as exc:
                messagebox.showerror("ADB error", str(exc))

        def enter(_event: object | None = None) -> None:
            selection = listing.curselection()
            if not selection:
                return
            value = listing.get(selection[0])
            if value == "..":
                parent = current.get().rstrip("/").rsplit("/", 1)[0] or "/sdcard"
                load(parent if parent.startswith("/sdcard") else "/sdcard")
            else:
                load(value)

        def choose() -> None:
            self.phone_root.set(current.get())
            browser.destroy()

        listing.bind("<Double-Button-1>", enter)
        buttons = ttk.Frame(browser)
        buttons.pack(fill=X, padx=8, pady=8)
        ttk.Button(buttons, text="Open", command=enter).pack(side=LEFT)
        ttk.Button(buttons, text="Choose this folder", command=choose).pack(side=RIGHT)
        load(current.get() or "/sdcard")

    def scan_differences(self) -> None:
        local = self.local_root.get()
        phone = self.phone_root.get()
        if not local or not phone:
            messagebox.showwarning("Missing folders", "Choose both folders before scanning.")
            return
        self._run_background(lambda: self._scan_worker(Path(local), phone))

    def _scan_worker(self, local: Path, phone: str) -> None:
        self._log("Scanning hard drive folder...")
        local_files = scan_local_folder(local)
        self._log("Scanning phone folder...")
        phone_files = self.adb.scan_phone_folder(phone)
        self.plan = build_sync_plan(phone_files=phone_files, local_files=local_files)
        self.conflict_choices = {conflict.relative_path: ConflictAction.SKIP for conflict in self.plan.conflicts}
        self.root.after(0, self._render_plan)

    def _render_plan(self) -> None:
        self.phone_to_local_list.delete(0, END)
        self.local_to_phone_list.delete(0, END)
        self.conflict_list.delete(0, END)
        if self.plan is None:
            return
        for item in self.plan.phone_to_local:
            self.phone_to_local_list.insert(END, item.relative_path)
        for item in self.plan.local_to_phone:
            self.local_to_phone_list.insert(END, item.relative_path)
        for conflict in self.plan.conflicts:
            action = self.conflict_choices.get(conflict.relative_path, ConflictAction.SKIP)
            self.conflict_list.insert(END, f"{conflict.relative_path} [{action.value}]")
        self._log(
            f"Scan complete: {len(self.plan.phone_to_local)} phone-to-hard-drive, "
            f"{len(self.plan.local_to_phone)} hard-drive-to-phone, {len(self.plan.conflicts)} conflicts."
        )

    def choose_conflict_action(self, _event: object | None = None) -> None:
        if self.plan is None:
            return
        selection = self.conflict_list.curselection()
        if not selection:
            return
        conflict = self.plan.conflicts[selection[0]]
        window = Toplevel(self.root)
        window.title("Conflict action")
        ttk.Label(window, text=conflict.relative_path).pack(fill=X, padx=12, pady=8)

        def choose(action: ConflictAction) -> None:
            self.conflict_choices[conflict.relative_path] = action
            window.destroy()
            self._render_plan()

        ttk.Button(window, text="Use phone version", command=lambda: choose(ConflictAction.USE_PHONE)).pack(fill=X, padx=12, pady=4)
        ttk.Button(window, text="Use hard drive version", command=lambda: choose(ConflictAction.USE_LOCAL)).pack(fill=X, padx=12, pady=4)
        ttk.Button(window, text="Keep both", command=lambda: choose(ConflictAction.KEEP_BOTH)).pack(fill=X, padx=12, pady=4)
        ttk.Button(window, text="Skip", command=lambda: choose(ConflictAction.SKIP)).pack(fill=X, padx=12, pady=4)

    def start_sync(self) -> None:
        if self.plan is None:
            messagebox.showwarning("No scan", "Scan differences before syncing.")
            return
        if not messagebox.askyesno("Confirm sync", "Run the listed copy operations now?"):
            return
        self._run_background(lambda: self._sync_worker(Path(self.local_root.get()), self.phone_root.get()))

    def _sync_worker(self, local: Path, phone: str) -> None:
        operations: list[CopyOperation] = []
        operations.extend(
            CopyOperation(item.relative_path, SourceSide.PHONE, SourceSide.LOCAL) for item in self.plan.phone_to_local
        )
        operations.extend(
            CopyOperation(item.relative_path, SourceSide.LOCAL, SourceSide.PHONE) for item in self.plan.local_to_phone
        )
        decisions = {
            path: ConflictDecision(relative_path=path, action=action)
            for path, action in self.conflict_choices.items()
        }
        operations.extend(resolve_conflicts(self.plan.conflicts, decisions))
        executor = SyncExecutor(adb=self.adb, local_root=local, phone_root=phone)
        for line in executor.execute_operations(operations):
            self._log(line)
        self._log(f"Sync complete: {len(operations)} operations attempted.")

    def _run_background(self, target) -> None:
        def wrapped() -> None:
            try:
                target()
            except Exception as exc:
                self._log(f"Error: {exc}")
                self.root.after(0, lambda: messagebox.showerror("SyncFiles error", str(exc)))

        threading.Thread(target=wrapped, daemon=True).start()

    def _log(self, message: str) -> None:
        self.log_queue.put(message)

    def _drain_log_queue(self) -> None:
        while not self.log_queue.empty():
            self.log_list.insert(END, self.log_queue.get())
            self.log_list.yview_moveto(1)
        self.root.after(100, self._drain_log_queue)


def main() -> None:
    root = Tk()
    SyncFilesApp(root)
    root.mainloop()
```

- [ ] **Step 2: Run automated tests**

Run: `pytest -v`

Expected: all tests pass.

- [ ] **Step 3: Run import smoke check**

Run: `python -c "from syncfiles.app import SyncFilesApp; print(SyncFilesApp.__name__)"`

Expected: `SyncFilesApp`.

- [ ] **Step 4: Commit**

```powershell
git add src/syncfiles/app.py
git commit -m "feat: add desktop sync interface"
```

### Task 7: Documentation and Final Verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update README with user workflow**

Modify `README.md`:

```markdown
# SyncFiles

SyncFiles is a Windows-first desktop helper for synchronizing one Android ADB folder with one local or external hard drive folder.

It performs bidirectional fill-in synchronization:

- Files missing on the hard drive are copied from the phone.
- Files missing on the phone are copied from the hard drive.
- Same-path files with different size or modified time are shown as conflicts.
- Deletions are not propagated.

## Requirements

- Python 3.11 or newer.
- Android Platform Tools with `adb` available on `PATH`.
- Android phone with USB debugging enabled and authorized.

## Development

```powershell
python -m pip install -e .[dev]
pytest
python -m syncfiles
```

## Basic Workflow

1. Connect the Android phone through USB.
2. Open SyncFiles with `python -m syncfiles`.
3. Click **Check device**.
4. Choose the hard drive folder with the folder picker.
5. Browse the phone folder from `/sdcard`.
6. Scan differences.
7. Double-click conflicts and choose an action.
8. Start sync after reviewing the preview.
```

- [ ] **Step 2: Run final verification**

Run: `pytest -v`

Expected: all tests pass.

Run: `python -c "from syncfiles.adb import AdbClient; from syncfiles.domain import build_sync_plan; print('imports ok')"`

Expected: `imports ok`.

- [ ] **Step 3: Commit**

```powershell
git add README.md
git commit -m "docs: document SyncFiles workflow"
```

## Self-Review Notes

Spec coverage:

- Desktop app: Task 6.
- Android ADB only: Task 4 and Task 6.
- User-selected hard drive folder: Task 6.
- ADB phone folder browser from `/sdcard`: Task 4 and Task 6.
- Bidirectional fill-in sync with no deletion propagation: Task 2, Task 5, and Task 6.
- Manual conflict choices: Task 2 and Task 6.
- Tests for pure logic and fake ADB/local behavior: Tasks 2 through 5.

Completion-marker scan:

- The plan contains no unfilled implementation steps.

Type consistency:

- `SourceSide`, `ConflictAction`, `ConflictDecision`, `CopyOperation`, and `SyncPlan` are introduced in Task 2 and reused consistently in Tasks 5 and 6.
