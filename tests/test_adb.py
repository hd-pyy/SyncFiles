import subprocess

import pytest

from syncfiles.adb import AdbClient, DeviceState
from syncfiles.domain import AdbError, SourceSide


class FakeRunner:
    def __init__(self, outputs: dict[tuple[str, ...], subprocess.CompletedProcess[str]]) -> None:
        self.outputs = outputs
        self.calls: list[tuple[str, ...]] = []
        self.call_kwargs: list[dict[str, object]] = []

    def __call__(
        self,
        command: list[str],
        *,
        capture_output: bool = True,
        text: bool = True,
        encoding: str | None = None,
        errors: str | None = None,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        key = tuple(command)
        self.calls.append(key)
        self.call_kwargs.append(
            {
                "capture_output": capture_output,
                "text": text,
                "encoding": encoding,
                "errors": errors,
                "check": check,
            }
        )
        result = self.outputs[key]
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, command, result.stdout, result.stderr)
        return result


def completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_classifies_one_authorized_device_as_ready() -> None:
    runner = FakeRunner({("adb", "devices"): completed("List of devices attached\nabc123\tdevice\n")})

    status = AdbClient(adb_path="adb", runner=runner).get_device_status()

    assert status.state is DeviceState.READY
    assert status.serial == "abc123"


def test_classifies_unauthorized_device() -> None:
    runner = FakeRunner({("adb", "devices"): completed("List of devices attached\nabc123\tunauthorized\n")})

    status = AdbClient(adb_path="adb", runner=runner).get_device_status()

    assert status.state is DeviceState.UNAUTHORIZED


def test_lists_phone_directories() -> None:
    runner = FakeRunner(
        {
            (
                "adb",
                "shell",
                "find",
                "/sdcard/",
                "-maxdepth",
                "1",
                "-mindepth",
                "1",
                "-type",
                "d",
            ): completed("/sdcard/DCIM\n/sdcard/Documents\n")
        }
    )

    directories = AdbClient(adb_path="adb", runner=runner).list_directories("/sdcard")

    assert directories == ["/sdcard/DCIM", "/sdcard/Documents"]


def test_lists_phone_directories_with_trailing_slash_for_symlink_roots() -> None:
    runner = FakeRunner(
        {
            (
                "adb",
                "shell",
                "find",
                "/sdcard/",
                "-maxdepth",
                "1",
                "-mindepth",
                "1",
                "-type",
                "d",
            ): completed("/sdcard/DCIM\n")
        }
    )

    directories = AdbClient(adb_path="adb", runner=runner).list_directories("/sdcard")

    assert directories == ["/sdcard/DCIM"]


def test_adb_commands_request_utf8_decoding() -> None:
    runner = FakeRunner(
        {
            (
                "adb",
                "shell",
                "find",
                "/sdcard/",
                "-maxdepth",
                "1",
                "-mindepth",
                "1",
                "-type",
                "d",
            ): completed("/sdcard/黄鸟工具包\n")
        }
    )

    directories = AdbClient(adb_path="adb", runner=runner).list_directories("/sdcard")

    assert directories == ["/sdcard/黄鸟工具包"]
    # adb on Windows emits GBK when filenames contain non-ASCII; we ask
    # subprocess for raw bytes and decode ourselves so a GBK blob doesn't
    # blow up with UnicodeDecodeError.
    assert runner.call_kwargs[0]["text"] is False
    assert runner.call_kwargs[0].get("encoding") is None


def test_scans_phone_folder_to_file_records() -> None:
    scan_command = (
        "find /sdcard/Test/ -type f -exec "
        "stat -c '%n\t%s\t%Y' {} \\; 2>/dev/null"
    )
    runner = FakeRunner(
        {
            (
                "adb",
                "shell",
                scan_command,
            ): completed(
                "/sdcard/Test/a.txt\t3\t1710000000\n"
                "/sdcard/Test/nested/file with spaces.jpg\t5\t1710000001\n"
            )
        }
    )

    records = AdbClient(adb_path="adb", runner=runner).scan_phone_folder("/sdcard/Test")

    assert [record.relative_path for record in records] == ["a.txt", "nested/file with spaces.jpg"]
    assert records[0].size == 3
    assert records[0].modified_time == 1710000000
    assert records[0].side is SourceSide.PHONE
    assert "-printf" not in runner.calls[0]


def test_push_and_pull_call_adb_with_expected_paths() -> None:
    runner = FakeRunner(
        {
            ("adb", "push", "C:/local/a.txt", "/sdcard/Test/a.txt"): completed(),
            ("adb", "pull", "/sdcard/Test/b.txt", "D:/Backup/b.txt"): completed(),
        }
    )
    client = AdbClient(adb_path="adb", runner=runner)

    client.push("C:/local/a.txt", "/sdcard/Test/a.txt")
    client.pull("/sdcard/Test/b.txt", "D:/Backup/b.txt")

    assert ("adb", "push", "C:/local/a.txt", "/sdcard/Test/a.txt") in runner.calls
    assert ("adb", "pull", "/sdcard/Test/b.txt", "D:/Backup/b.txt") in runner.calls


