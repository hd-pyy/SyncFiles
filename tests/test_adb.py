import subprocess

from syncfiles.adb import AdbClient, DeviceState
from syncfiles.domain import SourceSide


class FakeRunner:
    def __init__(self, outputs: dict[tuple[str, ...], subprocess.CompletedProcess[str]]) -> None:
        self.outputs = outputs
        self.calls: list[tuple[str, ...]] = []

    def __call__(
        self,
        command: list[str],
        *,
        capture_output: bool = True,
        text: bool = True,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        key = tuple(command)
        self.calls.append(key)
        result = self.outputs[key]
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, command, result.stdout, result.stderr)
        return result


def completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_classifies_one_authorized_device_as_ready() -> None:
    runner = FakeRunner({("adb", "devices"): completed("List of devices attached\nabc123\tdevice\n")})

    status = AdbClient(runner=runner).get_device_status()

    assert status.state is DeviceState.READY
    assert status.serial == "abc123"


def test_classifies_unauthorized_device() -> None:
    runner = FakeRunner({("adb", "devices"): completed("List of devices attached\nabc123\tunauthorized\n")})

    status = AdbClient(runner=runner).get_device_status()

    assert status.state is DeviceState.UNAUTHORIZED


def test_lists_phone_directories() -> None:
    runner = FakeRunner(
        {
            (
                "adb",
                "shell",
                "find",
                "/sdcard",
                "-maxdepth",
                "1",
                "-mindepth",
                "1",
                "-type",
                "d",
            ): completed("/sdcard/DCIM\n/sdcard/Documents\n")
        }
    )

    directories = AdbClient(runner=runner).list_directories("/sdcard")

    assert directories == ["/sdcard/DCIM", "/sdcard/Documents"]


def test_scans_phone_folder_to_file_records() -> None:
    runner = FakeRunner(
        {
            (
                "adb",
                "shell",
                "find",
                "/sdcard/Test",
                "-type",
                "f",
                "-printf",
                "%P\t%s\t%T@\n",
            ): completed("a.txt\t3\t1710000000.0\nnested/b.jpg\t5\t1710000001.0\n")
        }
    )

    records = AdbClient(runner=runner).scan_phone_folder("/sdcard/Test")

    assert [record.relative_path for record in records] == ["a.txt", "nested/b.jpg"]
    assert records[0].size == 3
    assert records[0].modified_time == 1710000000
    assert records[0].side is SourceSide.PHONE


def test_push_and_pull_call_adb_with_expected_paths() -> None:
    runner = FakeRunner(
        {
            ("adb", "push", "C:/local/a.txt", "/sdcard/Test/a.txt"): completed(),
            ("adb", "pull", "/sdcard/Test/b.txt", "D:/Backup/b.txt"): completed(),
        }
    )
    client = AdbClient(runner=runner)

    client.push("C:/local/a.txt", "/sdcard/Test/a.txt")
    client.pull("/sdcard/Test/b.txt", "D:/Backup/b.txt")

    assert ("adb", "push", "C:/local/a.txt", "/sdcard/Test/a.txt") in runner.calls
    assert ("adb", "pull", "/sdcard/Test/b.txt", "D:/Backup/b.txt") in runner.calls
