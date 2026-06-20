from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, X, Listbox, StringVar, Tk, Toplevel, filedialog, messagebox, ttk

from syncfiles.adb import AdbClient, DeviceState, DeviceStatus
from syncfiles.domain import (
    ConflictAction,
    ConflictDecision,
    CopyOperation,
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


class SyncFilesApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.language = DEFAULT_LANGUAGE
        self.root.title(self._tr("app_title"))
        self.adb = AdbClient()
        self.local_root = StringVar()
        self.phone_root = StringVar(value="/sdcard")
        self.language_label = StringVar(value=LANGUAGE_LABELS[self.language])
        self.status = StringVar(value=self._tr("device_status_unchecked"))
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.plan: SyncPlan | None = None
        self.conflict_choices: dict[str, ConflictAction] = {}
        self.device_status: DeviceStatus | None = None
        self.translatable_widgets: list[tuple[object, str]] = []
        self.tab_text_keys: list[tuple[object, str]] = []
        self._build_ui()
        self.root.after(100, self._drain_log_queue)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill=BOTH, expand=True)

        ttk.Label(outer, textvariable=self.status).pack(anchor="w", fill=X)
        top_row = ttk.Frame(outer)
        top_row.pack(fill=X, pady=(4, 10))
        self._register(ttk.Button(top_row, command=self.check_device), "button_check_device").pack(side=LEFT)
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

        local_row = ttk.Frame(outer)
        local_row.pack(fill=X, pady=4)
        self._register(ttk.Label(local_row), "label_local_folder").pack(side=LEFT)
        ttk.Entry(local_row, textvariable=self.local_root).pack(side=LEFT, fill=X, expand=True, padx=8)
        self._register(ttk.Button(local_row, command=self.choose_local_folder), "button_choose").pack(side=RIGHT)

        phone_row = ttk.Frame(outer)
        phone_row.pack(fill=X, pady=4)
        self._register(ttk.Label(phone_row), "label_phone_folder").pack(side=LEFT)
        ttk.Entry(phone_row, textvariable=self.phone_root).pack(side=LEFT, fill=X, expand=True, padx=8)
        self._register(ttk.Button(phone_row, command=self.open_phone_browser), "button_browse_phone").pack(side=RIGHT)

        actions = ttk.Frame(outer)
        actions.pack(fill=X, pady=8)
        self._register(ttk.Button(actions, command=self.scan_differences), "button_scan").pack(side=LEFT)
        self._register(ttk.Button(actions, command=self.start_sync), "button_start_sync").pack(side=LEFT, padx=8)

        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill=BOTH, expand=True)
        self.phone_to_local_list = Listbox(self.notebook)
        self.local_to_phone_list = Listbox(self.notebook)
        self.conflict_list = Listbox(self.notebook)
        self.conflict_list.bind("<Double-Button-1>", self.choose_conflict_action)
        self.notebook.add(self.phone_to_local_list, text=self._tr("tab_phone_to_local"))
        self.notebook.add(self.local_to_phone_list, text=self._tr("tab_local_to_phone"))
        self.notebook.add(self.conflict_list, text=self._tr("tab_conflicts"))
        self.tab_text_keys = [
            (self.phone_to_local_list, "tab_phone_to_local"),
            (self.local_to_phone_list, "tab_local_to_phone"),
            (self.conflict_list, "tab_conflicts"),
        ]

        self._register(ttk.Label(outer), "label_log").pack(anchor="w", pady=(8, 0))
        self.log_list = Listbox(outer, height=8)
        self.log_list.pack(fill=BOTH, expand=False)

    def check_device(self) -> None:
        self.device_status = self.adb.get_device_status()
        self._refresh_status()

    def choose_local_folder(self) -> None:
        selected = filedialog.askdirectory(title=self._tr("dialog_choose_local"))
        if selected:
            self.local_root.set(selected)

    def open_phone_browser(self) -> None:
        browser = Toplevel(self.root)
        browser.title(self._tr("dialog_choose_phone"))
        current = StringVar(value=self.phone_root.get() or "/sdcard")
        ttk.Label(browser, textvariable=current).pack(fill=X, padx=8, pady=8)
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
                messagebox.showerror(self._tr("dialog_adb_error"), str(exc))

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
        ttk.Button(buttons, text=self._tr("button_open"), command=enter).pack(side=LEFT)
        ttk.Button(buttons, text=self._tr("button_choose_this_folder"), command=choose).pack(side=RIGHT)
        load(current.get() or "/sdcard")

    def scan_differences(self) -> None:
        local = self.local_root.get()
        phone = self.phone_root.get()
        if not local or not phone:
            messagebox.showwarning(self._tr("dialog_missing_folders_title"), self._tr("dialog_missing_folders_message"))
            return
        self._run_background(lambda: self._scan_worker(Path(local), phone))

    def _scan_worker(self, local: Path, phone: str) -> None:
        self._log(self._tr("log_scanning_local"))
        local_files = scan_local_folder(local)
        self._log(self._tr("log_scanning_phone"))
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
        executor = SyncExecutor(adb=self.adb, local_root=local, phone_root=phone)
        executor.execute_operations(operations)
        for operation in operations:
            if operation.source_side is SourceSide.LOCAL and operation.destination_side is SourceSide.PHONE:
                self._log(self._tr("log_pushed", path=operation.relative_path))
            elif operation.source_side is SourceSide.PHONE and operation.destination_side is SourceSide.LOCAL:
                self._log(self._tr("log_pulled", path=operation.relative_path))
        self._log(self._tr("log_sync_complete", count=len(operations)))

    def _run_background(self, target: Callable[[], None]) -> None:
        def wrapped() -> None:
            try:
                target()
            except Exception as exc:
                message = str(exc)
                self._log(self._tr("log_error", message=message))
                self.root.after(0, lambda: messagebox.showerror(self._tr("dialog_error_title"), message))

        threading.Thread(target=wrapped, daemon=True).start()

    def _log(self, message: str) -> None:
        self.log_queue.put(message)

    def _drain_log_queue(self) -> None:
        while not self.log_queue.empty():
            self.log_list.insert(END, self.log_queue.get())
            self.log_list.yview_moveto(1)
        self.root.after(100, self._drain_log_queue)

    def change_language(self, _event: object | None = None) -> None:
        self.language = LANGUAGE_BY_LABEL.get(self.language_label.get(), DEFAULT_LANGUAGE)
        self._refresh_language()

    def _refresh_language(self) -> None:
        self.root.title(self._tr("app_title"))
        for widget, key in self.translatable_widgets:
            widget.configure(text=self._tr(key))
        for tab, key in self.tab_text_keys:
            self.notebook.tab(tab, text=self._tr(key))
        self._refresh_status()
        self._render_plan()

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


def main() -> None:
    root = Tk()
    SyncFilesApp(root)
    root.mainloop()
