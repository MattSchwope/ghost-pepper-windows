> **Superseded.** See `docs/BUILD_PLAN.md` (October 27, 2026). Kept for history.

# Build Phases

## Phase 0 — Repo setup

- Create Python package structure.
- Add config loading.
- Add logging.
- Add `requirements.txt`.
- Add setup script.
- Add README run instructions.

## Phase 1 — Dictation MVP

- Record audio from default mic.
- Save WAV.
- Transcribe with faster-whisper.
- Clean with Ollama.
- Copy to clipboard.
- Paste with AutoHotkey.
- Save Markdown note.

## Phase 2 — Reliability

- Device selection.
- Timeout handling.
- Empty-audio handling.
- Ollama unavailable fallback.
- Clipboard preservation.
- Manual retry.
- Log viewer or simple log file.

## Phase 3 — Prompt profiles

- General cleanup.
- Candidate screen.
- Client intake.
- Hiring manager debrief.
- BD call.
- Job description notes.
- Resume-review notes.

## Phase 4 — Meeting mode

- Separate meeting recorder.
- Capture mic first.
- Add system audio later.
- Chunk long recordings.
- Save transcript and summary.
- Add action item extraction.

## Phase 5 — UI/tray app

- Tray icon.
- Start/stop recording.
- Prompt profile selection.
- Model settings.
- Notes folder selection.
- Audio retention settings.

## Phase 6 — Distribution

- Build executable.
- Add installer.
- Add code signing if distributing outside your machine.
- Add update mechanism later.
