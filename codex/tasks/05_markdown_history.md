# Codex Task: Implement Markdown history

## Goal

Save raw and cleaned output with metadata in Markdown files.

## Files to inspect

`app/src/gpw/markdown_store.py`, `app/src/gpw/cli.py`, prompt files

## Files to edit or create

`markdown_store.py`, tests, config/docs as needed

## Constraints

- Follow `AGENTS.md`.
- Keep implementation simple.
- Do not add cloud APIs.
- Do not remove local-first behavior.
- Add logs where useful.
- Add or update tests when pure functions are introduced.
- Update README or docs if commands change.

## Acceptance criteria

- YAML frontmatter included
- Raw and cleaned transcript included
- File names are timestamped
- Notes folder configurable

## Manual test

Run dictation and confirm a Markdown note appears.

## Output expected from Codex

- Summary of changes
- Files changed
- How to run
- How to test
- Known limitations
