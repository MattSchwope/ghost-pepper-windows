# Codex Task: Implement MVP mic recording and AutoHotkey trigger

## Goal

Add fixed-duration mic recording and a safe AutoHotkey script to launch it.

## Files to inspect

`app/src/gpw/recorder.py`, `app/src/gpw/cli.py`, `app/scripts/hotkey.ahk`

## Files to edit or create

`recorder.py`, `cli.py`, `hotkey.ahk`, docs if needed

## Constraints

- Follow `AGENTS.md`.
- Keep implementation simple.
- Do not add cloud APIs.
- Do not remove local-first behavior.
- Add logs where useful.
- Add or update tests when pure functions are introduced.
- Update README or docs if commands change.

## Acceptance criteria

- Can record a WAV from default mic
- Saves file to configured audio directory
- AutoHotkey can call the Python command
- Errors are visible

## Manual test

Open terminal and run the record command. Then run the AHK script and confirm the WAV appears.

## Output expected from Codex

- Summary of changes
- Files changed
- How to run
- How to test
- Known limitations
