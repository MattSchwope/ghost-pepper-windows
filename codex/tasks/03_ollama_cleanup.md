# Codex Task: Implement Ollama cleanup

## Goal

Call Ollama's local chat API to clean raw transcript text using prompt files.

## Files to inspect

`app/src/gpw/cleaner.py`, `app/prompts/general_cleanup.md`, `app/config.example.yaml`

## Files to edit or create

`cleaner.py`, prompt files, config loading

## Constraints

- Follow `AGENTS.md`.
- Keep implementation simple.
- Do not add cloud APIs.
- Do not remove local-first behavior.
- Add logs where useful.
- Add or update tests when pure functions are introduced.
- Update README or docs if commands change.

## Acceptance criteria

- Calls `localhost:11434`
- Uses selected prompt profile
- Times out safely
- Falls back to raw text if cleanup fails

## Manual test

Start Ollama, pull the configured model, then run cleanup on sample text.

## Output expected from Codex

- Summary of changes
- Files changed
- How to run
- How to test
- Known limitations
