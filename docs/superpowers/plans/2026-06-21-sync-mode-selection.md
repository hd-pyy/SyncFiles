# Sync Mode Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit sync mode selector so SyncFiles supports both hard-drive-to-hard-drive sync and hard-drive-to-phone sync without misleading phone-only wording.

**Architecture:** Keep the existing planner model for this iteration and adapt at the app boundary. Add a small mode enum in `syncfiles.app`, add local-to-local copy support in a focused executor helper, and make UI labels, scanning, execution, conflict labels, logs, and tabs depend on the selected mode. Existing ADB behavior stays intact for hard-drive-to-phone mode.

**Tech Stack:** Python 3.11, Tkinter, pytest, standard-library filesystem copying, existing ADB adapter.

---

## Important Workspace Note

Before executing this plan, run:

```powershell
git status --short
```

At plan-writing time, `src/syncfiles/app.py` has uncommitted changes. Do not revert them. Read that file before editing and preserve unrelated user changes while implementing this plan.

## File Structure

- Modify `src/syncfiles/app.py`: add `SyncMode`, mode selector UI, mode-aware labels/tabs/logs/progress, mode-aware scan/sync paths, and local-to-local operation construction.
- Modify `src/syncfiles/i18n.py`: add sync mode labels, left/right folder labels, left/right tabs, left/right logs, and conflict labels.
- Modify `src/syncfiles/local_fs.py`: add local file copy helper that creates parent directories.
- Create `src/syncfiles/local_executor.py`: execute local-to-local `CopyOperation` objects between left and right roots.
- Modify `tests/test_local_fs.py`: cover local copy helper.
- Create `tests/test_local_executor.py`: cover bidirectional local copy execution and completion callbacks.
- Modify `tests/test_i18n.py`: cover new mode-aware labels.
- Modify `tests/test_app.py`: cover default mode, mode switching, plan clearing, mode-aware scanning/syncing, and phone mode preservation.
- Modify `README.md`: document both supported sync modes and the updated workflow.

### Task 1: Local Copy Primitive

**Files:**
- Modify: `src/syncfiles/local_fs.py`
- Test: `tests/test_local_fs.py`

- [ ] **Step 1: Write failing local copy test**

Add this test to `tests/test_local_fs.py`:

```python
def test_copy_local_file_creates_parent_and_copies_bytes(tmp_path: Path) -> None:
    source = tmp_path / "source" / "photos" / "a.jpg"
    destination = tmp_path / "dest" / "nested" / "a.jpg"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"image-bytes")

    copy_local_file(source, destination)

    assert destination.read_bytes() == b"image-bytes"
```

Update imports:

```python
from syncfiles.local_fs import copy_local_file, ensure_parent_directory, scan_local_folder
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
python -m pytest tests/test_local_fs.py::test_copy_local_file_creates_parent_and_copies_bytes -q
```

Expected: FAIL with `ImportError` or `AttributeError` for missing `copy_local_file`.

- [ ] **Step 3: Implement local copy helper**

Add to `src/syncfiles/local_fs.py`:

```python
import shutil
```

```python
def copy_local_file(source: Path, destination: Path) -> None:
    ensure_parent_directory(destination)
    shutil.copy2(source, destination)
```

- [ ] **Step 4: Run local filesystem tests**

Run:

```powershell
python -m pytest tests/test_local_fs.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit local copy helper**

Run:

```powershell
git add src/syncfiles/local_fs.py tests/test_local_fs.py
git commit -m "feat: add local file copy helper"
```

### Task 2: Local-to-Local Executor

**Files:**
- Create: `src/syncfiles/local_executor.py`
- Test: `tests/test_local_executor.py`

- [ ] **Step 1: Write failing local executor tests**

Create `tests/test_local_executor.py`:

```python
from __future__ import annotations

from pathlib import Path

from syncfiles.domain import CopyOperation, SourceSide
from syncfiles.local_executor import LocalSyncExecutor


