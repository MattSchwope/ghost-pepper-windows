# Ghost Pepper Windows — Codex Build Kit

This kit is designed to help Codex build a Windows-native version of the Ghost Pepper workflow:

**hotkey → mic recording → local transcription → local cleanup → clipboard paste → Markdown memory**

The initial build is intentionally simple:
- Python backend
- AutoHotkey v2 hotkey layer
- faster-whisper transcription
- Ollama cleanup
- Markdown output for Obsidian/RAG

Later phases can add:
- tray app
- GUI settings
- NAudio/.NET meeting recorder
- system-audio capture
- diarization
- installer

## Start here

1. Read `00_START_HERE.md`.
2. Give Codex `AGENTS.md`.
3. Use the task prompts in `codex/tasks/` one at a time.
4. Build Phase 1 first before touching meeting transcription.
5. Do not add cloud services unless explicitly requested.

## MVP success definition

The MVP is complete when:

1. User presses a hotkey.
2. The app records audio.
3. The app transcribes locally.
4. The app cleans the transcript locally.
5. The cleaned text is copied to clipboard.
6. The cleaned text is pasted into the active app.
7. A Markdown note is saved locally.
8. Failures are logged with clear error messages.
