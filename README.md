# SyncFiles

SyncFiles is a Windows-first desktop helper for synchronizing two folders. It supports:

- Hard drive <-> hard drive.
- Hard drive <-> Android phone through ADB.

The app starts in Chinese by default and can be switched between `中文` and `English` from the main window.

It performs bidirectional fill-in synchronization:

- Files missing on one side are copied from the other side.
- Same-path files with different size or modified time are shown as conflicts.
- Deletions are not propagated.

## Requirements

- Python 3.11 or newer.
- Android Platform Tools with `adb` available on `PATH` for phone sync mode.
- Android phone with USB debugging enabled and authorized for phone sync mode.

## Development

```powershell
python -m pip install -e .[dev]
python -m pytest
python -m syncfiles
```

## Basic Workflow

1. Connect the Android phone through USB when using phone sync mode.
2. Open SyncFiles with `python -m syncfiles`.
3. Use the language selector if you want to switch between `中文` and `English`.
4. Choose the sync mode.
5. In hard-drive mode, choose the left and right hard drive folders.
6. In phone mode, click **检查设备** / **Check device**, choose the hard drive folder, and browse the phone folder from `/sdcard`.
7. Scan differences.
8. Double-click conflicts and choose an action.
9. Start sync after reviewing the preview.

## Building a Windows Distribution

The packaged exe bundles `adb.exe` and its required DLLs, so end users do not
need to install platform-tools.

1. Stage the ADB fallback (one-time, not checked in):

   ```powershell
   mkdir src\syncfiles\adb_fallback
   copy "<path-to-platform-tools>\adb.exe"              src\syncfiles\adb_fallback\
   copy "<path-to-platform-tools>\AdbWinApi.dll"        src\syncfiles\adb_fallback\
   copy "<path-to-platform-tools>\AdbWinUsbApi.dll"     src\syncfiles\adb_fallback\
   copy "<path-to-platform-tools>\libwinpthread-1.dll"  src\syncfiles\adb_fallback\
   ```

2. Build:

   ```powershell
   .venv\Scripts\python -m pip install pyinstaller
   .venv\Scripts\pyinstaller --noconfirm SyncFiles.spec
   ```

3. The result is a single `dist\SyncFiles.exe` (~20 MB) that contains
   Python, Tcl/Tk, the syncfiles package, and `adb.exe` plus its DLLs.
   Ship just that one file to end users.

At runtime `syncfiles.adb.resolve_adb_path` looks for adb in this order:
`SYNCFILES_ADB` env var → `PATH` → adb next to the exe → bundled fallback.