def test_executes_left_to_right_operation(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    (left / "docs").mkdir(parents=True)
    (left / "docs" / "a.txt").write_text("left", encoding="utf-8")
    executor = LocalSyncExecutor(left_root=left, right_root=right)

    completed = executor.execute_operations(
        [
            CopyOperation(
                relative_path="docs/a.txt",
                source_side=SourceSide.LOCAL,
                destination_side=SourceSide.PHONE,
            )
        ]
    )

    assert (right / "docs" / "a.txt").read_text(encoding="utf-8") == "left"
    assert completed == ["Copied docs/a.txt left to right"]


def test_executes_right_to_left_operation(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    (right / "photos").mkdir(parents=True)
    (right / "photos" / "b.jpg").write_bytes(b"right")
    executor = LocalSyncExecutor(left_root=left, right_root=right)

    completed = executor.execute_operations(
        [
            CopyOperation(
                relative_path="photos/b.jpg",
                source_side=SourceSide.PHONE,
                destination_side=SourceSide.LOCAL,
            )
        ]
    )

    assert (left / "photos" / "b.jpg").read_bytes() == b"right"
    assert completed == ["Copied photos/b.jpg right to left"]


def test_local_executor_uses_destination_relative_path_and_callback(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    (right).mkdir(parents=True)
    (right / "notes.txt").write_text("right", encoding="utf-8")
    executor = LocalSyncExecutor(left_root=left, right_root=right)
    captured: list[tuple[str, float]] = []

    executor.execute_operations(
        [
            CopyOperation(
                relative_path="notes.txt",
                source_side=SourceSide.PHONE,
                destination_side=SourceSide.LOCAL,
                destination_relative_path="notes.txt.sync-conflict-right",
            )
        ],
        on_operation_complete=lambda operation, elapsed: captured.append((operation.relative_path, elapsed)),
    )

    assert (left / "notes.txt.sync-conflict-right").read_text(encoding="utf-8") == "right"
    assert captured[0][0] == "notes.txt"
    assert captured[0][1] >= 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/test_local_executor.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'syncfiles.local_executor'`.

- [ ] **Step 3: Implement local executor**

Create `src/syncfiles/local_executor.py`:

```python
from __future__ import annotations

import time
from pathlib import Path

from syncfiles.domain import CopyOperation, SourceSide
from syncfiles.executor import OperationCallback
from syncfiles.local_fs import copy_local_file


class LocalSyncExecutor:
    def __init__(self, left_root: Path, right_root: Path) -> None:
        self.left_root = left_root
        self.right_root = right_root

    def execute_operations(
        self,
        operations: list[CopyOperation],
        on_operation_complete: OperationCallback | None = None,
    ) -> list[str]:
        completed: list[str] = []
        for operation in operations:
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
```

- [ ] **Step 4: Run local executor tests**

Run:

```powershell
python -m pytest tests/test_local_executor.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit local executor**

Run:

```powershell
git add src/syncfiles/local_executor.py tests/test_local_executor.py
git commit -m "feat: add local sync executor"
```

### Task 3: Sync Mode Labels and Mode State

**Files:**
- Modify: `src/syncfiles/app.py`
- Modify: `src/syncfiles/i18n.py`
- Modify: `tests/test_i18n.py`
- Modify: `tests/test_app.py`

- [ ] **Step 1: Write failing i18n tests**

Add to `tests/test_i18n.py`:

```python
def test_sync_mode_and_left_right_labels_are_translated() -> None:
    assert text("sync_mode_hard_drive", Language.ENGLISH) == "Hard drive <-> hard drive"
    assert text("sync_mode_phone", Language.ENGLISH) == "Hard drive <-> phone"
    assert text("label_left_folder", Language.ENGLISH) == "Left hard drive folder"
    assert text("label_right_folder", Language.ENGLISH) == "Right hard drive folder"
    assert text("tab_left_to_right", Language.ENGLISH) == "Left -> right"
    assert text("tab_right_to_left", Language.ENGLISH) == "Right -> left"
```

- [ ] **Step 2: Write failing app tests for default mode and mode switching**

Add imports to `tests/test_app.py`:

```python
from syncfiles.app import SyncMode
```

Add tests:

```python
def test_default_sync_mode_is_hard_drive_to_hard_drive() -> None:
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)

        assert app.sync_mode is SyncMode.HARD_DRIVE
        assert app.mode_label.get() == text("sync_mode_hard_drive", app.language)
        assert str(app.check_device_button["state"]) == "disabled"
        assert app.second_folder_label.cget("text") == text("label_right_folder", app.language)
        assert app.second_choose_button.cget("text") == text("button_choose", app.language)
        assert app.notebook.tab(app.phone_to_local_list._syncfiles_container, "text") == text(
            "tab_right_to_left",
            app.language,
        )
    finally:
        root.destroy()


def test_changing_sync_mode_updates_labels_and_clears_plan() -> None:
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)
        app.plan = build_sync_plan(phone_files=[], local_files=[])
        app.conflict_choices = {"x": ConflictAction.SKIP}

        app.mode_label.set(text("sync_mode_phone", app.language))
        app.change_sync_mode()

        assert app.sync_mode is SyncMode.PHONE
        assert app.plan is None
        assert app.conflict_choices == {}
        assert str(app.check_device_button["state"]) == "normal"
        assert app.second_folder_label.cget("text") == text("label_phone_folder", app.language)
        assert app.second_choose_button.cget("text") == text("button_browse_phone", app.language)
        assert app.notebook.tab(app.phone_to_local_list._syncfiles_container, "text") == text(
            "tab_phone_to_local",
            app.language,
        )
    finally:
        root.destroy()
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/test_i18n.py tests/test_app.py::test_default_sync_mode_is_hard_drive_to_hard_drive tests/test_app.py::test_changing_sync_mode_updates_labels_and_clears_plan -q
```

Expected: FAIL because `SyncMode`, mode labels, and mode-aware widgets do not exist yet.

- [ ] **Step 4: Add translations**

Add these keys to `TRANSLATIONS` in `src/syncfiles/i18n.py`:

```python
"label_sync_mode": {
    Language.CHINESE: "同步类型",
    Language.ENGLISH: "Sync mode",
},
"sync_mode_hard_drive": {
    Language.CHINESE: "硬盘 <-> 硬盘",
    Language.ENGLISH: "Hard drive <-> hard drive",
},
"sync_mode_phone": {
    Language.CHINESE: "硬盘 <-> 手机",
    Language.ENGLISH: "Hard drive <-> phone",
},
"label_left_folder": {
    Language.CHINESE: "左侧硬盘文件夹",
    Language.ENGLISH: "Left hard drive folder",
},
"label_right_folder": {
    Language.CHINESE: "右侧硬盘文件夹",
    Language.ENGLISH: "Right hard drive folder",
},
"tab_left_to_right": {
    Language.CHINESE: "左侧 -> 右侧",
    Language.ENGLISH: "Left -> right",
},
"tab_right_to_left": {
    Language.CHINESE: "右侧 -> 左侧",
    Language.ENGLISH: "Right -> left",
},
```

- [ ] **Step 5: Add sync mode state and update UI labels**

In `src/syncfiles/app.py`, add:

```python
from enum import StrEnum
```

```python
class SyncMode(StrEnum):
    HARD_DRIVE = "hard_drive"
    PHONE = "phone"
```

In `__init__`, add:

```python
self.sync_mode = SyncMode.HARD_DRIVE
self.mode_label = StringVar(value=self._sync_mode_label(self.sync_mode))
```

In `_build_ui()`, add a mode row near the top:

```python
self._register(ttk.Label(top_row), "label_sync_mode").pack(side=LEFT, padx=(18, 4))
self.mode_selector = ttk.Combobox(
    top_row,
    textvariable=self.mode_label,
    values=[
        self._tr("sync_mode_hard_drive"),
        self._tr("sync_mode_phone"),
    ],
    state="readonly",
    width=20,
)
self.mode_selector.bind("<<ComboboxSelected>>", self.change_sync_mode)
self.mode_selector.pack(side=LEFT)
```

Keep widget references for mode-aware labels/buttons:

```python
self.first_folder_label = self._register(ttk.Label(local_row), "label_left_folder")
self.first_folder_label.pack(side=LEFT)
...
self.second_folder_label = self._register(ttk.Label(phone_row), "label_right_folder")
self.second_folder_label.pack(side=LEFT)
...
self.second_choose_button = self._register(
    ttk.Button(phone_row, command=self.choose_second_folder),
    "button_choose",
)
self.second_choose_button.pack(side=RIGHT)
self.phone_browse_button = self.second_choose_button
```

Add helper methods:

```python
def _sync_mode_label(self, mode: SyncMode) -> str:
    key = "sync_mode_phone" if mode is SyncMode.PHONE else "sync_mode_hard_drive"
    return self._tr(key)


def _sync_mode_from_label(self, label: str) -> SyncMode:
    return SyncMode.PHONE if label == self._tr("sync_mode_phone") else SyncMode.HARD_DRIVE
```

Add:

```python
def change_sync_mode(self, _event: object | None = None) -> None:
    if self._warn_if_busy():
        self.mode_label.set(self._sync_mode_label(self.sync_mode))
        return
    self.sync_mode = self._sync_mode_from_label(self.mode_label.get())
    self.plan = None
    self.conflict_choices = {}
    self._render_plan()
    self._refresh_mode_ui()
```

Add:

```python
def _refresh_mode_ui(self) -> None:
    self.mode_label.set(self._sync_mode_label(self.sync_mode))
    if self.sync_mode is SyncMode.PHONE:
        self.check_device_button.configure(state="disabled" if self.busy else "normal")
        self.first_folder_label.configure(text=self._tr("label_local_folder"))
        self.second_folder_label.configure(text=self._tr("label_phone_folder"))
        self.second_choose_button.configure(text=self._tr("button_browse_phone"), command=self.open_phone_browser)
        self.notebook.tab(self.phone_to_local_list._syncfiles_container, text=self._tr("tab_phone_to_local"))
        self.notebook.tab(self.local_to_phone_list._syncfiles_container, text=self._tr("tab_local_to_phone"))
    else:
        self.check_device_button.configure(state="disabled")
        self.first_folder_label.configure(text=self._tr("label_left_folder"))
        self.second_folder_label.configure(text=self._tr("label_right_folder"))
        self.second_choose_button.configure(text=self._tr("button_choose"), command=self.choose_second_folder)
        self.notebook.tab(self.phone_to_local_list._syncfiles_container, text=self._tr("tab_right_to_left"))
        self.notebook.tab(self.local_to_phone_list._syncfiles_container, text=self._tr("tab_left_to_right"))
    self.notebook.tab(self.conflict_list._syncfiles_container, text=self._tr("tab_conflicts"))
```

Call `_refresh_mode_ui()` at the end of `_build_ui()` and `_refresh_language()`.

In `_refresh_language()`, update the mode selector values before refreshing labels:

```python
self.mode_selector.configure(
    values=[
        self._tr("sync_mode_hard_drive"),
        self._tr("sync_mode_phone"),
    ]
)
```

Update `_set_busy()` so the device check button does not become enabled in hard-drive mode after background work finishes:

```python
def _set_busy(self, busy: bool) -> None:
    self.busy = busy
    state = "disabled" if busy else "normal"
    for button in (
        self.local_choose_button,
        self.second_choose_button,
        self.scan_button,
        self.sync_button,
    ):
        button.configure(state=state)
    self._refresh_mode_ui()
```

- [ ] **Step 6: Add second folder picker**

Add:

```python
def choose_second_folder(self) -> None:
    if self.sync_mode is SyncMode.PHONE:
        self.open_phone_browser()
        return
    if self._warn_if_busy():
        return
    selected = filedialog.askdirectory(title=self._tr("dialog_choose_right"))
    if selected:
        self.phone_root.set(selected)
```

Add translation:

```python
"dialog_choose_right": {
    Language.CHINESE: "选择右侧硬盘文件夹",
    Language.ENGLISH: "Choose right hard drive folder",
},
```

- [ ] **Step 7: Run mode label tests**

Run:

```powershell
python -m pytest tests/test_i18n.py tests/test_app.py::test_default_sync_mode_is_hard_drive_to_hard_drive tests/test_app.py::test_changing_sync_mode_updates_labels_and_clears_plan -q
```

Expected: PASS.

- [ ] **Step 8: Commit mode UI state**

Run:

```powershell
git add src/syncfiles/app.py src/syncfiles/i18n.py tests/test_i18n.py tests/test_app.py
git commit -m "feat: add sync mode selector"
```

### Task 4: Mode-Aware Scanning

**Files:**
- Modify: `src/syncfiles/app.py`
- Modify: `src/syncfiles/i18n.py`
- Modify: `tests/test_app.py`

- [ ] **Step 1: Write failing local-to-local scan test**

Add to `tests/test_app.py`:

```python
def test_hard_drive_mode_scans_two_local_folders(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    (left / "left-only.txt").write_text("left", encoding="utf-8")
    (right / "right-only.txt").write_text("right", encoding="utf-8")
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)
        app.local_root.set(str(left))
        app.phone_root.set(str(right))

        app._scan_worker(left, str(right))

        assert app.plan is not None
        assert [item.relative_path for item in app.plan.local_to_phone] == ["left-only.txt"]
        assert [item.relative_path for item in app.plan.phone_to_local] == ["right-only.txt"]
    finally:
        root.destroy()
```

Add phone preservation test:

```python
def test_phone_mode_scan_still_uses_adb(tmp_path: Path) -> None:
    left = tmp_path / "left"
    left.mkdir()
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)
        app.sync_mode = SyncMode.PHONE
        app._refresh_mode_ui()
        captured: list[str] = []
        app.adb.scan_phone_folder = lambda phone: captured.append(phone) or []  # type: ignore[method-assign]

        app._scan_worker(left, "/sdcard/DCIM")

        assert captured == ["/sdcard/DCIM"]
    finally:
        root.destroy()
```

- [ ] **Step 2: Run scan tests to verify local mode fails**

Run:

```powershell
python -m pytest tests/test_app.py::test_hard_drive_mode_scans_two_local_folders tests/test_app.py::test_phone_mode_scan_still_uses_adb -q
```

Expected: FAIL because hard drive mode still calls `adb.scan_phone_folder()` for the second path.

- [ ] **Step 3: Add mode-aware scan helpers**

In `src/syncfiles/app.py`, add:

```python
def _scan_second_folder(self, second: str) -> list[FileRecord]:
    if self.sync_mode is SyncMode.PHONE:
        self._log(self._tr("log_scanning_phone"))
        return self.adb.scan_phone_folder(second)
    self._log(self._tr("log_scanning_right"))
    records = scan_local_folder(Path(second))
    return [
        FileRecord(
            relative_path=record.relative_path,
            size=record.size,
            modified_time=record.modified_time,
            side=SourceSide.PHONE,
        )
        for record in records
    ]
```

Add `FileRecord` to the existing `from syncfiles.domain import (...)` import.

Change `_scan_worker()`:

```python
self._log(self._tr("log_scanning_left" if self.sync_mode is SyncMode.HARD_DRIVE else "log_scanning_local"))
...
self.progress.advance(
    current_path=self._tr("progress_current_phone" if self.sync_mode is SyncMode.PHONE else "progress_current_right")
)
second_files = self._scan_second_folder(phone)
self.plan = build_sync_plan(phone_files=second_files, local_files=local_files)
```

Add translations:

```python
"log_scanning_left": {
    Language.CHINESE: "正在扫描左侧硬盘文件夹...",
    Language.ENGLISH: "Scanning left hard drive folder...",
},
"log_scanning_right": {
    Language.CHINESE: "正在扫描右侧硬盘文件夹...",
    Language.ENGLISH: "Scanning right hard drive folder...",
},
"progress_current_right": {
    Language.CHINESE: "正在扫描右侧硬盘文件夹",
    Language.ENGLISH: "Scanning right hard drive folder",
},
```

- [ ] **Step 4: Run scan tests**

Run:

```powershell
python -m pytest tests/test_app.py::test_hard_drive_mode_scans_two_local_folders tests/test_app.py::test_phone_mode_scan_still_uses_adb -q
```

Expected: PASS.

- [ ] **Step 5: Commit mode-aware scanning**

Run:

```powershell
git add src/syncfiles/app.py src/syncfiles/i18n.py tests/test_app.py
git commit -m "feat: scan folders by sync mode"
```

### Task 5: Mode-Aware Sync Execution

**Files:**
- Modify: `src/syncfiles/app.py`
- Modify: `tests/test_app.py`

- [ ] **Step 1: Write failing hard-drive sync test**

Add to `tests/test_app.py`:

```python
def test_hard_drive_mode_sync_copies_between_local_roots(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    (left / "left-only.txt").write_text("left", encoding="utf-8")
    (right / "right-only.txt").write_text("right", encoding="utf-8")
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)
        app.sync_mode = SyncMode.HARD_DRIVE
        app.plan = build_sync_plan(
            phone_files=[
                FileRecord("right-only.txt", 5, 1, SourceSide.PHONE),
            ],
            local_files=[
                FileRecord("left-only.txt", 4, 1, SourceSide.LOCAL),
            ],
        )
        app.conflict_choices = {}

        app._sync_worker(left, str(right))

        assert (right / "left-only.txt").read_text(encoding="utf-8") == "left"
        assert (left / "right-only.txt").read_text(encoding="utf-8") == "right"
```

Add phone mode preservation test:

```python
def test_phone_mode_sync_still_uses_adb_transfer(tmp_path: Path) -> None:
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)
        app.sync_mode = SyncMode.PHONE
        app.adb = FakeTransfer()  # type: ignore[assignment]
        app.plan = build_sync_plan(
            phone_files=[
                FileRecord("phone-only.txt", 5, 1, SourceSide.PHONE),
            ],
            local_files=[
                FileRecord("local-only.txt", 4, 1, SourceSide.LOCAL),
            ],
        )
        app.conflict_choices = {}

        app._sync_worker(tmp_path / "local", "/sdcard/Test")

        assert app.adb.pulls == [("/sdcard/Test/phone-only.txt", str(tmp_path / "local" / "phone-only.txt"))]
        assert app.adb.pushes == [(str(tmp_path / "local" / "local-only.txt"), "/sdcard/Test/local-only.txt")]
    finally:
        root.destroy()
```

- [ ] **Step 2: Run sync tests to verify hard-drive mode fails**

Run:

```powershell
python -m pytest tests/test_app.py::test_hard_drive_mode_sync_copies_between_local_roots tests/test_app.py::test_phone_mode_sync_still_uses_adb_transfer -q
```

Expected: FAIL because `_sync_worker()` always uses `SyncExecutor`.

- [ ] **Step 3: Add mode-aware executor selection**

In `src/syncfiles/app.py`, import:

```python
from syncfiles.local_executor import LocalSyncExecutor
```

In `_sync_worker()`, replace executor construction with:

```python
if self.sync_mode is SyncMode.PHONE:
    executor = SyncExecutor(adb=self.adb, local_root=local, phone_root=phone)
else:
    executor = LocalSyncExecutor(left_root=local, right_root=Path(phone))
executor.execute_operations(operations, on_operation_complete=hook)
```

- [ ] **Step 4: Run sync tests**

Run:

```powershell
python -m pytest tests/test_app.py::test_hard_drive_mode_sync_copies_between_local_roots tests/test_app.py::test_phone_mode_sync_still_uses_adb_transfer -q
```

Expected: PASS.

- [ ] **Step 5: Commit mode-aware execution**

Run:

```powershell
git add src/syncfiles/app.py tests/test_app.py
git commit -m "feat: execute sync by selected mode"
```

### Task 6: Mode-Aware Conflict Labels, Logs, and README

**Files:**
- Modify: `src/syncfiles/app.py`
- Modify: `src/syncfiles/i18n.py`
- Modify: `tests/test_app.py`
- Modify: `README.md`

- [ ] **Step 1: Write failing conflict label test**

Add to `tests/test_app.py`:

```python
def test_conflict_action_labels_follow_sync_mode() -> None:
    root = tk.Tk()
    root.withdraw()
    try:
        app = SyncFilesApp(root)

        assert app._conflict_action_label(ConflictAction.USE_PHONE) == text("conflict_use_right", app.language)

        app.sync_mode = SyncMode.PHONE
        assert app._conflict_action_label(ConflictAction.USE_PHONE) == text("conflict_use_phone", app.language)
    finally:
        root.destroy()
```

- [ ] **Step 2: Run conflict label test to verify it fails**

Run:

```powershell
python -m pytest tests/test_app.py::test_conflict_action_labels_follow_sync_mode -q
```

Expected: FAIL because `_conflict_action_label()` does not exist.

- [ ] **Step 3: Add conflict translations and helper**

Add to `TRANSLATIONS`:

```python
"conflict_use_left": {
    Language.CHINESE: "使用左侧版本",
    Language.ENGLISH: "Use left version",
},
"conflict_use_right": {
    Language.CHINESE: "使用右侧版本",
    Language.ENGLISH: "Use right version",
},
"conflict_use_phone": {
    Language.CHINESE: "使用手机版本",
    Language.ENGLISH: "Use phone version",
},
"conflict_use_hard_drive": {
    Language.CHINESE: "使用硬盘版本",
    Language.ENGLISH: "Use hard drive version",
},
```

Add to `SyncFilesApp`:

```python
def _conflict_action_label(self, action: ConflictAction) -> str:
    if self.sync_mode is SyncMode.PHONE:
        return conflict_action_label(action, self.language)
    if action is ConflictAction.USE_PHONE:
        return self._tr("conflict_use_right")
    if action is ConflictAction.USE_LOCAL:
        return self._tr("conflict_use_left")
    return conflict_action_label(action, self.language)
```

Replace `conflict_action_label(..., self.language)` calls in `_render_plan()` and `choose_conflict_action()` with `self._conflict_action_label(...)`.

- [ ] **Step 4: Update README**

Update `README.md` so the introduction says SyncFiles synchronizes two folders and supports:

```markdown
- Hard drive <-> hard drive.
- Hard drive <-> Android phone through ADB.
```

Update the workflow to mention choosing a sync mode before selecting folders.

- [ ] **Step 5: Run conflict label and README-neutral tests**

Run:

```powershell
python -m pytest tests/test_app.py::test_conflict_action_labels_follow_sync_mode tests/test_i18n.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit labels and docs**

Run:

```powershell
git add src/syncfiles/app.py src/syncfiles/i18n.py tests/test_app.py README.md
git commit -m "feat: add mode-aware labels and docs"
```

### Task 7: Final Verification

**Files:**
- Verify all modified files.

- [ ] **Step 1: Run full test suite**

Run:

```powershell
python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 2: Run import smoke check**

Run:

```powershell
python -c "from syncfiles.app import SyncFilesApp, SyncMode; from syncfiles.local_executor import LocalSyncExecutor; print('imports ok')"
```

Expected output:

```text
imports ok
```

- [ ] **Step 3: Inspect git status**

Run:

```powershell
git status --short
```

Expected: no unintended changes. If `src/syncfiles/app.py` had pre-existing user changes, confirm they are either preserved and committed intentionally or still present only if unrelated to this feature.

## Self-Review Notes

Spec coverage:

- Explicit mode selector is covered by Task 3.
- Hard drive to hard drive scan and execution are covered by Tasks 4 and 5.
- Hard drive to phone preservation is covered by Tasks 4 and 5.
- Mode-aware labels, tabs, logs, and conflict choices are covered by Tasks 3 and 6.
- ADB is avoided in hard-drive mode through Tasks 4 and 5.
- Generic arbitrary endpoint composition remains out of scope.

Placeholder scan:

- This plan contains complete tasks, commands, expected failures, implementation snippets, and verification steps.

Type consistency:

- `SyncMode.HARD_DRIVE` and `SyncMode.PHONE` are introduced in Task 3 and used consistently afterward.
- Existing `SourceSide.LOCAL` maps to left or hard drive; existing `SourceSide.PHONE` maps to right or phone depending on mode.
- `LocalSyncExecutor.execute_operations()` matches the existing `SyncExecutor.execute_operations()` callback shape.
