# SyncFiles

SyncFiles is a Windows-first desktop helper for synchronizing one Android ADB folder with one local or external hard drive folder. The app starts in Chinese by default and can be switched between `中文` and `English` from the main window.

It performs bidirectional fill-in synchronization:

- Files missing on the hard drive are copied from the phone.
- Files missing on the phone are copied from the hard drive.
- Same-path files with different size or modified time are shown as conflicts.
- Deletions are not propagated.

## Requirements

- Python 3.11 or newer.
- Android Platform Tools with `adb` available on `PATH`.
- Android phone with USB debugging enabled and authorized.

## Development

```powershell
python -m pip install -e .[dev]
python -m pytest
python -m syncfiles
```

## Basic Workflow

1. Connect the Android phone through USB.
2. Open SyncFiles with `python -m syncfiles`.
3. Use the language selector if you want to switch between `中文` and `English`.
4. Click **检查设备** / **Check device**.
5. Choose the hard drive folder with the folder picker.
6. Browse the phone folder from `/sdcard`.
7. Scan differences.
8. Double-click conflicts and choose an action.
9. Start sync after reviewing the preview.
