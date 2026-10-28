# Ghost Pepper Windows

A Wispr Flow replacement for Windows, plus a long-recording-to-Markdown pipeline.

**Hold a hotkey → talk → fuzzy-audio cleanup → OpenAI GPT-Transcribe → personal dictionary + light cleanup → pasted into any app**

**Drop a recording in the inbox → transcript + profile summary → Markdown note**

## Start here

1. Read `docs/BUILD_PLAN.md`. It is the source of truth for scope and phase order.
2. Give your coding agent (Claude Code or Codex) `AGENTS.md`.
3. Build one phase at a time, starting with Phase 0.

`docs/03_build_phases.md` and `codex/tasks/` describe the original local-only, Ollama-based plan. They are kept for history only.

## Repo bootstrap

From the repo root, create a virtual environment and install the package in editable mode:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
```

Verify the command-line entry point:

```powershell
.\.venv\Scripts\python.exe -m gpw.cli --help
```

Run the included tests:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

The default config lives at `app/config.example.yaml`. It keeps app data under
`C:\GhostPepperWindows` and uses local services only, including Ollama on
`http://localhost:11434`.

## Success definition

Each phase in `docs/BUILD_PLAN.md` has its own "Done when" checklist.
