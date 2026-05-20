# Codex Task: Implement faster-whisper transcription

## Goal

Wrap faster-whisper behind a small transcriber module.

## Files to inspect

`app/src/gpw/transcriber.py`, `app/src/gpw/cli.py`, `app/requirements.txt`

## Files to edit or create

`transcriber.py`, `cli.py`, tests where useful

## Constraints

- Follow `AGENTS.md`.
- Keep implementation simple.
- Do not add cloud APIs.
- Do not remove local-first behavior.
- Add logs where useful.
- Add or update tests when pure functions are introduced.
- Update README or docs if commands change.

## Acceptance criteria

- Transcribes a WAV file locally
- Supports configurable model size/device/compute_type
- Handles empty transcription
- Logs model/device used

## Manual test

Run transcription against a WAV file and inspect raw text output.

## Output expected from Codex

- Summary of changes
- Files changed
- How to run
- How to test
- Known limitations
