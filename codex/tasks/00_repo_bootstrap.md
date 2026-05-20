# Codex Task: Bootstrap repo structure

## Goal

Create a clean Python project structure and make the app runnable from the command line.

## Files to inspect

`README.md`, `AGENTS.md`, `app/requirements.txt`, `app/config.example.yaml`

## Files to edit or create

`app/src/gpw/`, `app/tests/`, setup-related files as needed

## Constraints

- Follow `AGENTS.md`.
- Keep implementation simple.
- Do not add cloud APIs.
- Do not remove local-first behavior.
- Add logs where useful.
- Add or update tests when pure functions are introduced.
- Update README or docs if commands change.

## Acceptance criteria

- `python -m gpw.cli --help` works
- requirements install cleanly
- config example is documented
- no app logic is overbuilt

## Manual test

Run `pip install -r app/requirements.txt`, then run `python -m gpw.cli --help`.

## Output expected from Codex

- Summary of changes
- Files changed
- How to run
- How to test
- Known limitations
