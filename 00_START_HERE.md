# 00 — Start Here

## Objective

Build a Windows-native private dictation and meeting-note tool inspired by Ghost Pepper.

Do not attempt to port the macOS Swift app. Build the Windows equivalent.

## Two goals

### Goal 1 — Build files and prompts for Codex

This repo includes:
- product brief
- architecture
- Codex task prompts
- implementation rules
- prompt templates
- starter source files
- setup scripts
- acceptance criteria

### Goal 2 — Support-material checklist

This repo also includes:
- required Windows apps
- optional apps
- setup steps
- folder layout
- model choices
- Obsidian recommendation
- privacy/security defaults

## Build strategy

Start with the smallest useful loop:

```text
AutoHotkey hotkey
  → Python script
  → record mic
  → faster-whisper transcription
  → Ollama cleanup
  → copy to clipboard
  → paste into active app
  → save Markdown note
```

Only after that works, build meeting mode.

## Default assumptions

- OS: Windows 10/11
- Python: 3.11+
- Local LLM runtime: Ollama
- STT: faster-whisper
- Hotkey: AutoHotkey v2
- Note destination: local Markdown folder, optionally opened as an Obsidian vault
- No cloud transcription
- No telemetry
- No uploading recordings by default

## First Codex task to run

Use:

```text
codex/tasks/00_repo_bootstrap.md
```

Then proceed numerically.
