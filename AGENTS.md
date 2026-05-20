# AGENTS.md — Ghost Pepper Windows Build Rules

## Product intent

Build a Windows-native local-first dictation and meeting-note app.

Primary workflow:
1. Trigger recording with a hotkey.
2. Capture mic audio.
3. Transcribe locally.
4. Clean locally with Ollama.
5. Paste cleaned text into the active app.
6. Save raw + cleaned output as Markdown.

## Non-negotiables

- Do not send audio or transcripts to cloud APIs by default.
- Do not add telemetry.
- Do not require Obsidian for the core app to work.
- Keep Phase 1 Python + AutoHotkey simple.
- Keep meeting mode separate from dictation mode.
- Preserve raw transcript for debugging.
- Preserve clipboard content when possible.
- Add clear logs for each pipeline step.
- Use environment/config files instead of hardcoded user paths.
- All generated notes must be plain Markdown.
- Assume the user is technical but wants step-by-step execution.

## Architecture constraints

Phase 1:
- Python package under `app/src/gpw/`
- AutoHotkey script under `app/scripts/hotkey.ahk`
- Prompt files under `app/prompts/`
- Config example at `app/config.example.yaml`
- Tests under `app/tests/`

Phase 2:
- Add tray app or small GUI only after the Phase 1 pipeline works.

Phase 3:
- For system audio and meeting transcription, evaluate .NET + NAudio or another Windows-native audio layer.

## Coding style

- Prefer small modules.
- Use typed Python where useful.
- Use clear errors.
- No complex framework unless justified.
- Do not hide failures.
- Add tests for pure functions.
- Keep side effects isolated.

## Definition of done for each task

Each task must include:
- code changes
- setup/update notes
- how to run
- how to test manually
- known limitations
- next recommended task

## Security rules

- Do not log full sensitive transcripts by default unless `debug_log_transcripts=true`.
- Do not store API keys in source.
- Bind Ollama calls to localhost only.
- Keep recordings local.
- Add a cleanup option later to delete raw audio after successful transcription.
