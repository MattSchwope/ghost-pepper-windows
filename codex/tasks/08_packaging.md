# Codex Task: Package local MVP

## Goal

Create scripts to install dependencies, initialize folders, and run the app.

## Files to inspect

`app/scripts/setup.ps1`, `README.md`, `app/config.example.yaml`

## Files to edit or create

`setup.ps1`, `run_dictation.ps1`, docs

## Constraints

- Follow `AGENTS.md`.
- Keep implementation simple.
- Do not add cloud APIs.
- Do not remove local-first behavior.
- Add logs where useful.
- Add or update tests when pure functions are introduced.
- Update README or docs if commands change.

## Acceptance criteria

- Setup script creates local folders
- Installs Python deps
- Checks Ollama availability
- Does not require admin unless explained

## Manual test

Run setup on a clean Windows environment and then run dictation.

## Output expected from Codex

- Summary of changes
- Files changed
- How to run
- How to test
- Known limitations
