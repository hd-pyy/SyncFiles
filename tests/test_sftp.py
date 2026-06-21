from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path

import pytest

from syncfiles.domain import SourceSide
from syncfiles.sftp import (
    SftpClient,
    SftpConnectionConfig,
    SftpError,
    SftpSession,
    join_remote_path,
    normalize_remote_path,
)


@dataclass
class FakeAttrs:
    filename: str
    st_mode: int
    st_size: int = 0
    st_mtime: int = 0


class FakeSsh:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeSftp:
    def __init__(self, listings: dict[str, list[FakeAttrs]] | None = None) -> None:
        self.listings = listings or {}
        self.existing: set[str] = set(self.listings)
        self.mkdirs: list[str] = []
        self.puts: list[tuple[str, str]] = []
        self.gets: list[tuple[str, str]] = []
        self.closed = False

    def listdir_attr(self, path: str) -> list[FakeAttrs]:
        return self.listings[path]

    def stat(self, path: str) -> object:
        if path not in self.existing:
            raise FileNotFoundError(path)
        return object()

    def mkdir(self, path: str) -> None:
        self.existing.add(path)
        self.mkdirs.append(path)

    def put(self, local_path: str, remote_path: str) -> None:
        self.puts.append((local_path, remote_path))

    def get(self, remote_path: str, local_path: str) -> None:
        self.gets.append((remote_path, local_path))

    def close(self) -> None:
        self.closed = True


def directory(name: str) -> FakeAttrs:
    return FakeAttrs(filename=name, st_mode=stat.S_IFDIR | 0o755)


def file(name: str, size: int, modified_time: int) -> FakeAttrs:
    return FakeAttrs(
        filename=name,
        st_mode=stat.S_IFREG | 0o644,
        st_size=size,
        st_mtime=modified_time,
    )


def test_normalizes_and_joins_remote_paths() -> None:
    assert normalize_remote_path("") == "."
    assert normalize_remote_path("/remote//photos/") == "/remote/photos"
    assert join_remote_path("/remote/photos", "nested/a.jpg") == "/remote/photos/nested/a.jpg"


def test_scan_folder_recurses_and_returns_phone_side_records() -> None:
    sftp = FakeSftp(
        {
            "/remote": [directory("docs"), file("root.txt", 4, 10)],
            "/remote/docs": [file("a.txt", 3, 20)],
        }
    )

    records = SftpSession(FakeSsh(), sftp).scan_folder("/remote")

    assert [(record.relative_path, record.size, record.modified_time, record.side) for record in records] == [
        ("docs/a.txt", 3, 20, SourceSide.PHONE),
        ("root.txt", 4, 10, SourceSide.PHONE),
    ]


def test_upload_file_creates_missing_remote_parents_before_put(tmp_path: Path) -> None:
    local_file = tmp_path / "a.txt"
    local_file.write_text("hello", encoding="utf-8")
    sftp = FakeSftp()
    sftp.existing.add("/remote")

    SftpSession(FakeSsh(), sftp).upload_file(local_file, "/remote/nested/deep/a.txt")

    assert sftp.mkdirs == ["/remote/nested", "/remote/nested/deep"]
    assert sftp.puts == [(str(local_file), "/remote/nested/deep/a.txt")]


def test_download_file_creates_local_parent_before_get(tmp_path: Path) -> None:
    sftp = FakeSftp()
    destination = tmp_path / "nested" / "a.txt"

    SftpSession(FakeSsh(), sftp).download_file("/remote/a.txt", destination)

    assert destination.parent.is_dir()
    assert sftp.gets == [("/remote/a.txt", str(destination))]


def test_session_context_manager_closes_sftp_and_ssh() -> None:
    ssh = FakeSsh()
    sftp = FakeSftp()

    with SftpSession(ssh, sftp):
        pass

    assert sftp.closed is True
    assert ssh.closed is True


def test_client_wraps_connection_failure_as_sftp_error() -> None:
    class FailingSsh:
        def set_missing_host_key_policy(self, _policy: object) -> None:
            pass

        def connect(self, **_kwargs: object) -> None:
            raise RuntimeError("bad password")

        def close(self) -> None:
            pass

    client = SftpClient(ssh_factory=FailingSsh)

    with pytest.raises(SftpError, match="SFTP connection failed: bad password"):
        client.connect(SftpConnectionConfig("example.com", 22, "alice", "secret"))
