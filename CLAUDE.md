@AGENTS.md

## Claude Code workflow

- Build with `/build <phase>`, for example `/build 0`. Plain `/build` picks the next phase with unchecked boxes in `docs/BUILD_PLAN.md`.
- One phase per branch (`phase-<n>`) and one pull request per phase. Codex reviews PRs automatically.
- `reference/transcribe/` holds the working code from the `transcribe` repo. Port from it in Phase 0, then delete the folder in the same PR.

## Environment

- Windows, PowerShell. Python 3.11+ in `.venv`.
- Setup: `python -m venv .venv`, then `.\.venv\Scripts\python.exe -m pip install -e ".[test]"`
- Tests: `.\.venv\Scripts\python.exe -m pytest`
- OpenAI key lives in `.env` at the repo root (`OPENAI_API_KEY=...`). Never print it, log it or commit it.
- ffmpeg comes from the `imageio-ffmpeg` package. Do not require a system install.
- AutoHotkey v2 is needed from Phase 1 on.

## Rules for every change

- Tests for every pure function. Mock OpenAI in tests; never call the real API from pytest.
- Real API calls happen only in manual checks that Matt runs, or behind an explicit `--live` flag.
- Keep stage timing logs. Never log transcript text unless `debug_log_transcripts: true`.
- Commit author email must be the GitHub noreply address already in git config. Never set a personal email.
