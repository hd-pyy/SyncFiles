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
