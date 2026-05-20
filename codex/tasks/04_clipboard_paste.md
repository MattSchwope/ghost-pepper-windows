# Codex Task: Implement clipboard and paste flow

## Goal

Copy cleaned text to clipboard and coordinate with AutoHotkey to paste into the active app.

## Files to inspect

`app/src/gpw/clipboard.py`, `app/scripts/hotkey.ahk`, `app/src/gpw/cli.py`

## Files to edit or create

`clipboard.py`, `hotkey.ahk`, `cli.py`

## Constraints

- Follow `AGENTS.md`.
- Keep implementation simple.
- Do not add cloud APIs.
- Do not remove local-first behavior.
- Add logs where useful.
- Add or update tests when pure functions are introduced.
- Update README or docs if commands change.

## Acceptance criteria

- Final text is copied to clipboard
- AHK sends Ctrl+V after successful CLI run
- If paste fails, text remains on clipboard
- Logs paste/copy status

## Manual test

Open Notepad, trigger dictation, confirm final text appears.

## Output expected from Codex

- Summary of changes
- Files changed
- How to run
- How to test
- Known limitations
