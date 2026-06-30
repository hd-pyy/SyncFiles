from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Callable

from syncfiles.domain import AdbError, FileRecord, SourceSide

Runner = Callable[..., subprocess.CompletedProcess[bytes]]

# Single module-level logger. Handlers are wired up in configure_logging()
# (called from app startup) so output goes to stderr and survives the
# console=False frozen exe — no print() which would be discarded there.
_LOG = logging.getLogger("syncfiles.adb")


def configure_logging() -> None:
    """Idempotently attach a stderr handler.

    Safe to call multiple times. Honours ``SYNCFILES_ADB_DEBUG=1`` for
    DEBUG-level output (full stdout/stderr on every invocation). Default
    level is INFO so a single ``[adb] ... → rc=N`` line per command is
    enough to diagnose failures without scrolling.
    """
    if _LOG.handlers:
        return
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _LOG.addHandler(handler)
    _LOG.setLevel(logging.DEBUG if os.environ.get("SYNCFILES_ADB_DEBUG") else logging.INFO)
    _LOG.propagate = False


def resolve_adb_path() -> str:
    """Return the adb executable to invoke, probing in this order:

    1. ``SYNCFILES_ADB`` environment variable (escape hatch for power users).
    2. ``adb`` on ``PATH`` — honors a system / user-installed copy and lets
       users upgrade adb independently of the bundled exe.
    3. ``adb`` / ``adb.exe`` sitting next to the running executable — handy
       for a portable drop-in: copy a newer platform-tools here to override.
    4. The bundled fallback shipped inside the package / frozen exe.
    """
    override = os.environ.get("SYNCFILES_ADB")
    if override:
        return override

    on_path = shutil.which("adb")
    if on_path:
        return on_path

    executable_dir = Path(getattr(sys, "executable", __file__)).resolve().parent
    for candidate in ("adb.exe", "adb"):
        sibling = executable_dir / candidate
        if sibling.is_file():
            return str(sibling)

    bundled_name = "adb.exe" if os.name == "nt" else "adb"
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is not None:
        bundled = Path(meipass) / "adb_fallback" / bundled_name
        if bundled.is_file():
            return str(bundled)
    bundled = Path(__file__).resolve().parent / "adb_fallback" / bundled_name
    if bundled.is_file():
        return str(bundled)

    return "adb"


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
    def __init__(self, adb_path: str | None = None, runner: Runner = subprocess.run) -> None:
        self.adb_path = adb_path if adb_path is not None else resolve_adb_path()
        self.runner = runner

    def prewarm_server(self) -> None:
        """Best-effort: wake up the adb server daemon so the first real call
        doesn't pay the ~5s cold-start tax on a machine that has never run
        adb. Safe to call from any thread; ignores all errors because the
        real adb call right after this will surface the actual failure.
        """
        if self.adb_path == "adb" and shutil.which("adb") is None and self.runner is subprocess.run:
            return
        try:
            self._run([self.adb_path, "start-server"], check=False)
        except (FileNotFoundError, OSError):
            pass

    def get_device_status(self) -> DeviceStatus:
        try:
            result = self._run([self.adb_path, "devices"], check=False)
        except FileNotFoundError:
            return DeviceStatus(DeviceState.ADB_MISSING, "ADB is not installed or not on PATH.")

        stderr_text = _decode(result.stderr)
        stdout_text = _decode(result.stdout)

        if result.returncode != 0:
            detail = stderr_text.strip() or stdout_text.strip() or "ADB returned an error."
            return DeviceStatus(DeviceState.ERROR, detail)

        devices = _parse_devices(stdout_text)
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
        find_root = _find_root(phone_path)
        try:
            result = self._run(
                [self.adb_path, "shell", "find", find_root, "-maxdepth", "1", "-mindepth", "1", "-type", "d"],
                check=True,
            )
        except FileNotFoundError as exc:
            raise AdbError("ADB is not installed or not on PATH.") from exc
        except subprocess.CalledProcessError as exc:
            raise AdbError(
                f"Failed to list directories under {phone_path}.",
                detail=_decode(exc.stderr) or _decode(exc.stdout),
            ) from exc
        return sorted(line.strip() for line in _decode(result.stdout).splitlines() if line.strip())

    def scan_phone_folder(self, phone_root: str) -> list[FileRecord]:
        find_root = _find_root(phone_root)
        try:
            result = self._run([self.adb_path, "shell", _scan_files_command(find_root)], check=True)
        except FileNotFoundError as exc:
            raise AdbError("ADB is not installed or not on PATH.") from exc
        except subprocess.CalledProcessError as exc:
            raise AdbError(
                f"Failed to scan {phone_root} on the phone.",
                detail=_decode(exc.stderr) or _decode(exc.stdout),
            ) from exc
        records: list[FileRecord] = []
        for line in _decode(result.stdout).splitlines():
            if not line.strip():
                continue
            absolute_path, size, modified = line.rsplit("\t", 2)
            records.append(
                FileRecord(
                    relative_path=_relative_phone_path(absolute_path, find_root),
                    size=int(size),
                    modified_time=int(float(modified)),
                    side=SourceSide.PHONE,
                )
            )
        return sorted(records, key=lambda record: record.relative_path)

    def push(self, local_path: str, phone_path: str) -> None:
        try:
            self._run([self.adb_path, "push", local_path, phone_path], check=True)
        except FileNotFoundError as exc:
            raise AdbError("ADB is not installed or not on PATH.") from exc
        except subprocess.CalledProcessError as exc:
            raise AdbError(
                f"Failed to push {local_path} to {phone_path}.",
                detail=_decode(exc.stderr) or _decode(exc.stdout),
            ) from exc

    def pull(self, phone_path: str, local_path: str) -> None:
        try:
            self._run([self.adb_path, "pull", phone_path, local_path], check=True)
        except FileNotFoundError as exc:
            raise AdbError("ADB is not installed or not on PATH.") from exc
        except subprocess.CalledProcessError as exc:
            raise AdbError(
                f"Failed to pull {phone_path} to {local_path}.",
                detail=_decode(exc.stderr) or _decode(exc.stdout),
            ) from exc

    def _run(self, command: list[str], *, check: bool) -> subprocess.CompletedProcess[bytes]:
        kwargs: dict[str, object] = {
            "capture_output": True,
            "text": False,
            "check": check,
        }
        # Suppress the black cmd window that would otherwise flash whenever
        # we spawn the console-subsystem adb.exe from a windowed (GUI) app.
        # Gated on the real subprocess.run runner so test doubles that don't
        # accept creationflags keep working unchanged.
        if os.name == "nt" and self.runner is subprocess.run:
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        _LOG.info("[adb] %s", _format_command(command))

        # Retry transient "more than one device" errors. Some Windows hosts
        # have a process (Android Studio emulator probe, a phone vendor
        # helper, etc.) that briefly registers an offline ghost device and
        # then disappears within ~1s. adb can't pick a target while both
        # are visible, so pull/push fail. A short retry lets the ghost
        # vanish without bothering the user. Only applies to commands
        # without an explicit -s serial — those would never see this error.
        retryable = "-s" not in command
        attempts = 1 if not retryable else _GHOST_RETRY_ATTEMPTS
        delay = _GHOST_RETRY_DELAY
        last_exc: subprocess.CalledProcessError | None = None
        for attempt in range(1, attempts + 1):
            try:
                result = self.runner(command, **kwargs)
            except subprocess.CalledProcessError as exc:
                last_exc = exc
                stderr_text = _decode(exc.stderr).strip()
                if attempt < attempts and _is_ghost_device_error(stderr_text):
                    _LOG.info(
                        "[adb] %s → rc=%d (ghost device, retry %d/%d in %.1fs)\n  stderr: %s",
                        _format_command(command),
                        exc.returncode,
                        attempt,
                        attempts - 1,
                        delay,
                        stderr_text,
                    )
                    import time
                    time.sleep(delay)
                    continue
                _LOG.info(
                    "[adb] %s → rc=%d\n  stderr: %s\n  stdout: %s",
                    _format_command(command),
                    exc.returncode,
                    stderr_text,
                    _decode(exc.stdout).strip(),
                )
                raise
            else:
                if _LOG.isEnabledFor(logging.DEBUG) or result.returncode != 0:
                    _LOG.info(
                        "[adb] %s → rc=%d\n  stderr: %s\n  stdout: %s",
                        _format_command(command),
                        result.returncode,
                        _decode(result.stderr).strip(),
                        _decode(result.stdout).strip(),
                    )
                else:
                    _LOG.info("[adb] %s → rc=%d", _format_command(command), result.returncode)
                return result
        # All retries exhausted
        assert last_exc is not None
        raise last_exc


