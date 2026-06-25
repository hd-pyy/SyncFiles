from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from datetime import datetime
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
from syncfiles.executor import OperationCancelled, SyncExecutor
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
from syncfiles.sftp import SftpClient, SftpConnectionConfig
from syncfiles.sftp_executor import SftpSyncExecutor


class SyncMode(StrEnum):
    HARD_DRIVE = "hard_drive"
    PHONE = "phone"
    SFTP = "sftp"


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
    destination_side: SourceSide | None = None,
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
    if destination_side is not None:
        operations = [
            operation
            for operation in operations
            if operation.destination_side is destination_side
        ]
    return operations


def default_language_label() -> str:
    return LANGUAGE_LABELS[DEFAULT_LANGUAGE]


def phone_folder_to_choose(current_path: str, selected_value: str | None) -> str:
    if selected_value and selected_value != "..":
        return selected_value
    return current_path


def folder_basename(path: str) -> str:
    cleaned = path.strip().rstrip("/\\")
    if not cleaned:
        return ""
    for separator in ("/", "\\"):
        if separator in cleaned:
            cleaned = cleaned.rsplit(separator, 1)[-1]
    return cleaned


def folders_share_basename(local: str, other: str) -> bool:
    # Empty basenames (root paths or unset inputs) are treated as ambiguous
    # so a user who picks "/" + "/sdcard" or leaves a field blank is not warned.
    local_name = folder_basename(local)
    other_name = folder_basename(other)
    if not local_name or not other_name:
        return True
    return local_name == other_name


class SyncFilesApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.language = DEFAULT_LANGUAGE
        self.root.title(self._tr("app_title"))
        # Pin an initial geometry and a minimum size so a long filename in the
        # diff/log listboxes can't push the window wider — the listboxes are
        # created with width=1 and stretch via pack(fill=BOTH).
        self.root.geometry("1180x760")
        self.root.minsize(900, 620)
        self.adb = AdbClient()
        self.sftp_client = SftpClient()
        self.local_root = StringVar()
        self.phone_root = StringVar(value="/sdcard")
        self.sftp_host = StringVar()
        self.sftp_port = StringVar(value="22")
        self.sftp_username = StringVar()
        self.sftp_password = StringVar()
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
        self.left_records: list[FileRecord] = []
        self.right_records: list[FileRecord] = []
        self.conflict_choices: dict[str, ConflictAction] = {}
        self.conflict_item_paths: dict[str, str] = {}
        self.device_status: DeviceStatus | None = None
        self.busy = False
        self._closing = False
        self._after_ids: set[str] = set()
        # Worker threads poll this between every cancellable step. Cleared by
        # _run_background and set by request_cancel; the cancel button is the
        # only thing that flips it during a run.
        self._cancel_event = threading.Event()
        self.translatable_widgets: list[tuple[object, str]] = []
        self.tab_text_keys: list[tuple[object, str]] = []
        self._build_ui()
        self.root.bind("<Destroy>", self._on_root_destroy, add="+")
        self._schedule_after(100, self._drain_log_queue)
        self._schedule_after(100, self._drain_progress_queue)
        # Wake the adb-server daemon right after the UI is up so the first
        # "check device" or "browse phone" pays the warm ~30ms cost instead
        # of the cold ~5s one. Fire-and-forget: failures are silently
        # swallowed and the real adb call will surface the error.
        if self.root.state() != "withdrawn":
            threading.Thread(target=self.adb.prewarm_server, daemon=True).start()

    def _make_scrolled_list(self, parent: object) -> Listbox:
        """Build a Listbox paired with a vertical Scrollbar.

        Returns the Listbox so callers can keep referencing it as before.
        The outer ``ttk.Frame`` (containing the Listbox and Scrollbar) is
        attached as ``listbox._syncfiles_container`` so callers can pass it
        to ``ttk.Notebook.add``.
        """
        container = ttk.Frame(parent)
        # width=1 keeps the listbox from requesting space based on its longest
        # row — pack(fill=BOTH, expand=True) below still stretches it to fill
        # the container, but a long filename can no longer push the root wider.
        listbox = Listbox(container, exportselection=False, width=1)
        scrollbar = ttk.Scrollbar(container, orient=VERTICAL, command=listbox.yview)
        listbox.configure(yscrollcommand=scrollbar.set)
        listbox.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.pack(side=RIGHT, fill=Y)
        listbox._syncfiles_container = container  # type: ignore[attr-defined]
        return listbox

    def _make_file_tree(self, parent: object) -> ttk.Treeview:
        columns = ("name", "size", "modified", "status")
        tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="browse", height=14)
        tree.heading("name", text=self._tr("column_name"))
        tree.heading("size", text=self._tr("column_size"))
        tree.heading("modified", text=self._tr("column_modified"))
        tree.heading("status", text=self._tr("column_status"))
        tree.column("name", width=260, minwidth=120, stretch=True)
        tree.column("size", width=90, minwidth=70, stretch=False)
        tree.column("modified", width=120, minwidth=90, stretch=False)
        tree.column("status", width=92, minwidth=74, stretch=False)
        tree.tag_configure("same", background="#ecfdf5")
        tree.tag_configure("missing", background="#eff6ff")
        tree.tag_configure("conflict", background="#fffbeb")
        tree.tag_configure("skip", background="#f1f5f9")
        tree.bind("<Double-Button-1>", self.choose_conflict_action)
        scrollbar = ttk.Scrollbar(parent, orient=VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        return tree

    def _build_folder_panel(
        self,
        parent: object,
        title_key: str,
        path_var: StringVar,
        choose_command: Callable[[], None],
    ) -> tuple[ttk.Frame, ttk.Label, ttk.Button, ttk.Label, ttk.Treeview]:
        panel = ttk.Frame(parent, padding=10)
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(3, weight=1)

        title_row = ttk.Frame(panel)
        title_row.grid(row=0, column=0, sticky="ew")
        title_row.columnconfigure(0, weight=1)
        title_label = self._register(ttk.Label(title_row), title_key)
        title_label.grid(row=0, column=0, sticky="w")
        choose_button = self._register(ttk.Button(title_row, command=choose_command), "button_choose")
        choose_button.grid(row=0, column=1, sticky="e")
        ttk.Entry(panel, textvariable=path_var, width=1).grid(row=1, column=0, sticky="ew", pady=(8, 8))
        summary = ttk.Label(panel)
        summary.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        tree_frame = ttk.Frame(panel)
        tree_frame.grid(row=3, column=0, sticky="nsew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        tree = self._make_file_tree(tree_frame)
        return panel, title_label, choose_button, summary, tree

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
        self.mode_label_widget = self._register(ttk.Label(top_row), "label_sync_mode")
        self.mode_label_widget.pack(side=LEFT, padx=(18, 4))
        self.mode_selector = ttk.Combobox(
            top_row,
            textvariable=self.mode_label,
            values=[
                self._tr("sync_mode_hard_drive"),
                self._tr("sync_mode_phone"),
                self._tr("sync_mode_sftp"),
            ],
            state="readonly",
            width=20,
        )
        self.mode_selector.bind("<<ComboboxSelected>>", self.change_sync_mode)
        self.mode_buttons: dict[SyncMode, ttk.Radiobutton] = {}
        for mode in (SyncMode.HARD_DRIVE, SyncMode.PHONE, SyncMode.SFTP):
            mode_button = ttk.Radiobutton(
                top_row,
                text=self._sync_mode_label(mode),
                value=self._sync_mode_label(mode),
                variable=self.mode_label,
                command=self.change_sync_mode,
                style="Toolbutton",
            )
            mode_button.pack(side=LEFT)
            self.mode_buttons[mode] = mode_button

        # SFTP config row sits between the mode selector and the folder rows
        # so the user fills in connection details (host/port/credentials)
        # before picking the remote path. Created unconditionally but only
        # packed by _refresh_mode_ui when sync mode is SFTP — eager packing
        # pushes everything below it out of the window because pack_forget
        # doesn't reflow siblings. Uses a 2-row grid: row 0 holds the four
        # label+entry pairs, row 1 holds the "Test connection" button
        # right-aligned. grid columnconfigure makes the entry columns
        # stretchy so the row never gets squeezed off-screen.
        self.sftp_frame = ttk.Frame(outer)
        for col in (1, 3, 5, 7):
            self.sftp_frame.columnconfigure(col, weight=1)
        self._register(ttk.Label(self.sftp_frame), "label_sftp_host").grid(row=0, column=0, sticky="w")
        ttk.Entry(self.sftp_frame, textvariable=self.sftp_host, width=14).grid(row=0, column=1, sticky="ew", padx=(4, 8))
        self._register(ttk.Label(self.sftp_frame), "label_sftp_port").grid(row=0, column=2, sticky="w")
        ttk.Entry(self.sftp_frame, textvariable=self.sftp_port, width=6).grid(row=0, column=3, sticky="ew", padx=(4, 8))
        self._register(ttk.Label(self.sftp_frame), "label_sftp_username").grid(row=0, column=4, sticky="w")
        ttk.Entry(self.sftp_frame, textvariable=self.sftp_username, width=12).grid(row=0, column=5, sticky="ew", padx=(4, 8))
        self._register(ttk.Label(self.sftp_frame), "label_sftp_password").grid(row=0, column=6, sticky="w")
        ttk.Entry(self.sftp_frame, textvariable=self.sftp_password, show="*", width=12).grid(row=0, column=7, sticky="ew", padx=(4, 0))
        self.test_sftp_button = self._register(
            ttk.Button(self.sftp_frame, command=self.test_sftp_connection),
            "button_test_sftp",
        )
        self.test_sftp_button.grid(row=1, column=7, sticky="e", pady=(4, 0))

        local_row = self.local_row = ttk.Frame(outer)
        local_row.pack(fill=X, pady=4)
        self.first_folder_label = self._register(ttk.Label(local_row), "label_left_folder")
        self.first_folder_label.pack(side=LEFT)
        # width=1 prevents a long path from inflating the entry's requested
        # width; pack(expand=True) still gives it all the slack space.
        ttk.Entry(local_row, textvariable=self.local_root, width=1).pack(side=LEFT, fill=X, expand=True, padx=8)
        self.local_choose_button = self._register(ttk.Button(local_row, command=self.choose_local_folder), "button_choose")
        self.local_choose_button.pack(side=RIGHT)

        phone_row = ttk.Frame(outer)
        phone_row.pack(fill=X, pady=4)
        self.second_folder_label = self._register(ttk.Label(phone_row), "label_right_folder")
        self.second_folder_label.pack(side=LEFT)
        ttk.Entry(phone_row, textvariable=self.phone_root, width=1).pack(side=LEFT, fill=X, expand=True, padx=8)
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
        # Cancel sits next to Start sync but is only enabled while a worker is
        # running — _set_busy is the single source of truth for its state.
        self.cancel_button = self._register(ttk.Button(actions, command=self.request_cancel), "button_cancel")
        self.cancel_button.pack(side=LEFT)
        self.cancel_button.configure(state="disabled")

        local_row.pack_forget()
        phone_row.pack_forget()
        actions.pack_forget()

        self.workspace = ttk.Frame(outer)
        self.workspace.pack(fill=BOTH, expand=True, pady=(0, 8))
        self.workspace.columnconfigure(0, weight=1)
        self.workspace.columnconfigure(1, weight=0)
        self.workspace.columnconfigure(2, weight=1)
        self.workspace.rowconfigure(0, weight=1)

        (
            self.left_panel,
            self.first_folder_label,
            self.local_choose_button,
            self.left_summary_label,
            self.left_file_tree,
        ) = self._build_folder_panel(
            self.workspace,
            "label_left_folder",
            self.local_root,
            self.choose_local_folder,
        )
        self.left_panel.grid(row=0, column=0, sticky="nsew")

        center = ttk.Frame(self.workspace, padding=(12, 10))
        center.grid(row=0, column=1, sticky="ns")
        center.columnconfigure(0, weight=1)
        center.rowconfigure(0, weight=1)
        center.rowconfigure(4, weight=1)
        self.sync_to_right_button = ttk.Button(
            center,
            text=self._direction_button_text("→", self._tr("label_sync_to_right")),
            command=lambda: self.start_sync(SourceSide.PHONE),
            width=16,
        )
        self.sync_to_right_button.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self.sync_to_right_label = self._register(ttk.Label(center), "label_sync_to_right")
        self.sync_to_left_button = ttk.Button(
            center,
            text=self._direction_button_text("←", self._tr("label_sync_to_left")),
            command=lambda: self.start_sync(SourceSide.LOCAL),
            width=16,
        )
        self.sync_to_left_button.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self.sync_to_left_label = self._register(ttk.Label(center), "label_sync_to_left")
        self.scan_button = self._register(ttk.Button(center, command=self.scan_differences), "button_scan")
        self.scan_button.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self.cancel_button = self._register(ttk.Button(center, command=self.request_cancel), "button_cancel")
        self.cancel_button.grid(row=5, column=0, sticky="ew", pady=(0, 0))
        self.cancel_button.configure(state="disabled")
        self.sync_button = self.sync_to_right_button

        (
            self.right_panel,
            self.second_folder_label,
            self.second_choose_button,
            self.right_summary_label,
            self.right_file_tree,
        ) = self._build_folder_panel(
            self.workspace,
            "label_right_folder",
            self.phone_root,
            self.choose_second_folder,
        )
        self.right_folder_title = self.second_folder_label
        self.phone_browse_button = self.second_choose_button
        self.right_panel.grid(row=0, column=2, sticky="nsew")

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
            (self.phone_to_local_list._syncfiles_container, "tab_phone_to_local"),  # type: ignore[attr-defined]
            (self.local_to_phone_list._syncfiles_container, "tab_local_to_phone"),  # type: ignore[attr-defined]
            (self.conflict_list._syncfiles_container, "tab_conflicts"),  # type: ignore[attr-defined]
        ]
        self.notebook.pack_forget()
        self._refresh_mode_ui()
        self._refresh_file_summaries()

        self._register(ttk.Label(outer), "label_log").pack(anchor="w", pady=(8, 0))
        # Mirror _make_scrolled_list: pair the log Listbox with a vertical
        # Scrollbar inside a Frame so long history is reachable. width=1 keeps
        # a long log line from widening the root; fill=BOTH lets it grow with
        # the window instead.
        log_container = ttk.Frame(outer)
        log_container.pack(fill=BOTH, expand=False)
        self.log_list = Listbox(log_container, height=8, width=1)
        log_scrollbar = ttk.Scrollbar(log_container, orient=VERTICAL, command=self.log_list.yview)
        self.log_list.configure(yscrollcommand=log_scrollbar.set)
        self.log_list.pack(side=LEFT, fill=BOTH, expand=True)
        log_scrollbar.pack(side=RIGHT, fill=Y)

    def _schedule_after(self, delay_ms: int, callback: Callable[[], None]) -> None:
        if self._closing:
            return
        after_id = self.root.after(delay_ms, callback)
        self._after_ids.add(after_id)

    def _on_root_destroy(self, event: object) -> None:
        if getattr(event, "widget", None) is not self.root:
            return
        self._closing = True
        for after_id in list(self._after_ids):
            try:
                self.root.after_cancel(after_id)
            except Exception:
                pass
            finally:
                self._after_ids.discard(after_id)

    def check_device(self) -> None:
        if self._warn_if_busy():
            return
        self._run_background(self._check_device_worker)

    def _check_device_worker(self) -> None:
        # adb startup is slow (cold process spawn, ~1-2s). Run it on a worker
        # thread so the UI stays responsive; the result is delivered to the
        # main thread via _set_busy(False) -> _refresh_mode_ui.
        self.device_status = self.adb.get_device_status()
        self.root.after(0, self._refresh_status)

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
        if self.sync_mode is SyncMode.SFTP:
            self.open_sftp_browser()
            return
        if self._warn_if_busy():
            return
        selected = filedialog.askdirectory(title=self._tr("dialog_choose_right"))
        if selected:
            self.phone_root.set(selected)

    def open_phone_browser(self) -> None:
        if self._warn_if_busy():
            return
        if not self._phone_device_ready_or_warn():
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
        status_var = StringVar(value=self._tr("phone_browser_loading"))
        status_label = ttk.Label(browser, textvariable=status_var, foreground="gray")
        status_label.pack(fill=X, padx=8, pady=(0, 4))

        # Per-browser reentrancy guard: prevents double-clicks and rapid
        # "Open" presses from queueing overlapping adb calls.
        loading = {"value": False}

        def show_loading(message: str) -> None:
            listing.delete(0, END)
            status_var.set(message)
            open_button.configure(state="disabled")
            choose_button.configure(state="disabled")

        def show_results(path: str, directories: list[str]) -> None:
            current.set(path)
            listing.delete(0, END)
            if path != "/sdcard":
                listing.insert(END, "..")
            for directory in directories:
                listing.insert(END, directory)
            status_var.set("")
            open_button.configure(state="normal")
            choose_button.configure(state="normal")

        def load(path: str) -> None:
            if loading["value"]:
                return
            loading["value"] = True
            show_loading(self._tr("phone_browser_loading"))

            def worker() -> list[str] | None:
                try:
                    return self.adb.list_directories(path)
                except Exception as exc:
                    self.root.after(
                        0,
                        lambda: messagebox.showerror(
                            self._tr("dialog_adb_error"),
                            str(exc),
                            parent=browser,
                        ),
                    )
                    return None
                finally:
                    self.root.after(0, _finish_load)

            def _finish_load() -> None:
                loading["value"] = False

            def runner() -> None:
                result = worker()
                if result is not None:
                    self.root.after(0, lambda: show_results(path, result))

            threading.Thread(target=runner, daemon=True).start()

        def enter(_event: object | None = None) -> None:
            if loading["value"]:
                return
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
            if loading["value"]:
                return
            selection = listing.curselection()
            selected_value = listing.get(selection[0]) if selection else None
            self.phone_root.set(phone_folder_to_choose(current.get(), selected_value))
            browser.destroy()

        listing.bind("<Double-Button-1>", enter)
        buttons = ttk.Frame(browser)
        buttons.pack(fill=X, padx=8, pady=8)
        open_button = ttk.Button(buttons, text=self._tr("button_open"), command=enter)
        open_button.pack(side=LEFT)
        choose_button = ttk.Button(
            buttons, text=self._tr("button_choose_this_folder"), command=choose
        )
        choose_button.pack(side=RIGHT)
        load(current.get() or "/sdcard")

    def open_sftp_browser(self) -> None:
        if self._warn_if_busy():
            return
        # Validation happens inside load()'s worker — opening the browser
        # itself should never block on config errors, since the browser IS
        # the path for the user to fix the SFTP folder. The connection
        # fields are still checked on the first list attempt so a bad
        # host/password surfaces immediately.
        browser = Toplevel(self.root)
        browser.title(self._tr("dialog_choose_sftp"))
        current = StringVar(value=self.phone_root.get() or "/")
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
        status_var = StringVar(value=self._tr("sftp_browser_loading"))
        status_label = ttk.Label(browser, textvariable=status_var, foreground="gray")
        status_label.pack(fill=X, padx=8, pady=(0, 4))

        # Per-browser reentrancy guard: same role as the one in
        # open_phone_browser — stops double-clicks from queueing parallel
        # SSH handshakes.
        loading = {"value": False}

        def show_loading(message: str) -> None:
            listing.delete(0, END)
            status_var.set(message)
            open_button.configure(state="disabled")
            choose_button.configure(state="disabled")

        def show_results(path: str, directories: list[str]) -> None:
            current.set(path)
            listing.delete(0, END)
            if path != "/":
                listing.insert(END, "..")
            for directory in directories:
                listing.insert(END, directory)
            status_var.set("")
            open_button.configure(state="normal")
            choose_button.configure(state="normal")

        def load(path: str) -> None:
            if loading["value"]:
                return
            loading["value"] = True
            show_loading(self._tr("sftp_browser_loading"))

            def worker() -> list[str] | None:
                try:
                    # Re-read each time so password/host edits after the
                    # browser opened still take effect on the next directory
                    # load. _sftp_config_for_test skips the remote_root
                    # check — the browser IS how the user picks that.
                    cfg = self._sftp_config_for_test()
                except ValueError as exc:
                    self.root.after(
                        0,
                        lambda: messagebox.showwarning(
                            self._tr("dialog_sftp_config_title"),
                            str(exc),
                            parent=browser,
                        ),
                    )
                    self.root.after(
                        0,
                        lambda: (
                            status_var.set(""),
                            open_button.configure(state="normal"),
                            choose_button.configure(state="normal"),
                        ),
                    )
                    self.root.after(0, _finish_load)
                    return None
                try:
                    with self.sftp_client.connect(cfg) as session:
                        return session.list_directories(path)
                except Exception as exc:
                    self.root.after(
                        0,
                        lambda: messagebox.showerror(
                            self._tr("dialog_sftp_error_title"),
                            str(exc),
                            parent=browser,
                        ),
                    )
                    # Without this the status label and Open/Choose buttons
                    # stay frozen on the "loading" state forever after a
                    # failed connect.
                    self.root.after(
                        0,
                        lambda: (
                            status_var.set(""),
                            open_button.configure(state="normal"),
                            choose_button.configure(state="normal"),
                        ),
                    )
                    return None
                finally:
                    self.root.after(0, _finish_load)

            def _finish_load() -> None:
                loading["value"] = False

            def runner() -> None:
                result = worker()
                if result is not None:
                    self.root.after(0, lambda: show_results(path, result))

            threading.Thread(target=runner, daemon=True).start()

        def enter(_event: object | None = None) -> None:
            if loading["value"]:
                return
            selection = listing.curselection()
            if not selection:
                messagebox.showwarning(
                    self._tr("dialog_no_sftp_selection_title"),
                    self._tr("dialog_no_sftp_selection_message"),
                    parent=browser,
                )
                return
            value = listing.get(selection[0])
            if value == "..":
                # POSIX parent: strip trailing slash, take everything before
                # the last "/". An already-at-root path stays at "/".
                here = current.get().rstrip("/") or "/"
                if here == "/":
                    return
                parent = here.rsplit("/", 1)[0] or "/"
                load(parent)
            else:
                join = current.get().rstrip("/")
                next_path = join + "/" + value if join not in ("", "/") else "/" + value
                load(next_path)

        def choose() -> None:
            if loading["value"]:
                return
            self.phone_root.set(current.get())
            browser.destroy()

        listing.bind("<Double-Button-1>", enter)
        buttons = ttk.Frame(browser)
        buttons.pack(fill=X, padx=8, pady=8)
        open_button = ttk.Button(buttons, text=self._tr("button_open"), command=enter)
        open_button.pack(side=LEFT)
        choose_button = ttk.Button(
            buttons, text=self._tr("button_choose_this_folder"), command=choose
        )
        choose_button.pack(side=RIGHT)
        load(current.get() or "/")

    def scan_differences(self) -> None:
        if self._warn_if_busy():
            return
        if not self._phone_device_ready_or_warn():
            return
        local = self.local_root.get()
        phone = self.phone_root.get()
        if not local or not phone:
            messagebox.showwarning(self._tr("dialog_missing_folders_title"), self._tr("dialog_missing_folders_message"))
            return
        if self.sync_mode is SyncMode.SFTP and not self._validate_sftp_config_or_warn():
            return
        if not folders_share_basename(local, phone):
            if not messagebox.askyesno(
                self._tr("dialog_folder_mismatch_title"),
                self._tr(
                    "dialog_folder_mismatch_message",
                    first=folder_basename(local),
                    second=folder_basename(phone),
                ),
            ):
                return
        self._run_background(lambda: self._scan_worker(Path(local), phone))

    def _scan_worker(self, local: Path, phone: str) -> None:
        self._log(self._tr("log_scanning_local" if self.sync_mode is SyncMode.PHONE else "log_scanning_left"))
        self.progress.start(
            total=2,
            current_path=self._tr("progress_current_local"),
            mode=ProgressMode.INDETERMINATE,
        )
        local_files = scan_local_folder(local, is_cancelled=self._cancel_event.is_set)
        second_progress_key = "progress_current_phone"
        if self.sync_mode is SyncMode.HARD_DRIVE:
            second_progress_key = "progress_current_right"
        elif self.sync_mode is SyncMode.SFTP:
            second_progress_key = "progress_current_sftp"
        self.progress.advance(current_path=self._tr(second_progress_key))
        if self._cancel_event.is_set():
            raise OperationCancelled
        second_files = self._scan_second_folder(phone)
        if self._cancel_event.is_set():
            raise OperationCancelled
        self.left_records = local_files
        self.right_records = second_files
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
        if self.sync_mode is SyncMode.SFTP:
            self._log(self._tr("log_scanning_sftp"))
            with self.sftp_client.connect(self._sftp_config()) as session:
                return session.scan_folder(second, is_cancelled=self._cancel_event.is_set)
        self._log(self._tr("log_scanning_right"))
        records = scan_local_folder(Path(second), is_cancelled=self._cancel_event.is_set)
        return [
            FileRecord(
                relative_path=record.relative_path,
                size=record.size,
                modified_time=record.modified_time,
                side=SourceSide.PHONE,
            )
            for record in records
        ]

    def _format_size(self, size: int) -> str:
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        if size < 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        return f"{size / (1024 * 1024 * 1024):.1f} GB"

    def _format_modified(self, modified_time: int) -> str:
        if modified_time <= 0:
            return ""
        return datetime.fromtimestamp(modified_time).strftime("%Y-%m-%d %H:%M")

    def _tree_status_label(self, status: str) -> str:
        return self._tr(f"file_status_{status}")

    def _insert_file_row(
        self,
        tree: ttk.Treeview,
        record: FileRecord,
        status: str,
        side_prefix: str,
    ) -> None:
        tags = {
            "same": ("same",),
            "only_left": ("missing",),
            "only_right": ("missing",),
            "conflict": ("conflict",),
            "skip": ("skip",),
        }.get(status, ())
        item_id = f"{side_prefix}:{record.relative_path}"
        if status == "conflict":
            self.conflict_item_paths[item_id] = record.relative_path
        tree.insert(
            "",
            END,
            iid=item_id,
            values=(
                record.relative_path,
                self._format_size(record.size),
                self._format_modified(record.modified_time),
                self._tree_status_label(status),
            ),
            tags=tags,
        )

    def _refresh_file_summaries(self) -> None:
        if self.plan is None:
            self.left_summary_label.configure(text=self._tr("summary_no_scan"))
            self.right_summary_label.configure(text=self._tr("summary_no_scan"))
            return
        self.left_summary_label.configure(
            text=self._tr(
                "summary_left",
                total=len(self.left_records),
                to_right=len(self.plan.local_to_phone),
                conflicts=len(self.plan.conflicts),
            )
        )
        self.right_summary_label.configure(
            text=self._tr(
                "summary_right",
                total=len(self.right_records),
                to_left=len(self.plan.phone_to_local),
                conflicts=len(self.plan.conflicts),
            )
        )

    def _render_plan(self) -> None:
        self.phone_to_local_list.delete(0, END)
        self.local_to_phone_list.delete(0, END)
        self.conflict_list.delete(0, END)
        self.left_file_tree.delete(*self.left_file_tree.get_children())
        self.right_file_tree.delete(*self.right_file_tree.get_children())
        self.conflict_item_paths = {}
        self._refresh_file_summaries()
        if self.plan is None:
            return
        left_by_path = {record.relative_path: record for record in self.left_records}
        right_by_path = {record.relative_path: record for record in self.right_records}
        left_to_right = {record.relative_path for record in self.plan.local_to_phone}
        right_to_left = {record.relative_path for record in self.plan.phone_to_local}
        conflicts = {conflict.relative_path for conflict in self.plan.conflicts}
        for path in sorted(left_by_path):
            if path in conflicts:
                status = "conflict"
            elif path in left_to_right:
                status = "only_left"
            elif path in right_by_path:
                status = "same"
            else:
                status = "skip"
            self._insert_file_row(self.left_file_tree, left_by_path[path], status, "left")
        for path in sorted(right_by_path):
            if path in conflicts:
                status = "conflict"
            elif path in right_to_left:
                status = "only_right"
            elif path in left_by_path:
                status = "same"
            else:
                status = "skip"
            self._insert_file_row(self.right_file_tree, right_by_path[path], status, "right")
        for item in self.plan.phone_to_local:
            self.phone_to_local_list.insert(END, item.relative_path)
        for item in self.plan.local_to_phone:
            self.local_to_phone_list.insert(END, item.relative_path)
        for conflict in self.plan.conflicts:
            action = self.conflict_choices.get(conflict.relative_path, ConflictAction.SKIP)
            self.conflict_list.insert(END, f"{conflict.relative_path} [{self._conflict_action_label(action)}]")
        if self.sync_mode is SyncMode.PHONE:
            scan_complete_key = "log_scan_complete"
        elif self.sync_mode is SyncMode.SFTP:
            scan_complete_key = "log_scan_complete_sftp"
        else:
            scan_complete_key = "log_scan_complete_hard_drive"
        self._log(
            self._tr(
                scan_complete_key,
                phone_to_local=len(self.plan.phone_to_local),
                local_to_phone=len(self.plan.local_to_phone),
                conflicts=len(self.plan.conflicts),
            )
        )

    def choose_conflict_action(self, _event: object | None = None) -> None:
        if self.plan is None:
            return
        conflict_path: str | None = None
        for tree in (self.left_file_tree, self.right_file_tree):
            selection = tree.selection()
            if selection:
                conflict_path = self.conflict_item_paths.get(selection[0])
                break
        if conflict_path is None:
            selection = self.conflict_list.curselection()
            if selection:
                conflict_path = self.plan.conflicts[selection[0]].relative_path
        if conflict_path is None:
            return
        conflict = next(
            item for item in self.plan.conflicts if item.relative_path == conflict_path
        )
        window = Toplevel(self.root)
        window.title(self._tr("dialog_conflict_action"))
        ttk.Label(window, text=conflict.relative_path).pack(fill=X, padx=12, pady=8)

        def choose(action: ConflictAction) -> None:
            self.conflict_choices[conflict.relative_path] = action
            window.destroy()
            self._render_plan()

        ttk.Button(
            window,
            text=self._conflict_action_label(ConflictAction.USE_PHONE),
            command=lambda: choose(ConflictAction.USE_PHONE),
        ).pack(
            fill=X,
            padx=12,
            pady=4,
        )
        ttk.Button(
            window,
            text=self._conflict_action_label(ConflictAction.USE_LOCAL),
            command=lambda: choose(ConflictAction.USE_LOCAL),
        ).pack(
            fill=X,
            padx=12,
            pady=4,
        )
        ttk.Button(
            window,
            text=self._conflict_action_label(ConflictAction.KEEP_BOTH),
            command=lambda: choose(ConflictAction.KEEP_BOTH),
        ).pack(
            fill=X,
            padx=12,
            pady=4,
        )
        ttk.Button(
            window,
            text=self._conflict_action_label(ConflictAction.SKIP),
            command=lambda: choose(ConflictAction.SKIP),
        ).pack(fill=X, padx=12, pady=4)

    def start_sync(self, destination_side: SourceSide | None = None) -> None:
        if self._warn_if_busy():
            return
        if self.plan is None:
            messagebox.showwarning(self._tr("dialog_no_scan_title"), self._tr("dialog_no_scan_message"))
            return
        if not self._phone_device_ready_or_warn():
            return
        local = self.local_root.get()
        phone = self.phone_root.get()
        if self.sync_mode is SyncMode.SFTP and not self._validate_sftp_config_or_warn():
            return
        if not folders_share_basename(local, phone):
            if not messagebox.askyesno(
                self._tr("dialog_folder_mismatch_title"),
                self._tr(
                    "dialog_folder_mismatch_message",
                    first=folder_basename(local),
                    second=folder_basename(phone),
                ),
            ):
                return
        if not messagebox.askyesno(self._tr("dialog_confirm_sync_title"), self._tr("dialog_confirm_sync_message")):
            return
        self._run_background(lambda: self._sync_worker(Path(local), phone, destination_side=destination_side))

    def _sync_worker(
        self,
        local: Path,
        phone: str,
        destination_side: SourceSide | None = None,
    ) -> None:
        if self.plan is None:
            return
        operations = build_operations_from_plan(
            self.plan,
            self.conflict_choices,
            destination_side=destination_side,
        )
        self.progress.start(
            total=len(operations),
            current_path=operations[0].relative_path if operations else None,
        )
        try:
            completed_count = 0
            completed_operations: list[CopyOperation] = []

            def hook(operation: CopyOperation, _elapsed: float) -> None:
                nonlocal completed_count
                completed_count += 1
                completed_operations.append(operation)
                next_path = (
                    operations[completed_count].relative_path
                    if completed_count < len(operations)
                    else None
                )
                self.progress.advance(current_path=next_path)

            try:
                if self.sync_mode is SyncMode.PHONE:
                    executor = SyncExecutor(adb=self.adb, local_root=local, phone_root=phone)
                    executor.execute_operations(
                        operations,
                        on_operation_complete=hook,
                        is_cancelled=self._cancel_event.is_set,
                    )
                elif self.sync_mode is SyncMode.SFTP:
                    with self.sftp_client.connect(self._sftp_config()) as session:
                        executor = SftpSyncExecutor(sftp=session, local_root=local, remote_root=phone)
                        executor.execute_operations(
                            operations,
                            on_operation_complete=hook,
                            is_cancelled=self._cancel_event.is_set,
                        )
                else:
                    executor = LocalSyncExecutor(left_root=local, right_root=Path(phone))
                    executor.execute_operations(
                        operations,
                        on_operation_complete=hook,
                        is_cancelled=self._cancel_event.is_set,
                    )
            except OperationCancelled:
                self._log_executed_operations(completed_operations)
                self._log(self._tr("log_sync_cancelled", count=len(completed_operations)))
                self.progress.cancel()
                return
            self._log_executed_operations(operations)
            self._log(self._tr("log_sync_complete", count=len(operations)))
        except Exception:
            self.progress.fail()
            raise
        else:
            self.progress.succeed()

    def _log_executed_operations(self, operations: list[CopyOperation]) -> None:
        if self.sync_mode is SyncMode.PHONE:
            push_key = "log_pushed"
            pull_key = "log_pulled"
        elif self.sync_mode is SyncMode.SFTP:
            push_key = "log_uploaded_sftp"
            pull_key = "log_downloaded_sftp"
        else:
            push_key = "log_copied_left_to_right"
            pull_key = "log_copied_right_to_left"
        for operation in operations:
            if operation.source_side is SourceSide.LOCAL and operation.destination_side is SourceSide.PHONE:
                self._log(self._tr(push_key, path=operation.relative_path))
            elif operation.source_side is SourceSide.PHONE and operation.destination_side is SourceSide.LOCAL:
                self._log(self._tr(pull_key, path=operation.relative_path))

    def _run_background(self, target: Callable[[], None]) -> None:
        self._cancel_event.clear()
        self._set_busy(True)

        def wrapped() -> None:
            try:
                target()
            except OperationCancelled:
                # Workers may raise this before reaching their own cancel
                # handler (e.g. scan_local_folder bailing out). Treat it the
                # same way _sync_worker does: mark the run cancelled, log it,
                # and skip the error dialog.
                self.progress.cancel()
                self._log(self._tr("log_scan_cancelled"))
            except Exception as exc:
                message = str(exc)
                self.progress.fail()
                self._log(self._tr("log_error", message=message))
                self.root.after(0, lambda: messagebox.showerror(self._tr("dialog_error_title"), message))
            finally:
                self.root.after(0, lambda: self._set_busy(False))

        threading.Thread(target=wrapped, daemon=True).start()

    def request_cancel(self) -> None:
        """Signal any running scan or sync worker to stop at its next check.

        Safe to call when no worker is running — the event is cleared at
        every ``_run_background`` start, so setting it now is harmless.
        """
        if not self.busy:
            return
        self._cancel_event.set()
        self.cancel_button.configure(state="disabled")

    def _log(self, message: str) -> None:
        self.log_queue.put(message)

    def _enqueue_progress_snapshot(self, snapshot: ProgressSnapshot) -> None:
        self.progress_queue.put(snapshot)

    def _drain_progress_queue(self) -> None:
        if self._closing:
            return
        latest: ProgressSnapshot | None = None
        while True:
            try:
                latest = self.progress_queue.get_nowait()
            except queue.Empty:
                break
        if latest is not None:
            self._render_progress(latest)
        self._schedule_after(100, self._drain_progress_queue)

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
        if snapshot.state is ProgressState.CANCELLED:
            # Cancellation is a terminal "nothing to show" state, like IDLE —
            # not SUCCEEDED (value=1 asserts completion that never happened)
            # and not FAILED (value=fraction points at where the run died).
            # The completed count is preserved in the log line; the bar
            # itself should not look like a paused run.
            self.progress_status_label.configure(text=self._tr("progress_cancelled"))
            self.progress_eta_label.configure(text="")
            self.progress_current_label.configure(text="")
            self.progress_bar.configure(value=0)
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
        if self._closing:
            return
        while not self.log_queue.empty():
            self.log_list.insert(END, self.log_queue.get())
            self.log_list.yview_moveto(1)
        self._schedule_after(100, self._drain_log_queue)

    def change_language(self, _event: object | None = None) -> None:
        self.language = LANGUAGE_BY_LABEL.get(self.language_label.get(), DEFAULT_LANGUAGE)
        self._refresh_language()

    def change_sync_mode(self, _event: object | None = None) -> None:
        if self._warn_if_busy():
            self.mode_label.set(self._sync_mode_label(self.sync_mode))
            return
        self.sync_mode = self._sync_mode_from_label(self.mode_label.get())
        # Reset the right-hand folder: "/sdcard" is the Android default that
        # only makes sense in phone mode, and any other value the user typed
        # for a previous mode is almost certainly wrong for the new one.
        # For phone mode the standard default kicks in via _refresh_mode_ui.
        if self.sync_mode is SyncMode.PHONE:
            self.phone_root.set("/sdcard")
        else:
            self.phone_root.set("")
        self.plan = None
        self.left_records = []
        self.right_records = []
        self.conflict_choices = {}
        self._render_plan()
        self._refresh_mode_ui()

    def test_sftp_connection(self) -> None:
        if self._warn_if_busy():
            return
        # Test-connection only needs host/port/credentials — the remote
        # folder is irrelevant. _sftp_config() also requires remote_root, so
        # validate the connection fields inline instead of going through it.
        try:
            config = self._sftp_config_for_test()
        except ValueError as exc:
            messagebox.showwarning(self._tr("dialog_sftp_config_title"), str(exc))
            return
        self._log(self._tr("log_sftp_testing"))
        self._run_background(lambda: self._test_sftp_connection_worker(config))

    def _test_sftp_connection_worker(self, config: SftpConnectionConfig) -> None:
        # Open then immediately close. If connect() doesn't raise, the
        # handshake (TCP + SSH + SFTP subsystem) succeeded, which is exactly
        # what "can I reach the server with these credentials" means.
        try:
            with self.sftp_client.connect(config):
                pass
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            self._log(self._tr("log_sftp_test_failed", message=message))
            self.root.after(
                0,
                lambda: messagebox.showerror(
                    self._tr("dialog_sftp_test_failed_title"),
                    self._tr("dialog_sftp_test_failed_message", message=message),
                ),
            )
            return
        self._log(self._tr("log_sftp_test_succeeded"))
        self.root.after(
            0,
            lambda: messagebox.showinfo(
                self._tr("dialog_sftp_test_succeeded_title"),
                self._tr("dialog_sftp_test_succeeded_message"),
            ),
        )

    def _refresh_language(self) -> None:
        self.root.title(self._tr("app_title"))
        self.mode_selector.configure(
            values=[
                self._tr("sync_mode_hard_drive"),
                self._tr("sync_mode_phone"),
                self._tr("sync_mode_sftp"),
            ]
        )
        for widget, key in self.translatable_widgets:
            widget.configure(text=self._tr(key))
        for mode, button in self.mode_buttons.items():
            button.configure(text=self._sync_mode_label(mode), value=self._sync_mode_label(mode))
        for tree in (self.left_file_tree, self.right_file_tree):
            tree.heading("name", text=self._tr("column_name"))
            tree.heading("size", text=self._tr("column_size"))
            tree.heading("modified", text=self._tr("column_modified"))
            tree.heading("status", text=self._tr("column_status"))
        for tab, key in self.tab_text_keys:
            self.notebook.tab(tab, text=self._tr(key))
        self._refresh_mode_ui()
        self._refresh_status()
        self._render_plan()
        if self._current_snapshot is not None:
            self._render_progress(self._current_snapshot)

    def _sync_mode_label(self, mode: SyncMode) -> str:
        if mode is SyncMode.PHONE:
            return self._tr("sync_mode_phone")
        if mode is SyncMode.SFTP:
            return self._tr("sync_mode_sftp")
        return self._tr("sync_mode_hard_drive")

    def _sync_mode_from_label(self, label: str) -> SyncMode:
        if label == self._tr("sync_mode_phone"):
            return SyncMode.PHONE
        if label == self._tr("sync_mode_sftp"):
            return SyncMode.SFTP
        return SyncMode.HARD_DRIVE

    def _direction_button_text(self, arrow: str, label: str) -> str:
        return f"{arrow} {label}"

    def _sftp_config(self) -> SftpConnectionConfig:
        host = self.sftp_host.get().strip()
        username = self.sftp_username.get().strip()
        password = self.sftp_password.get()
        remote_root = self.phone_root.get().strip()
        if not host or not username or not password or not remote_root:
            raise ValueError(self._tr("error_sftp_missing_fields"))
        try:
            port = int(self.sftp_port.get().strip())
        except ValueError as exc:
            raise ValueError(self._tr("error_sftp_invalid_port")) from exc
        if port < 1 or port > 65535:
            raise ValueError(self._tr("error_sftp_invalid_port"))
        return SftpConnectionConfig(host=host, port=port, username=username, password=password)

    def _sftp_config_for_test(self) -> SftpConnectionConfig:
        # Like _sftp_config, but ignores remote_root — the test-connection
        # action only needs host/port/credentials, and the SFTP folder is
        # almost always empty when the user first reaches the field.
        host = self.sftp_host.get().strip()
        username = self.sftp_username.get().strip()
        password = self.sftp_password.get()
        if not host or not username or not password:
            raise ValueError(self._tr("error_sftp_missing_connection_fields"))
        try:
            port = int(self.sftp_port.get().strip())
        except ValueError as exc:
            raise ValueError(self._tr("error_sftp_invalid_port")) from exc
        if port < 1 or port > 65535:
            raise ValueError(self._tr("error_sftp_invalid_port"))
        return SftpConnectionConfig(host=host, port=port, username=username, password=password)

    def _validate_sftp_config_or_warn(self) -> bool:
        try:
            self._sftp_config()
        except ValueError as exc:
            messagebox.showwarning(self._tr("dialog_sftp_config_title"), str(exc))
            return False
        return True

    def _conflict_action_label(self, action: ConflictAction) -> str:
        if self.sync_mode is SyncMode.PHONE:
            return conflict_action_label(action, self.language)
        if self.sync_mode is SyncMode.SFTP:
            if action is ConflictAction.USE_PHONE:
                return self._tr("conflict_use_sftp")
            if action is ConflictAction.USE_LOCAL:
                return self._tr("conflict_use_hard_drive")
            return conflict_action_label(action, self.language)
        if action is ConflictAction.USE_PHONE:
            return self._tr("conflict_use_right")
        if action is ConflictAction.USE_LOCAL:
            return self._tr("conflict_use_left")
        return conflict_action_label(action, self.language)

    def _refresh_mode_ui(self) -> None:
        self.mode_label.set(self._sync_mode_label(self.sync_mode))
        if self.sync_mode is SyncMode.PHONE:
            self.sftp_frame.pack_forget()
            self.check_device_button.configure(state="disabled" if self.busy else "normal")
            self.first_folder_label.configure(text=self._tr("label_local_folder"))
            self.second_folder_label.configure(text=self._tr("label_phone_folder"))
            self.sync_to_right_label.configure(text=self._tr("tab_local_to_phone"))
            self.sync_to_left_label.configure(text=self._tr("tab_phone_to_local"))
            self.sync_to_right_button.configure(
                text=self._direction_button_text("→", self._tr("tab_local_to_phone"))
            )
            self.sync_to_left_button.configure(
                text=self._direction_button_text("←", self._tr("tab_phone_to_local"))
            )
            self.second_choose_button.configure(
                text=self._tr("button_browse_phone"),
                state="disabled" if self.busy else "normal",
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
        elif self.sync_mode is SyncMode.SFTP:
            self.sftp_frame.pack(fill=X, pady=4, before=self.workspace)
            self.check_device_button.configure(state="disabled")
            self.first_folder_label.configure(text=self._tr("label_local_folder"))
            self.second_folder_label.configure(text=self._tr("label_sftp_remote_folder"))
            self.sync_to_right_label.configure(text=self._tr("tab_local_to_sftp"))
            self.sync_to_left_label.configure(text=self._tr("tab_sftp_to_local"))
            self.sync_to_right_button.configure(
                text=self._direction_button_text("→", self._tr("tab_local_to_sftp"))
            )
            self.sync_to_left_button.configure(
                text=self._direction_button_text("←", self._tr("tab_sftp_to_local"))
            )
            self.second_choose_button.configure(
                text=self._tr("button_browse_sftp"),
                state="disabled" if self.busy else "normal",
                command=self.choose_second_folder,
            )
            self.notebook.tab(
                self.phone_to_local_list._syncfiles_container,  # type: ignore[attr-defined]
                text=self._tr("tab_sftp_to_local"),
            )
            self.notebook.tab(
                self.local_to_phone_list._syncfiles_container,  # type: ignore[attr-defined]
                text=self._tr("tab_local_to_sftp"),
            )
        else:
            self.sftp_frame.pack_forget()
            self.check_device_button.configure(state="disabled")
            self.first_folder_label.configure(text=self._tr("label_left_folder"))
            self.second_folder_label.configure(text=self._tr("label_right_folder"))
            self.sync_to_right_label.configure(text=self._tr("label_sync_to_right"))
            self.sync_to_left_label.configure(text=self._tr("label_sync_to_left"))
            self.sync_to_right_button.configure(
                text=self._direction_button_text("→", self._tr("label_sync_to_right"))
            )
            self.sync_to_left_button.configure(
                text=self._direction_button_text("←", self._tr("label_sync_to_left"))
            )
            self.second_choose_button.configure(
                text=self._tr("button_choose"),
                state="disabled" if self.busy else "normal",
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

    def _phone_device_ready_or_warn(self) -> bool:
        if self.sync_mode is not SyncMode.PHONE:
            return True
        if self.device_status is None:
            self.device_status = self.adb.get_device_status()
            self._refresh_status()
        if self.device_status.state is DeviceState.READY:
            return True
        messagebox.showwarning(
            self._tr("dialog_device_not_ready_title"),
            self._device_status_message(self.device_status),
        )
        return False

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
            self.sync_to_left_button,
        ):
            button.configure(state=state)
        # Test-connection is only meaningful in SFTP mode (the host/port/cred
        # fields are hidden otherwise) and shouldn't fire mid-run.
        test_state = (
            "normal"
            if (self.sync_mode is SyncMode.SFTP and not busy)
            else "disabled"
        )
        self.test_sftp_button.configure(state=test_state)
        # Cancel is the inverse — only meaningful mid-run.
        self.cancel_button.configure(state="normal" if busy else "disabled")
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
