# SyncFiles Desktop Design

## Goal

Build a Windows-first desktop application that synchronizes two same-purpose folders by filling in files that are missing on either side. One folder lives on an Android phone connected through ADB, and the other lives on a local or external hard drive attached to the computer.

## First Version Scope

The first version supports Android phones through ADB only. It does not support iPhone, MTP automation, network sync, cloud storage, background services, or deletion propagation.

The desktop app lets the user pick both folders instead of typing paths:

- The hard drive folder is selected with the native Windows folder picker.
- The phone folder is selected with an in-app ADB directory browser that starts at `/sdcard`.

## Synchronization Model

SyncFiles compares the two selected folders by relative path.

If a file exists on the phone but not on the hard drive, it is listed as a phone-to-hard-drive copy.

If a file exists on the hard drive but not on the phone, it is listed as a hard-drive-to-phone copy.

If a file exists on both sides and the size or modified timestamp differs, it is listed as a conflict. The app never resolves conflicts automatically. The user chooses one action per conflict:

- Use phone version.
- Use hard drive version.
- Keep both by creating a conflict copy.
- Skip.

Deletion is intentionally not part of the sync model. If one side is missing a file, the app treats that as a file to copy from the other side, not as a deletion to propagate.

## User Experience

The main window contains:

- A phone connection status area showing whether ADB is missing, no device is connected, the device is unauthorized, multiple devices are connected, or exactly one authorized device is ready.
- A hard drive folder selector.
- A phone folder selector that opens the ADB directory browser.
- Buttons for checking the device, scanning differences, and starting synchronization.
- A preview area with three groups: phone-to-hard-drive copies, hard-drive-to-phone copies, and conflicts.
- A progress and log area for scan and copy operations.

The normal workflow is:

1. Connect and authorize the Android phone.
2. Select the hard drive folder.
3. Select the phone folder through the ADB directory browser.
4. Scan differences.
5. Review the preview.
6. Choose actions for conflicts.
7. Confirm and run the sync.
8. Review the final summary.

## Architecture

The app is a Python desktop application using Tkinter for the first version. Tkinter keeps the dependency footprint low, runs on the current Windows machine, and keeps the code portable enough for a Linux ADB version.

The code is split into focused modules:

- `syncfiles.domain`: file records, sync plans, conflict decisions, and pure comparison logic.
- `syncfiles.local_fs`: local hard drive scanning and local file operations.
- `syncfiles.adb`: ADB device detection, phone directory browsing, phone scanning, `adb pull`, and `adb push`.
- `syncfiles.app`: Tkinter windows, event handling, preview display, and progress reporting.
- `tests`: unit tests for comparison logic and fake ADB/local filesystem behavior.

The comparison logic is independent of Tkinter and ADB so it can be tested without a phone.

## Phone Connection Handling

The app checks for the `adb` executable before any phone operation. It then runs `adb devices` and classifies the result:

- ADB missing: show setup guidance.
- No device: ask the user to connect a phone and enable USB debugging.
- Unauthorized device: ask the user to accept the authorization prompt on the phone.
- Multiple devices: ask the user to connect only one device for the first version.
- One authorized device: allow browsing and scanning.

The phone browser lists directories with ADB shell commands and only allows selecting directories. The first version starts at `/sdcard` because it is the common user-accessible Android storage root.

## File Comparison

Each file record includes:

- Relative path.
- Size in bytes.
- Last modified timestamp where available.
- Source side: phone or hard drive.

For files present on both sides, the first comparison uses size and modified timestamp. If either value differs, the file is marked as a conflict and the user decides what to do. The first version does not automatically hash both sides to prove whether same-size files contain identical bytes.

For missing files, the app does not need a hash before copying.

## Error Handling

The app reports errors in user-facing language and logs technical details in the progress log. Common errors include:

- ADB is not installed or not on `PATH`.
- The phone disconnects during scanning or copying.
- The selected phone directory cannot be read.
- The hard drive folder is unavailable or write-protected.
- A file copy fails because the destination directory cannot be created.

Failed copy operations remain visible in the final summary. A failed file does not stop unrelated files from being copied unless the underlying device connection is lost.

## Testing Strategy

Unit tests cover the pure sync planner:

- Files missing on the hard drive are planned as phone-to-hard-drive copies.
- Files missing on the phone are planned as hard-drive-to-phone copies.
- Identical files are skipped.
- Same relative path with different metadata becomes a conflict.
- Conflict decisions produce the expected copy, keep-both, or skip operation.

ADB operations are wrapped behind a small interface so tests can use fake command runners. The UI is kept thin and manually verified in the first version by running the app on Windows.

## Future Extensions

After the Windows ADB version works, the next likely extensions are:

- Linux support for the same ADB workflow.
- Better packaging as a single executable.
- Optional hash-based conflict detection and full verification after copy.
- Remembered folder pairs.
- Safer MTP exploration if ADB is not acceptable for some users.
