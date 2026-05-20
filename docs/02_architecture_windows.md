# Windows Architecture

## Phase 1 architecture

```text
AutoHotkey v2
  ├─ handles keyboard shortcut
  ├─ calls PowerShell/Python
  └─ sends Ctrl+V after text is copied

Python app
  ├─ records mic audio
  ├─ saves WAV
  ├─ transcribes with faster-whisper
  ├─ cleans with Ollama localhost API
  ├─ copies cleaned text to clipboard
  ├─ writes Markdown note
  └─ logs pipeline events

Local services
  └─ Ollama on localhost:11434
```

## Recommended directories

```text
C:\GhostPepperWindows\
  audio\
  notes\
  logs\
  models\
  config.yaml
```

## Repo directories

```text
app\
  src\gpw\
  prompts\
  scripts\
  tests\
docs\
codex\
```

## Core modules

| Module | Purpose |
|---|---|
| `config.py` | load app settings |
| `recorder.py` | record mic audio |
| `transcriber.py` | faster-whisper wrapper |
| `cleaner.py` | Ollama cleanup wrapper |
| `clipboard.py` | copy/paste support |
| `markdown_store.py` | save raw + cleaned notes |
| `cli.py` | command-line entry point |
| `logging_setup.py` | app logs |

## Data flow

```text
Hotkey
  → CLI command
  → WAV file
  → raw transcript
  → cleaned transcript
  → clipboard
  → active application
  → Markdown note
```

## Later meeting mode

Meeting mode should be a separate pipeline:

```text
mic/system audio
  → chunks
  → transcript
  → summary
  → action items
  → Markdown meeting note
```

Do not mix meeting mode into the first dictation pipeline.
