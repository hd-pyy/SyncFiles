from __future__ import annotations

import tkinter as tk
from collections.abc import Iterator

import pytest


_real_tk = tk.Tk


@pytest.fixture(scope="session")
def tk_session_root() -> Iterator[tk.Tk]:
    root = _real_tk()
    root.withdraw()
    try:
        yield root
    finally:
        root.destroy()


@pytest.fixture(autouse=True)
def reuse_tk_root(monkeypatch: pytest.MonkeyPatch, tk_session_root: tk.Tk) -> None:
    def make_test_window(*_args: object, **_kwargs: object) -> tk.Toplevel:
        window = tk.Toplevel(tk_session_root)
        window.withdraw()
        return window

    monkeypatch.setattr(tk, "Tk", make_test_window)
