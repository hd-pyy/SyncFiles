# Chinese Language Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a clickable Chinese/English language selector to the SyncFiles desktop app, with Chinese as the default language.

**Architecture:** Add a focused `syncfiles.i18n` module containing language constants, translation lookup, and conflict action labels. Update `syncfiles.app` to register translatable widgets and refresh them when the selected language changes, without changing sync planning or copy behavior.

**Tech Stack:** Python 3.11+, Tkinter, pytest.

---

## Task 1: Translation Module

**Files:**
- Create: `src/syncfiles/i18n.py`
- Create: `tests/test_i18n.py`

- [ ] Write failing tests for default language, text lookup, and conflict action labels.
- [ ] Run `python -m pytest tests/test_i18n.py -v` and confirm it fails because `syncfiles.i18n` is missing.
- [ ] Implement `Language`, `DEFAULT_LANGUAGE`, `LANGUAGE_LABELS`, `text()`, and `conflict_action_label()`.
- [ ] Run `python -m pytest tests/test_i18n.py -v` and confirm the tests pass.
- [ ] Commit with `feat: add UI translations`.

## Task 2: App Language Selector

**Files:**
- Modify: `src/syncfiles/app.py`
- Modify: `tests/test_app.py`

- [ ] Write a failing test showing `build_operations_from_plan()` is unchanged and the app can import translation helpers.
- [ ] Run `python -m pytest tests/test_app.py -v` and confirm the new expectation fails.
- [ ] Add a `ttk.Combobox` language selector with `中文` and `English`, defaulting to Chinese.
- [ ] Register labels, buttons, tabs, dialogs, and log message calls through translation keys.
- [ ] Refresh visible text when the selected language changes.
- [ ] Run `python -m pytest -v` and confirm all tests pass.
- [ ] Run `python -c "import tkinter as tk; from syncfiles.app import SyncFilesApp; root=tk.Tk(); root.withdraw(); app=SyncFilesApp(root); print(root.title()); root.destroy()"` and confirm the title is `SyncFiles`.
- [ ] Commit with `feat: add desktop language selector`.

## Task 3: Documentation and Verification

**Files:**
- Modify: `README.md`

- [ ] Update README workflow to mention the language selector and Chinese default.
- [ ] Run `python -m pytest -v`.
- [ ] Run `python -c "from syncfiles.i18n import DEFAULT_LANGUAGE, text; print(DEFAULT_LANGUAGE.value, text('button_scan', DEFAULT_LANGUAGE))"`.
- [ ] Commit with `docs: document language selector`.

## Self-Review

Spec coverage:

- Chinese default: Task 1 and Task 2.
- Clickable language selector: Task 2.
- English retained: Task 1 and Task 2.
- Sync logic unchanged: Task 2 regression test and full suite.

Completion-marker scan:

- No unfilled implementation steps remain.

Type consistency:

- `Language` is introduced in Task 1 and reused in Task 2 and Task 3.
