# ADR-003: AutoHotkey front end + always-running Python service

## Status

Accepted, October 27, 2026. Refines ADR-001.

## Decision

- AutoHotkey v2 owns hotkeys (hold, double-tap, voice-edit, cancel), selection copy, paste and clipboard restore.
- A Python service runs in the background and does capture, transcription, cleanup and file work.
- They talk over HTTP bound to 127.0.0.1.

## Why

- Starting Python on every keypress adds a second or more before recording can begin. A warm service removes that.
- AHK is the most reliable way to catch key-down and key-up, suppress keys and paste on Windows. Python keyboard hooks can be dropped by Windows when the process stalls.
- Each side stays small and testable.

## Consequences

- Two processes to start. A launcher script starts both, and AHK checks `/health` before recording.
- Team packaging (Phase 7) ships a compiled AHK exe and a PyInstaller exe in one installer.
- Revisit if packaging two runtimes becomes painful.
