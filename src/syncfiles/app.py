from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from tkinter import BOTH, BooleanVar, END, LEFT, RIGHT, TOP, VERTICAL, X, Y, Listbox, Scrollbar, StringVar, Tk, Toplevel, filedialog, messagebox, ttk
from typing import NamedTuple

from syncfiles.adb import AdbClient, DeviceState, DeviceStatus
from syncfiles.domain import (
    Conflict,
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


class _RowView(NamedTuple):
    """One aligned row in the dual-pane Treeview.

    Each row represents a single ``relative_path`` across the two folders.
    Either ``phone`` or ``local`` may be ``None`` when the file exists only
    on the opposite side. ``status`` picks the row tag (red / blue / gray).
    """

    relative_path: str
    phone: FileRecord | None
    local: FileRecord | None
    status: str  # "phone_only" | "local_only" | "conflict" | "identical"


def _build_row_views(plan: SyncPlan) -> list[_RowView]:
    """Flatten a SyncPlan into a single sorted list of aligned rows.

    The sorted union of every relative_path across all four buckets is
    iterated exactly once. Each path produces one ``_RowView`` regardless
    of which bucket it came from, which is what gives the Treeview its
    left/right alignment by filename.
    """
    phone_only = {f.relative_path: f for f in plan.phone_to_local}
    local_only = {f.relative_path: f for f in plan.local_to_phone}
    conflicts = {c.relative_path: c for c in plan.conflicts}
    identical = {f.relative_path: f for f in plan.identical}

    rows: list[_RowView] = []
    all_paths = sorted(
        phone_only.keys() | local_only.keys() | conflicts.keys() | identical.keys()
    )
    for path in all_paths:
        if path in conflicts:
            conflict = conflicts[path]
            rows.append(_RowView(path, conflict.phone, conflict.local, "conflict"))
        elif path in phone_only:
            rows.append(_RowView(path, phone_only[path], None, "phone_only"))
        elif path in local_only:
            rows.append(_RowView(path, None, local_only[path], "local_only"))
        elif path in identical:
            rows.append(_RowView(path, identical[path], identical[path], "identical"))
    return rows


def _format_size(size: int) -> str:
    """Render a file size for the Treeview's size column."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / (1024 * 1024 * 1024):.1f} GB"


def _format_mtime(epoch_seconds: int) -> str:
    """Render a Unix timestamp as a sortable, locale-neutral YYYY-MM-DD HH:MM."""
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _direction_glyph_for_row(row: _RowView, sync_direction: str) -> str:
    """Pick the symbol shown in the Treeview's center "Action" column.

    Conflict rows show ``!`` because the user resolves them in a popup.
    Side-only rows always show the symbol of the direction that *would*
    copy the file — phone_only rows always read from the phone, local_only
    rows always read from local, regardless of the global direction toggle.
    Identical rows show ``-`` to signal "nothing to do".
    """
    if row.status == "conflict":
        return "!"
    if row.status == "identical":
        return "-"
    if row.status == "phone_only":
        return "→"  # the phone side has it → flow points to local
    if row.status == "local_only":
        return "←"  # the local side has it → flow points to phone
    return ""


def build_operations_from_view(
    views: list[_RowView],
    conflict_choices: dict[str, ConflictAction],
) -> list[CopyOperation]:
    """Translate a rendered Treeview row list into CopyOperations.

    Side-only rows are direction-invariant: a phone_only row is always a
    pull (phone → local) and a local_only row is always a push (local →
    phone). The global sync_direction toggle does not flip these because
    flipping them would mean "push a phone file to itself" / "pull a
    local file from itself", which is meaningless.
    """
    operations: list[CopyOperation] = []
    # Walk the views once and bucket per status; identical rows produce no
    # operation and are skipped here.
    phone_only_paths: dict[str, FileRecord] = {}
    local_only_paths: dict[str, FileRecord] = {}
    conflict_paths: set[str] = set()
    for row in views:
        if row.status == "phone_only" and row.phone is not None:
            phone_only_paths[row.relative_path] = row.phone
        elif row.status == "local_only" and row.local is not None:
            local_only_paths[row.relative_path] = row.local
        elif row.status == "conflict":
            conflict_paths.add(row.relative_path)

    for path, _record in phone_only_paths.items():
        operations.append(CopyOperation(path, SourceSide.PHONE, SourceSide.LOCAL))
    for path, _record in local_only_paths.items():
        operations.append(CopyOperation(path, SourceSide.LOCAL, SourceSide.PHONE))

    # Re-derive a minimal Conflict list from views so resolve_conflicts can
    # apply the user's choices (USE_PHONE / USE_LOCAL / KEEP_BOTH / SKIP).
    from syncfiles.domain import Conflict
    minimal_conflicts = [
        Conflict(relative_path=r.relative_path, phone=r.phone, local=r.local)
        for r in views
        if r.status == "conflict" and r.phone is not None and r.local is not None
    ]
    decisions_map = {
        path: ConflictDecision(relative_path=path, action=conflict_choices.get(path, ConflictAction.SKIP))
        for path in conflict_paths
    }
    operations.extend(resolve_conflicts(minimal_conflicts, decisions_map))
    return operations


class SyncFilesApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.language = DEFAULT_LANGUAGE
        self.root.title(self._tr("app_title"))
        # Pin an initial geometry and a minimum size so a long filename in the
        # diff/log listboxes can't push the window wider — the listboxes are
        # created with width=1 and stretch via pack(fill=BOTH).
        self.root.geometry("620x780")
        self.root.minsize(520, 600)
        self.adb = AdbClient()
        logging.getLogger("syncfiles.adb").info("[adb] using %s", self.adb.adb_path)
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
        # Default sync direction is "right_to_left" — the most common case is
        # pulling new files off the phone (right) onto the hard drive (left).
        # The two big arrow buttons flip this; per-row action is then derived
        # from (sync_direction, row.status) inside _render_plan.
        self.sync_direction: str = "right_to_left"
        self.show_identical: bool = False
        self.device_status: DeviceStatus | None = None
        self.busy = False
        # Worker threads poll this between every cancellable step. Cleared by
        # _run_background and set by request_cancel; the cancel button is the
        # only thing that flips it during a run.
        self._cancel_event = threading.Event()
        self.translatable_widgets: list[tuple[object, str]] = []
        self.tab_text_keys: list[tuple[object, str]] = []  # legacy, kept for back-compat
        self.pane_label_keys: list[tuple[ttk.Label, str]] = []
        self.column_heading_keys: list[tuple[str, str]] = []
        self.arrow_button_keys: list[tuple[ttk.Button, str]] = []
        self.checkbox_keys: list[tuple[ttk.Checkbutton, str]] = []
        self._build_ui()
        # Apply the initial active-button bold so the default direction is
        # visible before the user has run a scan.
        self._refresh_arrow_buttons()
        self.root.after(100, self._drain_log_queue)
        self.root.after(100, self._drain_progress_queue)
        # Wake the adb-server daemon right after the UI is up so the first
        # "check device" or "browse phone" pays the warm ~30ms cost instead
        # of the cold ~5s one. Fire-and-forget: failures are silently
        # swallowed and the real adb call will surface the error.
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

        # Two-pane grouped Treeview. Layout (top to bottom):
        #   row 0: pane title row  (left title — spacer — right title)
        #   row 1: toolbar         (checkbox on left — vertical arrow buttons on right)
        #   row 2: the Treeview itself, with 3 columns (left / center / right)
        #          plus the "#0" tree column that holds expand/collapse triangles.
        # The center column is narrow and shows nothing in the row — the actual
        # arrow buttons live in the toolbar's column-2 zone, visually above the
        # tree's center.
        self.pane_container = ttk.Frame(outer)
        self.pane_container.pack(fill=BOTH, expand=True)
        self.pane_container.columnconfigure(0, weight=1)
        self.pane_container.columnconfigure(1, weight=0)
        self.pane_container.columnconfigure(2, weight=0)
        self.pane_container.columnconfigure(3, weight=1)
        self.pane_container.rowconfigure(2, weight=1)

        # Pane title row.
        header_row = ttk.Frame(self.pane_container)
        header_row.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 4))
        self.left_pane_header = ttk.Label(header_row)
        self.left_pane_header.pack(side=LEFT)
        ttk.Frame(header_row).pack(side=LEFT, expand=True, fill=X)
        self.right_pane_header = ttk.Label(header_row)
        self.right_pane_header.pack(side=RIGHT)
        self.pane_label_keys = [
            (self.left_pane_header, "pane_left_header_phone"),
            (self.right_pane_header, "pane_right_header_phone"),
        ]

        # Toolbar row: [checkbox] .................................. [arrow][arrow]
        toolbar = ttk.Frame(self.pane_container)
        toolbar.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(0, 4))
        toolbar.columnconfigure(0, weight=1)
        toolbar.columnconfigure(1, weight=0)
        self.show_identical_var = BooleanVar(value=self.show_identical)
        self.show_identical_checkbox = ttk.Checkbutton(
            toolbar,
            variable=self.show_identical_var,
            command=self._on_show_identical_toggle,
        )
        self.show_identical_checkbox.grid(row=0, column=0, sticky="w")
        arrow_zone = ttk.Frame(toolbar)
        arrow_zone.grid(row=0, column=1, sticky="e")
        # The "right_to_left" arrow goes on top (default active), the
        # "left_to_right" arrow on the bottom — same visual order as the
        # user's screenshot.
        self.right_to_left_button = ttk.Button(
            arrow_zone, command=self._flip_to_right_to_left, width=3
        )
        self.right_to_left_button.pack(side=TOP, pady=1)
        self.left_to_right_button = ttk.Button(
            arrow_zone, command=self._flip_to_left_to_right, width=3
        )
        self.left_to_right_button.pack(side=TOP, pady=1)
        self.arrow_button_keys = [
            (self.right_to_left_button, "arrow_right_to_left"),
            (self.left_to_right_button, "arrow_left_to_right"),
        ]
        self.checkbox_keys = [
            (self.show_identical_checkbox, "toggle_show_identical"),
        ]

        # Treeview body. show="tree headings" exposes the #0 (tree) column for
        # the expand/collapse triangle; the 3 data columns carry the file
        # cells. The center column is intentionally narrow and empty in every
        # row — it just reserves space so the row text doesn't crowd the
        # arrow buttons visually.
        self.tree = ttk.Treeview(
            self.pane_container,
            columns=("left", "center", "right"),
            show="tree headings",
            selectmode="browse",
        )
        self.tree.heading("#0", text="")
        self.tree.heading("left", text=self._tr("label_left_folder"))
        self.tree.heading("center", text="")
        self.tree.heading("right", text=self._tr("label_right_folder"))
        self.tree.column("#0", width=24, stretch=False)
        self.tree.column("left", width=380, anchor="w", stretch=True)
        self.tree.column("center", width=24, anchor="center", stretch=False)
        self.tree.column("right", width=380, anchor="w", stretch=True)
        # Two-line rows need a taller row height. ``rowheight`` is a style
        # option on the Treeview, not a configure() option on the widget.
        ttk.Style().configure("Treeview", rowheight=44)
        self.column_heading_keys = [
            ("left", "label_left_folder"),
            ("right", "label_right_folder"),
        ]
        # Group/band/row tags.
        self.tree.tag_configure("group_header", font=("", 10, "bold"))
        self.tree.tag_configure("band_different", background="#ddf4ff")
        self.tree.tag_configure("band_conflict", background="#ffe0e6")
        self.tree.tag_configure("band_identical", background="#eeeeee")
        self.tree.tag_configure("row_different", foreground="#0a4a8a")
        self.tree.tag_configure("row_conflict", foreground="#a01030")
        self.tree.tag_configure("row_conflict_resolved", foreground="#0a7030")
        self.tree.tag_configure("row_identical", foreground="#888888")
        # Map used to look up the _RowView for a clicked iid (relative_path).
        self._row_views_by_path: dict[str, _RowView] = {}
        self.tree.bind("<Double-Button-1>", self._on_tree_double_click)
        self.tree.grid(row=2, column=0, columnspan=4, sticky="nsew")
        scroll = ttk.Scrollbar(
            self.pane_container, orient=VERTICAL, command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.grid(row=2, column=4, sticky="ns")

        # Keep these around as None so any stale test attribute lookup doesn't
        # AttributeError; tests that read them should be rewritten (see plan).
        self.phone_to_local_list = None
        self.local_to_phone_list = None
        self.conflict_list = None
        self.notebook = None

        self._refresh_mode_ui()

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

    def scan_differences(self) -> None:
        if self._warn_if_busy():
            return
        local = self.local_root.get()
        phone = self.phone_root.get()
        if not local or not phone:
            messagebox.showwarning(self._tr("dialog_missing_folders_title"), self._tr("dialog_missing_folders_message"))
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
        self.progress.advance(
            current_path=self._tr(
                "progress_current_phone" if self.sync_mode is SyncMode.PHONE else "progress_current_right"
            )
        )
        if self._cancel_event.is_set():
            raise OperationCancelled
        second_files = self._scan_second_folder(phone)
        if self._cancel_event.is_set():
            raise OperationCancelled
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

    def _render_plan(self) -> None:
        # Wipe any previous render; deleting all children of the root "" node
        # empties the Treeview in one call. _row_views_by_path is reset so a
        # subsequent double-click can't look up a stale iid.
        for iid in self.tree.get_children(""):
            self.tree.delete(iid)
        self._row_views_by_path = {}
        if self.plan is None:
            return

        views = _build_row_views(self.plan)
        different = [v for v in views if v.status in ("phone_only", "local_only")]
        conflicts = [v for v in views if v.status == "conflict"]
        identical = [v for v in views if v.status == "identical"]

        if different:
            self._populate_group(
                "group_different",
                "group_different_files",
                "band_different",
                "row_different",
                different,
            )
        if conflicts:
            self._populate_group(
                "group_conflict",
                "group_conflict_files",
                "band_conflict",
                "row_conflict",
                conflicts,
            )
        if self.show_identical_var.get() and identical:
            self._populate_group(
                "group_identical",
                "group_identical_files",
                "band_identical",
                "row_identical",
                identical,
            )

        scan_complete_key = (
            "log_scan_complete" if self.sync_mode is SyncMode.PHONE else "log_scan_complete_hard_drive"
        )
        self._log(
            self._tr(
                scan_complete_key,
                phone_to_local=len(self.plan.phone_to_local),
                local_to_phone=len(self.plan.local_to_phone),
                conflicts=len(self.plan.conflicts),
            )
        )
        self._refresh_arrow_buttons()

    def _populate_group(
        self,
        group_iid: str,
        label_key: str,
        band_tag: str,
        row_tag: str,
        views: list[_RowView],
    ) -> None:
        """Insert one section header, a colored band row, and N data rows.

        Layout inside the group:
          group header (iid=group_iid, text=label, open=True)
            ├── band row (iid=__group_X_band, values=("", "", ""), tag=band_tag)
            ├── data row 1 (iid=relative_path, values=(left, "", right), tag=row_tag)
            ├── data row 2
            └── ...
        """
        label = self._tr(label_key, count=len(views))
        self.tree.insert(
            "", "end", iid=group_iid, text=label, open=True, tags=("group_header",)
        )
        self.tree.insert(
            group_iid,
            "end",
            iid=f"__{group_iid}_band",
            values=("", "", ""),
            tags=(band_tag,),
        )
        for view in views:
            resolved_tag = row_tag
            if view.status == "conflict":
                action = self.conflict_choices.get(
                    view.relative_path, ConflictAction.SKIP
                )
                if action in (
                    ConflictAction.USE_PHONE,
                    ConflictAction.USE_LOCAL,
                    ConflictAction.KEEP_BOTH,
                ):
                    resolved_tag = "row_conflict_resolved"
            left_cell = self._format_cell(view, side="left")
            right_cell = self._format_cell(view, side="right")
            self.tree.insert(
                group_iid,
                "end",
                iid=view.relative_path,
                values=(left_cell, "", right_cell),
                tags=(resolved_tag,),
            )
            self._row_views_by_path[view.relative_path] = view

    def _format_cell(self, view: _RowView, side: str) -> str:
        """Build the two-line cell text for one side of a row.

        ``side="right"`` maps to the phone side (because the phone root sits
        in the right folder picker) and ``side="left"`` maps to the local
        side. Conflict rows get the "左侧更新: ..." / "右侧更新: ..." prefix
        because both sides have a copy; non-conflict rows just show the
        date and size. The phone side of a local-only row is empty (and
        vice versa) to keep the visual asymmetry the user wants.
        """
        record = view.phone if side == "right" else view.local
        if record is None:
            return ""
        if view.status == "conflict":
            key = "meta_right_updated" if side == "right" else "meta_left_updated"
        else:
            key = "meta_with_date_size"
        meta = self._tr(
            key,
            date=_format_mtime(record.modified_time),
            size=_format_size(record.size),
        )
        return f"{view.relative_path}\n{meta}"

    def _refresh_arrow_buttons(self) -> None:
        """Visually mark the active direction button as bold."""
        bold = ttk.Style()
        bold.configure("Active.TButton", font=("TkDefaultFont", 10, "bold"))
        if self.sync_direction == "left_to_right":
            self.left_to_right_button.configure(style="Active.TButton")
            self.right_to_left_button.configure(style="TButton")
        else:
            self.right_to_left_button.configure(style="Active.TButton")
            self.left_to_right_button.configure(style="TButton")

    def _flip_to_left_to_right(self) -> None:
        self.sync_direction = "left_to_right"
        # Direction is a UI-only affordance; no data changes, so no need to
        # re-render the tree. Just refresh the bold state of the buttons.
        self._refresh_arrow_buttons()

    def _flip_to_right_to_left(self) -> None:
        self.sync_direction = "right_to_left"
        self._refresh_arrow_buttons()

    def _on_show_identical_toggle(self) -> None:
        self.show_identical = bool(self.show_identical_var.get())
        self._render_plan()

    def _on_tree_double_click(self, event: object) -> None:
        """Toggle a group's open state, or open the conflict popup.

        Double-clicking a group header OR its colored band row toggles
        fold/expand. Double-clicking a data row opens the conflict popup
        if the row is a conflict; non-conflict rows do nothing.
        """
        iid = self.tree.identify_row(event.y)  # type: ignore[attr-defined]
        if not iid:
            return
        # Group header or band row → toggle parent open state.
        if iid.startswith("group_") or iid.startswith("__group_"):
            target = iid if iid.startswith("group_") else self.tree.parent(iid)
            if target:
                self.tree.item(target, open=not bool(self.tree.item(target, "open")))
            return
        # Data row → conflict popup if applicable.
        view = self._row_views_by_path.get(iid)
        if view is None:
            return
        if view.status == "conflict" and view.phone is not None and view.local is not None:
            self._open_conflict_popup(view)

    def choose_conflict_action(self, _event: object | None = None) -> None:
        # Legacy entry point: kept for the old <Double-Button-1> binding path
        # that some tests may still wire. The primary path is now
        # _on_tree_double_click → _open_conflict_popup(view).
        if self.plan is None:
            return
        path = getattr(self, "_selected_row_path", "")
        for conflict in self.plan.conflicts:
            if conflict.relative_path == path:
                self._open_conflict_popup(conflict)
                return
        if self.plan.conflicts:
            self._open_conflict_popup(self.plan.conflicts[0])

    def _open_conflict_popup(self, view_or_conflict: object) -> None:
        """Open the 4-button resolution popup for one conflict row.

        Accepts either a ``Conflict`` (legacy) or a ``_RowView`` (new design,
        from _on_tree_double_click) — the latter is the primary path.
        Re-renders after the user picks so the row's color shifts from
        red → dark green.
        """
        if isinstance(view_or_conflict, Conflict):
            conflict = view_or_conflict
        else:
            view = view_or_conflict
            assert view.phone is not None and view.local is not None
            conflict = Conflict(
                relative_path=view.relative_path,
                phone=view.phone,
                local=view.local,
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
        ).pack(fill=X, padx=12, pady=4)
        ttk.Button(
            window,
            text=self._conflict_action_label(ConflictAction.USE_LOCAL),
            command=lambda: choose(ConflictAction.USE_LOCAL),
        ).pack(fill=X, padx=12, pady=4)
        ttk.Button(
            window,
            text=self._conflict_action_label(ConflictAction.KEEP_BOTH),
            command=lambda: choose(ConflictAction.KEEP_BOTH),
        ).pack(fill=X, padx=12, pady=4)
        ttk.Button(
            window,
            text=self._conflict_action_label(ConflictAction.SKIP),
            command=lambda: choose(ConflictAction.SKIP),
        ).pack(fill=X, padx=12, pady=4)

    def start_sync(self) -> None:
        if self._warn_if_busy():
            return
        if self.plan is None:
            messagebox.showwarning(self._tr("dialog_no_scan_title"), self._tr("dialog_no_scan_message"))
            return
        local = self.local_root.get()
        phone = self.phone_root.get()
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
        self._run_background(lambda: self._sync_worker(Path(local), phone))

    def _sync_worker(self, local: Path, phone: str) -> None:
        if self.plan is None:
            return
        operations = build_operations_from_view(
            _build_row_views(self.plan), self.conflict_choices
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

            if self.sync_mode is SyncMode.PHONE:
                executor = SyncExecutor(adb=self.adb, local_root=local, phone_root=phone)
            else:
                executor = LocalSyncExecutor(left_root=local, right_root=Path(phone))
            try:
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
        push_key = "log_pushed" if self.sync_mode is SyncMode.PHONE else "log_copied_left_to_right"
        pull_key = "log_pulled" if self.sync_mode is SyncMode.PHONE else "log_copied_right_to_left"
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
        # New dual-pane widgets replace the old notebook+tabs.
        for label, key in self.pane_label_keys:
            label.configure(text=self._tr(key))
        for col, key in self.column_heading_keys:
            self.tree.heading(col, text=self._tr(key))
        for button, key in self.arrow_button_keys:
            button.configure(text=self._tr(key))
        for checkbox, key in self.checkbox_keys:
            checkbox.configure(text=self._tr(key))
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

    def _conflict_action_label(self, action: ConflictAction) -> str:
        if self.sync_mode is SyncMode.PHONE:
            return conflict_action_label(action, self.language)
        if action is ConflictAction.USE_PHONE:
            return self._tr("conflict_use_right")
        if action is ConflictAction.USE_LOCAL:
            return self._tr("conflict_use_left")
        return conflict_action_label(action, self.language)

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
            self.pane_label_keys = [
                (self.left_pane_header, "pane_left_header_phone"),
                (self.right_pane_header, "pane_right_header_phone"),
            ]
        else:
            self.check_device_button.configure(state="disabled")
            self.first_folder_label.configure(text=self._tr("label_left_folder"))
            self.second_folder_label.configure(text=self._tr("label_right_folder"))
            self.second_choose_button.configure(
                text=self._tr("button_choose"),
                command=self.choose_second_folder,
            )
            self.pane_label_keys = [
                (self.left_pane_header, "pane_left_header_hard_drive"),
                (self.right_pane_header, "pane_right_header_hard_drive"),
            ]
        # Apply the current pane labels now and re-bind keys so the next
        # language switch retitles them correctly.
        for label, key in self.pane_label_keys:
            label.configure(text=self._tr(key))

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
