# AGENTS.md — Ghost Pepper Windows Build Rules

## Source of truth

`docs/BUILD_PLAN.md` defines scope, phase order and acceptance criteria. Work one phase at a time. If anything below conflicts with it, the build plan wins.

## Product intent

Build a Windows dictation tool that replaces Wispr Flow, plus a long-recording-to-Markdown pipeline.

Primary workflow (dictation):
1. Hold a hotkey (or double-tap for hands-free).
2. Capture mic audio in the always-running Python service.
3. Prep the clip (trim, highpass, level, optional denoise).
4. Transcribe with OpenAI `gpt-transcribe` plus personal dictionary keywords.
5. Apply dictionary fixes, snippets and light cleanup. Never change word choice.
6. Paste into the active app through AutoHotkey, then restore the clipboard.

Secondary workflow (recordings): watched inbox folder → cleanup → chunked transcription → profile summary → Markdown note.

## Non-negotiables

- Cloud calls go only to OpenAI, only through the `openai:` config block. No other external services without a visible config flag. (ADR-002)
- Never log transcript text unless `debug_log_transcripts: true`.
- API keys come from `.env`, environment variables or Windows Credential Manager. Never from source.
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

- Python package under `app/src/gpw/`
- AutoHotkey script under `app/scripts/hotkey.ahk`; it talks to the Python service over 127.0.0.1 only (ADR-003)
- Prompt files under `app/prompts/`
- Config example at `app/config.example.yaml`
- Tests under `app/tests/`
- Eval clips under `eval/clips/` (git-ignored)
- Settings GUI and installer only in Phase 7.
- System-audio capture is a later idea, not scheduled.

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
- Bind the Python service to 127.0.0.1 only.
- Recordings, notes, eval clips and `.env` never go into git.
- Keep the `delete_audio_after_success` option working.
