# Codex Task: Plan meeting mode with NAudio

## Goal

Design and optionally scaffold a later .NET meeting recorder, but do not merge it into MVP dictation flow.

## Files to inspect

`docs/08_future_desktop_app_dotnet.md`, `docs/02_architecture_windows.md`

## Files to edit or create

`docs/`, optional `desktop/` scaffold only if requested

## Constraints

- Follow `AGENTS.md`.
- Keep implementation simple.
- Do not add cloud APIs.
- Do not remove local-first behavior.
- Add logs where useful.
- Add or update tests when pure functions are introduced.
- Update README or docs if commands change.

## Acceptance criteria

- Meeting mode remains separate
- Documents mic/system audio approach
- Documents chunking/transcript/summary flow
- No breaking changes to MVP

## Manual test

Review docs and confirm MVP commands still work.

## Output expected from Codex

- Summary of changes
- Files changed
- How to run
- How to test
- Known limitations