class _MissingAdbRunner:
    """Pretends the adb binary doesn't exist on disk."""

    calls: list[list[str]] = []

    def __call__(self, command, **_kwargs):
        self.calls.append(list(command))
        raise FileNotFoundError(2, "The system cannot find the file specified", command[0])


def test_list_directories_raises_adb_error_when_binary_missing() -> None:
    runner = _MissingAdbRunner()
    with pytest.raises(AdbError, match="ADB is not installed"):
        AdbClient(adb_path="/does/not/exist/adb.exe", runner=runner).list_directories("/sdcard")


def test_scan_phone_folder_raises_adb_error_when_binary_missing() -> None:
    runner = _MissingAdbRunner()
    with pytest.raises(AdbError, match="ADB is not installed"):
        AdbClient(adb_path="/does/not/exist/adb.exe", runner=runner).scan_phone_folder("/sdcard/Test")


def test_push_raises_adb_error_when_binary_missing() -> None:
    runner = _MissingAdbRunner()
    with pytest.raises(AdbError, match="ADB is not installed"):
        AdbClient(adb_path="/does/not/exist/adb.exe", runner=runner).push("C:/local/a.txt", "/sdcard/Test/a.txt")


def test_pull_raises_adb_error_when_binary_missing() -> None:
    runner = _MissingAdbRunner()
    with pytest.raises(AdbError, match="ADB is not installed"):
        AdbClient(adb_path="/does/not/exist/adb.exe", runner=runner).pull("/sdcard/Test/b.txt", "D:/Backup/b.txt")


def test_pull_retries_transient_ghost_device_error() -> None:
    """First invocation fails with the ghost-device blip; the retry succeeds."""
    class _QueueRunner:
        def __init__(self, results: list[subprocess.CompletedProcess[str]]) -> None:
            self.results = results
            self.calls = 0

        def __call__(
            self,
            command: list[str],
            *,
            capture_output: bool = True,
            text: bool = True,
            encoding: str | None = None,
            errors: str | None = None,
            check: bool = False,
        ) -> subprocess.CompletedProcess[str]:
            self.calls += 1
            result = self.results.pop(0)
            if check and result.returncode != 0:
                raise subprocess.CalledProcessError(result.returncode, command, result.stdout, result.stderr)
            return result

    queue_runner = _QueueRunner(
        [
            completed(stderr="adb: error: failed to get feature set: more than one device/emulator", returncode=1),
            completed(returncode=0),
        ]
    )
    import syncfiles.adb as adb_module
    original_delay = adb_module._GHOST_RETRY_DELAY
    adb_module._GHOST_RETRY_DELAY = 0.0
    try:
        AdbClient(adb_path="adb", runner=queue_runner).pull("/sdcard/Test/b.txt", "D:/Backup/b.txt")
    finally:
        adb_module._GHOST_RETRY_DELAY = original_delay
    assert queue_runner.calls == 2


def test_pull_surfaces_real_adb_error_after_ghost_retries_exhausted() -> None:
    """If every retry still says ghost-device, raise AdbError with the detail."""
    ghost = completed(stderr="adb: error: failed to get feature set: more than one device/emulator", returncode=1)

    class _AlwaysGhost:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(
            self,
            command: list[str],
            *,
            capture_output: bool = True,
            text: bool = True,
            encoding: str | None = None,
            errors: str | None = None,
            check: bool = False,
        ) -> subprocess.CompletedProcess[str]:
            self.calls += 1
            if check:
                raise subprocess.CalledProcessError(ghost.returncode, command, ghost.stdout, ghost.stderr)
            return ghost

    import syncfiles.adb as adb_module
    original_delay = adb_module._GHOST_RETRY_DELAY
    original_attempts = adb_module._GHOST_RETRY_ATTEMPTS
    adb_module._GHOST_RETRY_DELAY = 0.0
    adb_module._GHOST_RETRY_ATTEMPTS = 3  # initial + 2 retries → keeps test fast
    try:
        runner = _AlwaysGhost()
        with pytest.raises(AdbError) as info:
            AdbClient(adb_path="adb", runner=runner).pull("/sdcard/Test/b.txt", "D:/Backup/b.txt")
    finally:
        adb_module._GHOST_RETRY_DELAY = original_delay
        adb_module._GHOST_RETRY_ATTEMPTS = original_attempts
    assert "Failed to pull" in str(info.value)
    assert "more than one device" in str(info.value)
    assert runner.calls == 3
