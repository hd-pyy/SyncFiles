from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, VERTICAL, X, Y, Listbox, Scrollbar, StringVar, Tk, Toplevel, filedialog, messagebox, ttk

from syncfiles.adb import AdbClient, DeviceState, DeviceStatus
from syncfiles.domain import (
    ConflictAction,
    ConflictDecision,
    CopyOperation,
    FileRecord,
    SourceSide,
    SyncPlan,
    build_sync_plan,
    resolve_conflicts,
)
from syncfiles.executor import SyncExecutor
from syncfiles.i18n import (
    DEFAULT_LANGUAGE,
    LANGUAGE_BY_LABEL,
    LANGUAGE_LABELS,
    Language,
    conflict_action_label,
    text,
)
from syncfiles.local_fs import scan_local_folder
from syncfiles.local_executor import LocalSyncExecutor
from syncfiles.progress import ProgressMode, ProgressReporter, ProgressSnapshot, ProgressState, format_duration


class SyncMode(StrEnum):
    HARD_DRIVE = "hard_drive"
    PHONE = "phone"


DEVICE_STATUS_TEXT_KEYS: dict[DeviceState, str] = {
    DeviceState.ADB_MISSING: "device_adb_missing",
    DeviceState.NO_DEVICE: "device_no_device",
    DeviceState.UNAUTHORIZED: "device_unauthorized",
    DeviceState.MULTIPLE_DEVICES: "device_multiple",
    DeviceState.READY: "device_ready",
}


def build_operations_from_plan(
    plan: SyncPlan,
    conflict_choices: dict[str, ConflictAction],
) -> list[CopyOperation]:
    operations: list[CopyOperation] = []
    operations.extend(
        CopyOperation(item.relative_path, SourceSide.PHONE, SourceSide.LOCAL) for item in plan.phone_to_local
    )
    operations.extend(
        CopyOperation(item.relative_path, SourceSide.LOCAL, SourceSide.PHONE) for item in plan.local_to_phone
    )
    decisions = {
        path: ConflictDecision(relative_path=path, action=action)
        for path, action in conflict_choices.items()
    }
    operations.extend(resolve_conflicts(plan.conflicts, decisions))
    return operations


def default_language_label() -> str:
    return LANGUAGE_LABELS[DEFAULT_LANGUAGE]


def phone_folder_to_choose(current_path: str, selected_value: str | None) -> str:
    if selected_value and selected_value != "..":
        return selected_value
    return current_path


class SyncFilesApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.language = DEFAULT_LANGUAGE
        self.root.title(self._tr("app_title"))
        self.adb = AdbClient()
        self.local_root = StringVar()
        self.phone_root = StringVar(value="/sdcard")
        self.language_label = StringVar(value=LANGUAGE_LABELS[self.language])
        self.sync_mode = SyncMode.HARD_DRIVE
        self.mode_label = StringVar(value=self._sync_mode_label(self.sync_mode))
        self.status = StringVar(value=self._tr("device_status_unchecked"))
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.progress_queue: queue.Queue[ProgressSnapshot] = queue.Queue()
        self.progress = ProgressReporter(on_change=self._enqueue_progress_snapshot)
        self._current_snapshot: ProgressSnapshot | None = None
        self._rendered_progress_mode = ProgressMode.DETERMINATE
        self.plan: SyncPlan | None = None
        self.conflict_choices: dict[str, ConflictAction] = {}
        self.device_status: DeviceStatus | None = None
        self.busy = False
        self.translatable_widgets: list[tuple[object, str]] = []
        self.tab_text_keys: list[tuple[object, str]] = []
        self._build_ui()
        self.root.after(100, self._drain_log_queue)
        self.root.after(100, self._drain_progress_queue)

    def _make_scrolled_list(self, parent: object) -> Listbox:
        """Build a Listbox paired with a vertical Scrollbar.

        Returns the Listbox so callers can keep referencing it as before.
        The outer ``ttk.Frame`` (containing the Listbox and Scrollbar) is
        attached as ``listbox._syncfiles_container`` so callers can pass it
        to ``ttk.Notebook.add``.
        """
        container = ttk.Frame(parent)
        listbox = Listbox(container, exportselection=False)
        scrollbar = ttk.Scrollbar(container, orient=VERTICAL, command=listbox.yview)
        listbox.configure(yscrollcommand=scrollbar.set)
        listbox.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.pack(side=RIGHT, fill=Y)
        listbox._syncfiles_container = container  # type: ignore[attr-defined]
        return listbox

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill=BOTH, expand=True)

        ttk.Label(outer, textvariable=self.status).pack(anchor="w", fill=X)
        top_row = ttk.Frame(outer)
        top_row.pack(fill=X, pady=(4, 10))
        self.check_device_button = self._register(ttk.Button(top_row, command=self.check_device), "button_check_device")
        self.check_device_button.pack(side=LEFT)
        self._register(ttk.Label(top_row), "label_language").pack(side=LEFT, padx=(18, 4))
        language_selector = ttk.Combobox(
            top_row,
            textvariable=self.language_label,
            values=[LANGUAGE_LABELS[Language.CHINESE], LANGUAGE_LABELS[Language.ENGLISH]],
            state="readonly",
            width=10,
        )
        language_selector.bind("<<ComboboxSelected>>", self.change_language)
        language_selector.pack(side=LEFT)
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

        local_row = ttk.Frame(outer)
        local_row.pack(fill=X, pady=4)
        self.first_folder_label = self._register(ttk.Label(local_row), "label_left_folder")
        self.first_folder_label.pack(side=LEFT)
        ttk.Entry(local_row, textvariable=self.local_root).pack(side=LEFT, fill=X, expand=True, padx=8)
        self.local_choose_button = self._register(ttk.Button(local_row, command=self.choose_local_folder), "button_choose")
        self.local_choose_button.pack(side=RIGHT)

        phone_row = ttk.Frame(outer)
        phone_row.pack(fill=X, pady=4)
        self.second_folder_label = self._register(ttk.Label(phone_row), "label_right_folder")
        self.second_folder_label.pack(side=LEFT)
        ttk.Entry(phone_row, textvariable=self.phone_root).pack(side=LEFT, fill=X, expand=True, padx=8)
        self.second_choose_button = self._register(
            ttk.Button(phone_row, command=self.choose_second_folder),
            "button_choose",
        )
        self.second_choose_button.pack(side=RIGHT)
        self.phone_browse_button = self.second_choose_button

        actions = ttk.Frame(outer)
        actions.pack(fill=X, pady=8)
        self.scan_button = self._register(ttk.Button(actions, command=self.scan_differences), "button_scan")
        self.scan_button.pack(side=LEFT)
        self.sync_button = self._register(ttk.Button(actions, command=self.start_sync), "button_start_sync")
        self.sync_button.pack(side=LEFT, padx=8)

        progress_box = ttk.Frame(outer)
        progress_box.pack(fill=X, pady=(0, 8))
        progress_box.columnconfigure(0, weight=1)
        progress_box.columnconfigure(1, weight=1)
        self.progress_status_label = self._register(ttk.Label(progress_box), "progress_idle")
        self.progress_status_label.grid(row=0, column=0, sticky="w")
        self.progress_eta_label = self._register(ttk.Label(progress_box), "progress_idle")
        self.progress_eta_label.grid(row=0, column=1, sticky="e")
        self.progress_bar = ttk.Progressbar(
            progress_box, mode="determinate", maximum=1, value=0
        )
        self.progress_bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self.progress_current_label = self._register(ttk.Label(progress_box), "progress_idle")
        self.progress_current_label.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill=BOTH, expand=True)
        self.phone_to_local_list = self._make_scrolled_list(self.notebook)
        self.local_to_phone_list = self._make_scrolled_list(self.notebook)
        self.conflict_list = self._make_scrolled_list(self.notebook)
        self.conflict_list.bind("<Double-Button-1>", self.choose_conflict_action)
        self.notebook.add(
            self.phone_to_local_list._syncfiles_container,  # type: ignore[attr-defined]
            text=self._tr("tab_phone_to_local"),
        )
        self.notebook.add(
            self.local_to_phone_list._syncfiles_container,  # type: ignore[attr-defined]
            text=self._tr("tab_local_to_phone"),
        )
        self.notebook.add(
            self.conflict_list._syncfiles_container,  # type: ignore[attr-defined]
            text=self._tr("tab_conflicts"),
        )
        self.tab_text_keys = [
            (self.phone_to_local_list, "tab_phone_to_local"),
            (self.local_to_phone_list, "tab_local_to_phone"),
            (self.conflict_list, "tab_conflicts"),
        ]
        self._refresh_mode_ui()

        self._register(ttk.Label(outer), "label_log").pack(anchor="w", pady=(8, 0))
        self.log_list = Listbox(outer, height=8)
        self.log_list.pack(fill=BOTH, expand=False)

    def check_device(self) -> None:
        self.device_status = self.adb.get_device_status()
        self._refresh_status()

    def choose_local_folder(self) -> None:
        if self._warn_if_busy():
            return
        selected = filedialog.askdirectory(title=self._tr("dialog_choose_local"))
        if selected:
            self.local_root.set(selected)

    def choose_second_folder(self) -> None:
        if self.sync_mode is SyncMode.PHONE:
            self.open_phone_browser()
            return
        if self._warn_if_busy():
            return
        selected = filedialog.askdirectory(title=self._tr("dialog_choose_right"))
        if selected:
            self.phone_root.set(selected)

    def open_phone_browser(self) -> None:
        if self._warn_if_busy():
            return
        browser = Toplevel(self.root)
        browser.title(self._tr("dialog_choose_phone"))
        current = StringVar(value=self.phone_root.get() or "/sdcard")
        ttk.Label(browser, textvariable=current).pack(fill=X, padx=8, pady=8)
        listing_container = ttk.Frame(browser)
        listing_container.pack(fill=BOTH, expand=True, padx=8, pady=8)
        listing_container.columnconfigure(0, weight=1)
        listing_container.rowconfigure(0, weight=1)
        listing = Listbox(listing_container, width=80, height=20, exportselection=False)
        scrollbar = ttk.Scrollbar(
            listing_container, orient=VERTICAL, command=listing.yview
        )
        listing.configure(yscrollcommand=scrollbar.set)
        listing.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        def load(path: str) -> None:
            current.set(path)
            listing.delete(0, END)
            if path != "/sdcard":
                listing.insert(END, "..")
            try:
                for directory in self.adb.list_directories(path):
                    listing.insert(END, directory)
            except Exception as exc:
                messagebox.showerror(self._tr("dialog_adb_error"), str(exc))

        def enter(_event: object | None = None) -> None:
            selection = listing.curselection()
            if not selection:
                messagebox.showwarning(
                    self._tr("dialog_no_phone_selection_title"),
                    self._tr("dialog_no_phone_selection_message"),
                    parent=browser,
                )
                return
            value = listing.get(selection[0])
            if value == "..":
                parent = current.get().rstrip("/").rsplit("/", 1)[0] or "/sdcard"
                load(parent if parent.startswith("/sdcard") else "/sdcard")
            else:
                load(value)

        def choose() -> None:
            selection = listing.curselection()
            selected_value = listing.get(selection[0]) if selection else None
            self.phone_root.set(phone_folder_to_choose(current.get(), selected_value))
            browser.destroy()

        listing.bind("<Double-Button-1>", enter)
        buttons = ttk.Frame(browser)
        buttons.pack(fill=X, padx=8, pady=8)
        ttk.Button(buttons, text=self._tr("button_open"), command=enter).pack(side=LEFT)
        ttk.Button(buttons, text=self._tr("button_choose_this_folder"), command=choose).pack(side=RIGHT)
        load(current.get() or "/sdcard")

    def scan_differences(self) -> None:
        if self._warn_if_busy():
            return
        local = self.local_root.get()
        phone = self.phone_root.get()
        if not local or not phone:
            messagebox.showwarning(self._tr("dialog_missing_folders_title"), self._tr("dialog_missing_folders_message"))
            return
        self._run_background(lambda: self._scan_worker(Path(local), phone))

    def _scan_worker(self, local: Path, phone: str) -> None:
        self._log(self._tr("log_scanning_local" if self.sync_mode is SyncMode.PHONE else "log_scanning_left"))
        self.progress.start(
            total=2,
            current_path=self._tr("progress_current_local"),
            mode=ProgressMode.INDETERMINATE,
        )
        local_files = scan_local_folder(local)
        self.progress.advance(
            current_path=self._tr(
                "progress_current_phone" if self.sync_mode is SyncMode.PHONE else "progress_current_right"
            )
        )
        second_files = self._scan_second_folder(phone)
        self.plan = build_sync_plan(phone_files=second_files, local_files=local_files)
        self.conflict_choices = {
            conflict.relative_path: ConflictAction.SKIP for conflict in self.plan.conflicts
        }
        self.root.after(0, self._render_plan)
        self.progress.succeed()

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
            self.conflict_list.insert(END, f"{conflict.relative_path} [{conflict_action_label(action, self.language)}]")
        self._log(
            self._tr(
                "log_scan_complete",
                phone_to_local=len(self.plan.phone_to_local),
                local_to_phone=len(self.plan.local_to_phone),
                conflicts=len(self.plan.conflicts),
            )
        )

    def choose_conflict_action(self, _event: object | None = None) -> None:
        if self.plan is None:
            return
        selection = self.conflict_list.curselection()
        if not selection:
            return
        conflict = self.plan.conflicts[selection[0]]
        window = Toplevel(self.root)
        window.title(self._tr("dialog_conflict_action"))
        ttk.Label(window, text=conflict.relative_path).pack(fill=X, padx=12, pady=8)

        def choose(action: ConflictAction) -> None:
            self.conflict_choices[conflict.relative_path] = action
            window.destroy()
            self._render_plan()

        ttk.Button(
            window,
            text=conflict_action_label(ConflictAction.USE_PHONE, self.language),
            command=lambda: choose(ConflictAction.USE_PHONE),
        ).pack(
            fill=X,
            padx=12,
            pady=4,
        )
        ttk.Button(
            window,
            text=conflict_action_label(ConflictAction.USE_LOCAL, self.language),
            command=lambda: choose(ConflictAction.USE_LOCAL),
        ).pack(
            fill=X,
            padx=12,
            pady=4,
        )
        ttk.Button(
            window,
            text=conflict_action_label(ConflictAction.KEEP_BOTH, self.language),
            command=lambda: choose(ConflictAction.KEEP_BOTH),
        ).pack(
            fill=X,
            padx=12,
            pady=4,
        )
        ttk.Button(
            window,
            text=conflict_action_label(ConflictAction.SKIP, self.language),
            command=lambda: choose(ConflictAction.SKIP),
        ).pack(fill=X, padx=12, pady=4)

    def start_sync(self) -> None:
        if self._warn_if_busy():
            return
        if self.plan is None:
            messagebox.showwarning(self._tr("dialog_no_scan_title"), self._tr("dialog_no_scan_message"))
            return
        if not messagebox.askyesno(self._tr("dialog_confirm_sync_title"), self._tr("dialog_confirm_sync_message")):
            return
        self._run_background(lambda: self._sync_worker(Path(self.local_root.get()), self.phone_root.get()))

    def _sync_worker(self, local: Path, phone: str) -> None:
        if self.plan is None:
            return
        operations = build_operations_from_plan(self.plan, self.conflict_choices)
        self.progress.start(
            total=len(operations),
            current_path=operations[0].relative_path if operations else None,
        )
        try:
            completed_count = 0

            def hook(_operation: CopyOperation, _elapsed: float) -> None:
                nonlocal completed_count
                completed_count += 1
                next_path = (
                    operations[completed_count].relative_path
                    if completed_count < len(operations)
                    else None
                )
                self.progress.advance(current_path=next_path)

            if self.sync_mode is SyncMode.PHONE:
                executor = SyncExecutor(adb=self.adb, local_root=local, phone_root=phone)
            else:
                executor = LocalSyncExecutor(left_root=local, right_root=Path(phone))
            executor.execute_operations(operations, on_operation_complete=hook)
            for operation in operations:
                if operation.source_side is SourceSide.LOCAL and operation.destination_side is SourceSide.PHONE:
                    self._log(self._tr("log_pushed", path=operation.relative_path))
                elif operation.source_side is SourceSide.PHONE and operation.destination_side is SourceSide.LOCAL:
                    self._log(self._tr("log_pulled", path=operation.relative_path))
            self._log(self._tr("log_sync_complete", count=len(operations)))
        except Exception:
            self.progress.fail()
            raise
        else:
            self.progress.succeed()

    def _run_background(self, target: Callable[[], None]) -> None:
        self._set_busy(True)

        def wrapped() -> None:
            try:
                target()
            except Exception as exc:
                message = str(exc)
                self.progress.fail()
                self._log(self._tr("log_error", message=message))
                self.root.after(0, lambda: messagebox.showerror(self._tr("dialog_error_title"), message))
            finally:
                self.root.after(0, lambda: self._set_busy(False))

        threading.Thread(target=wrapped, daemon=True).start()

    def _log(self, message: str) -> None:
        self.log_queue.put(message)

    def _enqueue_progress_snapshot(self, snapshot: ProgressSnapshot) -> None:
        self.progress_queue.put(snapshot)

    def _drain_progress_queue(self) -> None:
        latest: ProgressSnapshot | None = None
        while True:
            try:
                latest = self.progress_queue.get_nowait()
            except queue.Empty:
                break
        if latest is not None:
            self._render_progress(latest)
        self.root.after(100, self._drain_progress_queue)

    def _render_progress(self, snapshot: ProgressSnapshot) -> None:
        self._current_snapshot = snapshot
        idle = self._tr("progress_idle")
        target_mode = snapshot.mode if snapshot.state is ProgressState.RUNNING else ProgressMode.DETERMINATE
        self._set_progress_mode(target_mode)
        if snapshot.state is ProgressState.IDLE or snapshot.total <= 0:
            self.progress_status_label.configure(text=idle)
            self.progress_eta_label.configure(text="")
            self.progress_current_label.configure(text="")
            self.progress_bar.configure(value=0)
            return
        if snapshot.state is ProgressState.SUCCEEDED:
            self.progress_status_label.configure(text=self._tr("progress_complete"))
            self.progress_eta_label.configure(text="")
            self.progress_current_label.configure(text="")
            self.progress_bar.configure(value=1)
            return
        if snapshot.state is ProgressState.FAILED:
            self.progress_status_label.configure(text=self._tr("progress_failed"))
            self.progress_eta_label.configure(text="")
            if snapshot.current_path:
                self.progress_current_label.configure(
                    text=self._tr("progress_current_file", path=snapshot.current_path)
                )
            else:
                self.progress_current_label.configure(text="")
            self.progress_bar.configure(value=snapshot.fraction)
            return
        self.progress_status_label.configure(
            text=self._tr("progress_x_of_n", index=snapshot.completed, total=snapshot.total)
        )
        if snapshot.completed == 0:
            self.progress_eta_label.configure(text=self._tr("progress_eta_unknown"))
        else:
            self.progress_eta_label.configure(
                text=self._tr("progress_eta_remaining", eta=format_duration(snapshot.eta_seconds))
            )
        if snapshot.current_path:
            self.progress_current_label.configure(
                text=self._tr("progress_current_file", path=snapshot.current_path)
            )
        else:
            self.progress_current_label.configure(text="")
        self.progress_bar.configure(value=snapshot.fraction)

    def _set_progress_mode(self, mode: ProgressMode) -> None:
        if self._rendered_progress_mode is mode:
            return
        self.progress_bar.stop()
        self.progress_bar.configure(mode=mode.value)
        if mode is ProgressMode.INDETERMINATE:
            self.progress_bar.start(80)
        self._rendered_progress_mode = mode

    def _drain_log_queue(self) -> None:
        while not self.log_queue.empty():
            self.log_list.insert(END, self.log_queue.get())
            self.log_list.yview_moveto(1)
        self.root.after(100, self._drain_log_queue)

    def change_language(self, _event: object | None = None) -> None:
        self.language = LANGUAGE_BY_LABEL.get(self.language_label.get(), DEFAULT_LANGUAGE)
        self._refresh_language()

    def change_sync_mode(self, _event: object | None = None) -> None:
        if self._warn_if_busy():
            self.mode_label.set(self._sync_mode_label(self.sync_mode))
            return
        self.sync_mode = self._sync_mode_from_label(self.mode_label.get())
        self.plan = None
        self.conflict_choices = {}
        self._render_plan()
        self._refresh_mode_ui()

    def _refresh_language(self) -> None:
        self.root.title(self._tr("app_title"))
        self.mode_selector.configure(
            values=[
                self._tr("sync_mode_hard_drive"),
                self._tr("sync_mode_phone"),
            ]
        )
        for widget, key in self.translatable_widgets:
            widget.configure(text=self._tr(key))
        for tab, key in self.tab_text_keys:
            self.notebook.tab(tab, text=self._tr(key))
        self._refresh_mode_ui()
        self._refresh_status()
        self._render_plan()
        if self._current_snapshot is not None:
            self._render_progress(self._current_snapshot)

    def _sync_mode_label(self, mode: SyncMode) -> str:
        key = "sync_mode_phone" if mode is SyncMode.PHONE else "sync_mode_hard_drive"
        return self._tr(key)

    def _sync_mode_from_label(self, label: str) -> SyncMode:
        return SyncMode.PHONE if label == self._tr("sync_mode_phone") else SyncMode.HARD_DRIVE

    def _refresh_mode_ui(self) -> None:
        self.mode_label.set(self._sync_mode_label(self.sync_mode))
        if self.sync_mode is SyncMode.PHONE:
            self.check_device_button.configure(state="disabled" if self.busy else "normal")
            self.first_folder_label.configure(text=self._tr("label_local_folder"))
            self.second_folder_label.configure(text=self._tr("label_phone_folder"))
            self.second_choose_button.configure(
                text=self._tr("button_browse_phone"),
                command=self.open_phone_browser,
            )
            self.notebook.tab(
                self.phone_to_local_list._syncfiles_container,  # type: ignore[attr-defined]
                text=self._tr("tab_phone_to_local"),
            )
            self.notebook.tab(
                self.local_to_phone_list._syncfiles_container,  # type: ignore[attr-defined]
                text=self._tr("tab_local_to_phone"),
            )
        else:
            self.check_device_button.configure(state="disabled")
            self.first_folder_label.configure(text=self._tr("label_left_folder"))
            self.second_folder_label.configure(text=self._tr("label_right_folder"))
            self.second_choose_button.configure(
                text=self._tr("button_choose"),
                command=self.choose_second_folder,
            )
            self.notebook.tab(
                self.phone_to_local_list._syncfiles_container,  # type: ignore[attr-defined]
                text=self._tr("tab_right_to_left"),
            )
            self.notebook.tab(
                self.local_to_phone_list._syncfiles_container,  # type: ignore[attr-defined]
                text=self._tr("tab_left_to_right"),
            )
        self.notebook.tab(
            self.conflict_list._syncfiles_container,  # type: ignore[attr-defined]
            text=self._tr("tab_conflicts"),
        )

    def _refresh_status(self) -> None:
        if self.device_status is None:
            self.status.set(self._tr("device_status_unchecked"))
            return
        message = self._device_status_message(self.device_status)
        self.status.set(f"{self._tr('device_status_prefix')}{message}")

    def _device_status_message(self, status: DeviceStatus) -> str:
        key = DEVICE_STATUS_TEXT_KEYS.get(status.state)
        if key is not None:
            return self._tr(key)
        return status.message

    def _register(self, widget: object, key: str) -> object:
        widget.configure(text=self._tr(key))
        self.translatable_widgets.append((widget, key))
        return widget

    def _tr(self, key: str, **values: object) -> str:
        return text(key, self.language, **values)

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

    def _warn_if_busy(self) -> bool:
        if not self.busy:
            return False
        messagebox.showwarning(self._tr("dialog_busy_title"), self._tr("dialog_busy_message"))
        return True


def main() -> None:
    root = Tk()
    SyncFilesApp(root)
    root.mainloop()