def _format_command(command: list[str]) -> str:
    """Render a command list as a single shell-friendly string for logs.

    We don't actually invoke a shell — this is purely for readability in
    the terminal. Paths with spaces or Chinese characters are quoted so a
    copy/paste from the log reproduces the original argv verbatim.
    """
    parts: list[str] = []
    for arg in command:
        if arg and not any(ch.isspace() or ch == '"' for ch in arg):
            parts.append(arg)
        else:
            parts.append(shlex.quote(arg))
    return " ".join(parts)


_GHOST_RETRY_ATTEMPTS = 4  # initial + 3 retries → ~1.2s total worst case
_GHOST_RETRY_DELAY = 0.4
_GHOST_ERROR_FRAGMENTS = (
    "more than one device",
    "more than one emulator",
    "failed to get feature set",
)


def _is_ghost_device_error(stderr_text: str) -> bool:
    """True iff adb's stderr looks like a transient multi-device blip.

    adb prints slightly different phrasing across versions ("failed to
    get feature set", "more than one device/emulator", "device offline"
    races) but they all collapse to the same root cause: a ghost device
    that adb will drop within a second or two.
    """
    lowered = stderr_text.lower()
    return any(fragment in lowered for fragment in _GHOST_ERROR_FRAGMENTS)


def _decode(data: bytes | str | None) -> str:
    """Best-effort decode of adb output.

    adb on Windows emits GBK when the shell has Chinese filenames; using
    UTF-8 here would raise ``UnicodeDecodeError`` and lose the real
    message. ``errors='replace'`` keeps the UI legible without crashing.
    """
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    return data.decode("utf-8", errors="replace")


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


def _find_root(phone_path: str) -> str:
    stripped = phone_path.rstrip("/")
    if not stripped:
        return "/"
    return f"{stripped}/"


def _scan_files_command(find_root: str) -> str:
    stat_format = "%n\t%s\t%Y"
    return (
        f"find {shlex.quote(find_root)} -type f -exec "
        f"stat -c {shlex.quote(stat_format)} {{}} \\; 2>/dev/null"
    )


def _relative_phone_path(absolute_path: str, find_root: str) -> str:
    normalized_path = absolute_path.replace("\\", "/")
    normalized_root = find_root.rstrip("/") + "/"
    if normalized_path.startswith(normalized_root):
        return normalized_path[len(normalized_root) :]
    return normalized_path.lstrip("/")
