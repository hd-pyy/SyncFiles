# SFTP Sync Design

## Goal

Add a third user-selectable sync mode:

- Hard drive <-> SFTP.

The first SFTP version supports username and password authentication only. It should let a user compare a local hard drive folder with a remote SFTP folder, preview missing files and conflicts, and run the same bidirectional fill-in synchronization model already used by the app.

## Scope

This change adds SFTP as a right-side remote endpoint. The existing planner still uses the current logical sides:

- `SourceSide.LOCAL` means the left hard drive folder.
- `SourceSide.PHONE` means the right endpoint. In SFTP mode, this represents the SFTP folder.

Keeping the existing logical sides avoids a broad domain rename in this iteration.

## UI Behavior

Add `Hard drive <-> SFTP` to the existing sync mode selector.

When SFTP mode is selected:

- Disable the Android device check button because ADB is not used.
- Label the first path as the hard drive folder.
- Replace the second endpoint controls with SFTP connection fields:
  - Host.
  - Port, default `22`.
  - Username.
  - Password.
  - Remote folder path.
- Keep the same scan and sync buttons.
- Preview tabs show:
  - SFTP -> hard drive.
  - Hard drive -> SFTP.
  - Conflicts.
- Conflict actions show:
  - Use SFTP version.
  - Use hard drive version.
  - Keep both.
  - Skip.

Passwords are held only in memory for the current app run. They are not written to disk in this version.

## SFTP Client

Add a focused SFTP adapter module. It should wrap the third-party SFTP implementation and expose a small app-facing API:

- Connect with host, port, username, and password.
- Scan a remote folder recursively into `FileRecord` values.
- Download one remote file to a local path.
- Upload one local file to a remote path.
- Create remote parent directories before upload.

The adapter should normalize remote paths to POSIX-style `/` separators. Remote relative paths should match local relative paths so the existing planner can compare both sides.

Use `paramiko` for SFTP transport. This keeps password authentication practical on Windows and gives tests a clean seam for fake clients.

## Execution

Add an SFTP executor that matches the existing executor interface:

- `SourceSide.PHONE -> SourceSide.LOCAL` downloads from SFTP to the hard drive.
- `SourceSide.LOCAL -> SourceSide.PHONE` uploads from the hard drive to SFTP.
- `CopyOperation.final_destination_relative_path` is respected for keep-both conflict copies.
- Cancellation is checked between operations.
- The existing progress callback is called after each completed operation.

The app chooses the executor by sync mode:

- Hard drive mode: `LocalSyncExecutor`.
- Phone mode: current ADB `SyncExecutor`.
- SFTP mode: new `SftpSyncExecutor`.

## Scanning Flow

In SFTP mode:

1. Scan the local hard drive folder with `scan_local_folder`.
2. Connect to SFTP with the entered credentials.
3. Recursively scan the selected remote folder.
4. Convert remote files to `FileRecord(..., side=SourceSide.PHONE)`.
5. Build the existing `SyncPlan`.

The scan should fail with a user-visible error if connection, authentication, or remote directory traversal fails.

## Error Handling

SFTP mode should surface clear errors for:

- Missing host, username, password, or remote folder.
- Invalid port.
- Connection refused or timed out.
- Authentication failure.
- Remote folder missing or inaccessible.
- Upload/download permission errors.

SFTP errors should not be shown as ADB errors. A generic `SyncFiles error` dialog is acceptable for the first version as long as the message is specific.

## Packaging

Add `paramiko` as a runtime dependency in `pyproject.toml`. The existing PyInstaller build should include it through normal dependency discovery.

## Tests

Add or update tests for:

- SFTP mode labels and mode switching.
- SFTP input validation.
- Remote recursive scanning converts entries to `FileRecord` values.
- Remote parent directories are created before upload.
- SFTP executor downloads remote-to-local operations.
- SFTP executor uploads local-to-remote operations.
- Keep-both conflict destination paths are honored.
- SFTP mode scan uses SFTP, not ADB.
- SFTP mode sync uses the SFTP executor.
- Existing hard drive and phone modes keep passing.

## Out Of Scope

- SSH private key authentication.
- Saving credentials.
- Host key management UI.
- Automatic LAN detection or fastest-mode selection.
- SFTP-to-SFTP, phone-to-SFTP, or arbitrary endpoint composition.
- Delete propagation.
- Per-byte transfer progress.
