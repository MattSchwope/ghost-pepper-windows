# Codex Task: Add recruiting prompt profiles

## Goal

Add reusable cleanup/summary profiles for recruiting workflows.

## Files to inspect

`app/prompts/`, `docs/`

## Files to edit or create

`app/prompts/*.md`, config/docs

## Constraints

- Follow `AGENTS.md`.
- Keep implementation simple.
- Do not add cloud APIs.
- Do not remove local-first behavior.
- Add logs where useful.
- Add or update tests when pure functions are introduced.
- Update README or docs if commands change.

## Acceptance criteria

- Includes general, candidate screen, client intake, hiring manager debrief, BD call
- CLI can select profile
- Defaults to general cleanup

## Manual test

Run same transcript through two profiles and compare output.

## Output expected from Codex

- Summary of changes
- Files changed
- How to run
- How to test
- Known limitations
