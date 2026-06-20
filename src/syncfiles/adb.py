from __future__ import annotations

import shlex
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
            result = self._run([self.adb_path, "devices"], check=False)
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
        find_root = _find_root(phone_path)
        result = self._run(
            [self.adb_path, "shell", "find", find_root, "-maxdepth", "1", "-mindepth", "1", "-type", "d"],
            check=True,
        )
        return sorted(line.strip() for line in result.stdout.splitlines() if line.strip())

    def scan_phone_folder(self, phone_root: str) -> list[FileRecord]:
        find_root = _find_root(phone_root)
        result = self._run([self.adb_path, "shell", _scan_files_command(find_root)], check=True)
        records: list[FileRecord] = []
        for line in result.stdout.splitlines():
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
        self._run([self.adb_path, "push", local_path, phone_path], check=True)

    def pull(self, phone_path: str, local_path: str) -> None:
        self._run([self.adb_path, "pull", phone_path, local_path], check=True)

    def _run(self, command: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        return self.runner(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=check,
        )


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
