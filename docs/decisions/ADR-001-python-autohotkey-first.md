# ADR-001 — Start with Python + AutoHotkey

## Status

Accepted for MVP.

## Decision

Use Python for the local transcription/cleanup pipeline and AutoHotkey v2 for Windows hotkeys/paste automation.

## Why

- Fastest path to working prototype.
- Easy to debug.
- No installer required.
- Avoids premature desktop-app complexity.
- Allows Codex to iterate quickly.

## Consequences

Pros:
- Simple setup.
- Easy to replace components.
- Good for proof-of-concept.

Cons:
- Not polished.
- Hotkey behavior is basic.
- Meeting/system-audio capture likely needs .NET/NAudio later.
