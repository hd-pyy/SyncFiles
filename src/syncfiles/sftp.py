from __future__ import annotations

import posixpath
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from syncfiles.domain import FileRecord, OperationCancelled, SourceSide
from syncfiles.local_fs import ensure_parent_directory


class SftpError(Exception):
    """Raised when an SFTP connection or transfer fails."""


@dataclass(frozen=True, slots=True)
class SftpConnectionConfig:
    host: str
    port: int
    username: str
    password: str


class SftpAttrs(Protocol):
    filename: str
    st_mode: int
    st_size: int
    st_mtime: int


class SftpHandle(Protocol):
    def listdir_attr(self, path: str) -> list[SftpAttrs]:
        ...

    def stat(self, path: str) -> object:
        ...

    def mkdir(self, path: str) -> None:
        ...

    def put(self, local_path: str, remote_path: str) -> None:
        ...

    def get(self, remote_path: str, local_path: str) -> None:
        ...

    def close(self) -> None:
        ...


class SshHandle(Protocol):
    def close(self) -> None:
        ...


def normalize_remote_path(path: str) -> str:
    cleaned = path.strip().replace("\\", "/")
    if not cleaned:
        return "."
    normalized = posixpath.normpath(cleaned)
    if cleaned.startswith("/") and not normalized.startswith("/"):
        return f"/{normalized}"
    return normalized


def join_remote_path(root: str, relative_path: str) -> str:
    normalized_root = normalize_remote_path(root)
    normalized_relative = relative_path.replace("\\", "/").strip("/")
    if normalized_root == ".":
        return normalize_remote_path(normalized_relative)
    return normalize_remote_path(posixpath.join(normalized_root, normalized_relative))


class SftpSession:
    def __init__(self, ssh: SshHandle, sftp: SftpHandle) -> None:
        self.ssh = ssh
        self.sftp = sftp

    def __enter__(self) -> SftpSession:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()

    def close(self) -> None:
        self.sftp.close()
        self.ssh.close()

    def scan_folder(
        self,
        remote_root: str,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> list[FileRecord]:
        root = normalize_remote_path(remote_root)
        records: list[FileRecord] = []

        def visit(directory: str, relative_prefix: str) -> None:
            for attrs in sorted(self.sftp.listdir_attr(directory), key=lambda item: item.filename):
                if is_cancelled is not None and is_cancelled():
                    raise OperationCancelled
                if attrs.filename in (".", ".."):
                    continue
                remote_path = join_remote_path(directory, attrs.filename)
                relative_path = "/".join(part for part in (relative_prefix, attrs.filename) if part)
                if stat.S_ISDIR(attrs.st_mode):
                    visit(remote_path, relative_path)
                elif stat.S_ISREG(attrs.st_mode):
                    records.append(
                        FileRecord(
                            relative_path=relative_path,
                            size=int(attrs.st_size),
                            modified_time=int(attrs.st_mtime),
                            side=SourceSide.PHONE,
                        )
                    )

        visit(root, "")
        return records

    def download_file(self, remote_path: str, local_path: Path) -> None:
        ensure_parent_directory(local_path)
        self.sftp.get(normalize_remote_path(remote_path), str(local_path))

    def upload_file(self, local_path: Path, remote_path: str) -> None:
        normalized_remote = normalize_remote_path(remote_path)
        self._ensure_remote_parent(normalized_remote)
        self.sftp.put(str(local_path), normalized_remote)

    def _ensure_remote_parent(self, remote_path: str) -> None:
        parent = posixpath.dirname(remote_path)
        if parent in ("", "."):
            return
        current = "/" if parent.startswith("/") else "."
        for part in parent.strip("/").split("/"):
            current = join_remote_path(current, part)
            try:
                self.sftp.stat(current)
            except OSError:
                self.sftp.mkdir(current)


class SftpClient:
    def __init__(self, ssh_factory: Callable[[], object] | None = None, timeout_seconds: float = 15.0) -> None:
        self.ssh_factory = ssh_factory
        self.timeout_seconds = timeout_seconds

    def connect(self, config: SftpConnectionConfig) -> SftpSession:
        ssh = None
        try:
            if self.ssh_factory is None:
                import paramiko

                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            else:
                ssh = self.ssh_factory()
            ssh.connect(
                hostname=config.host,
                port=config.port,
                username=config.username,
                password=config.password,
                timeout=self.timeout_seconds,
                look_for_keys=False,
                allow_agent=False,
            )
            return SftpSession(ssh, ssh.open_sftp())
        except Exception as exc:
            if ssh is not None:
                try:
                    ssh.close()
                except Exception:
                    pass
            raise SftpError(f"SFTP connection failed: {exc}") from exc
