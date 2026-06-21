# Sync Mode Selection Design

## Goal

SyncFiles should support two user-visible synchronization modes:

- Hard drive to hard drive.
- Hard drive to Android phone.

The app should no longer imply that the second folder is always a phone. The default workflow should make the selected mode obvious before scanning, previewing, or syncing.

## Problem

The current UI and domain language are phone-centric:

- The second folder is labeled as a phone folder.
- The second folder button says browse phone.
- Preview tabs say phone to hard drive and hard drive to phone.
- Logs and progress text mention scanning the phone folder.
- The executor assumes one side is local storage and one side is ADB storage.

This is correct for Android sync, but it is misleading when the user wants to synchronize two local or external hard drive folders.

## Recommended Approach

Add an explicit sync mode selector while keeping the implementation scoped:

- `Hard drive <-> hard drive`
- `Hard drive <-> phone`

The app keeps its existing two-folder layout. Mode selection controls how the second folder is chosen, scanned, previewed, and copied.

This avoids a full endpoint-composition system for now. The app will not support phone-to-phone, phone-to-network, or arbitrary endpoint combinations in this change.

## UI Behavior

Add a sync mode combobox near the top of the main window.

When mode is `Hard drive <-> hard drive`:

- Hide or disable the device status check controls because ADB is not needed.
- Label the first path as left hard drive folder.
- Label the second path as right hard drive folder.
- Both folder buttons use the native folder picker.
- Preview tabs show:
  - Left -> right
  - Right -> left
  - Conflicts
- Logs and progress text mention left and right folders, not phone.

When mode is `Hard drive <-> phone`:

- Keep the existing device check controls.
- Label the first path as hard drive folder.
- Label the second path as phone folder.
- The first folder button uses the native folder picker.
- The second folder button opens the ADB phone browser.
- Preview tabs show:
  - Phone -> hard drive
  - Hard drive -> phone
  - Conflicts
- Logs and progress text mention hard drive and phone.

Changing mode should clear any existing scan plan and conflict choices, because the old preview may no longer match the selected endpoints.

## Domain Model

Keep the existing `SyncPlan` behavior for this iteration, but introduce mode-aware naming at the app boundary.

For local-to-local mode:

- Scan the left folder with `scan_local_folder()`.
- Scan the right folder with `scan_local_folder()`.
- Convert one scanned list to the logical phone side and the other to the logical local side before calling the existing planner.
- Render logical phone/local results as right/left labels in the UI.

For phone mode:

- Keep the current ADB scan for the phone side.
- Keep the current local scan for the hard drive side.

This keeps the first implementation small and testable. Renaming `SourceSide.PHONE` and `SourceSide.LOCAL` to generic endpoint names is intentionally excluded from this change.

## Execution

Add a local-to-local executor path for hard drive mode:

- Copy from left folder to right folder when the plan says left-to-right.
- Copy from right folder to left folder when the plan says right-to-left.
- Create parent directories before local copies.
- Preserve the existing fill-in synchronization behavior: missing files are copied, conflicts are previewed, deletions are not propagated.

Keep the current ADB executor path for phone mode:

- Use `adb pull` for phone-to-hard-drive copies.
- Use `adb push` for hard-drive-to-phone copies.

## Conflict Actions

Conflict actions should use mode-aware labels:

- Hard drive mode:
  - Use left version.
  - Use right version.
  - Keep both.
  - Skip.
- Phone mode:
  - Use phone version.
  - Use hard drive version.
  - Keep both.
  - Skip.

The underlying conflict resolution can continue using the existing action enum during this iteration.

## Error Handling

Hard drive mode should not perform ADB checks or show ADB-specific errors.

Hard drive mode should surface local filesystem errors, such as:

- Folder missing or inaccessible.
- Permission denied while scanning.
- Permission denied while copying.

Phone mode should keep the existing ADB error handling.

## Tests

Add or update tests for:

- Default sync mode is hard drive to hard drive.
- Mode changes update labels, buttons, and preview tab titles.
- Mode changes clear the existing scan plan.
- Hard drive mode scans two local folders and builds a bidirectional plan.
- Hard drive mode executes local copies in both directions.
- Phone mode still uses ADB scanning and ADB push/pull execution.
- Conflict labels are mode-aware.
- Existing progress behavior still passes.

## Out Of Scope

- Generic arbitrary endpoint composition.
- Phone-to-phone synchronization.
- Network, cloud, MTP, iPhone, or background sync.
- Delete propagation.
- Per-byte copy progress.
