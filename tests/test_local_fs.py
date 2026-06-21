from pathlib import Path

from syncfiles.domain import SourceSide
from syncfiles.local_fs import copy_local_file, ensure_parent_directory, scan_local_folder


def test_scans_local_folder_with_relative_paths(tmp_path: Path) -> None:
    root = tmp_path / "root"
    nested = root / "photos"
    nested.mkdir(parents=True)
    file_path = nested / "a.jpg"
    file_path.write_bytes(b"abc")

    records = scan_local_folder(root)

    assert len(records) == 1
    assert records[0].relative_path == "photos/a.jpg"
    assert records[0].size == 3
    assert records[0].side is SourceSide.LOCAL
    assert records[0].modified_time > 0


def test_scan_local_folder_ignores_directories(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "empty").mkdir(parents=True)

    assert scan_local_folder(root) == []


def test_ensure_parent_directory_creates_nested_destination(tmp_path: Path) -> None:
    destination = tmp_path / "root" / "nested" / "file.txt"

    ensure_parent_directory(destination)

    assert destination.parent.is_dir()


def test_copy_local_file_creates_parent_and_copies_bytes(tmp_path: Path) -> None:
    source = tmp_path / "source" / "photos" / "a.jpg"
    destination = tmp_path / "dest" / "nested" / "a.jpg"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"image-bytes")

    copy_local_file(source, destination)

    assert destination.read_bytes() == b"image-bytes"
