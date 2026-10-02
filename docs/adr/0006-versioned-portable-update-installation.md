# Versioned portable update installation

The Windows portable artifact uses a stable root launcher plus versioned application directories. The root contains `SwimTunes.exe`, `current.json`, optional `previous.json`, and `versions/<version>/`. Each version directory contains the PyInstaller onedir app folder, `UPDATE_VERIFIED.json`, and `INSTALL_COMPLETE`.

The launcher is intentionally small: read `current.json`, refuse any version missing its verification record, completion marker, or executable, and fall back to `previous.json` or the latest complete version. It never starts a `.staging` directory or an unmarked version.

At startup the app consumes the signed eligibility result from the update checker. The installer writes a downloaded candidate to `versions/<new>.staging`, rechecks the signed metadata hash and size against the artifact, extracts it safely, writes the verification record, writes the completion marker last, atomically promotes the staging directory to `versions/<new>`, then atomically flips the pointer while preserving the old pointer. Fetch, write, verification, archive, or relaunch failures leave the old pointer and old version runnable and surface a warning.

Old versions are retained until a later launch can prove the new version started successfully; pruning is deliberately not part of the initial replacement step.
