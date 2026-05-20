# Support Materials Checklist

## Required

### 1. Windows PC

Recommended:
- Windows 10 or 11
- 16 GB RAM minimum preferred
- SSD storage
- Good microphone or headset

### 2. Python 3.11+

Use Python for the first build.

### 3. Git

Needed for Codex/GitHub workflow.

### 4. VS Code

Recommended local editor for reviewing Codex changes.

### 5. AutoHotkey v2

Used for global hotkeys and paste automation.

### 6. Ollama

Used for local cleanup/summarization.

Recommended first model:

```powershell
ollama pull qwen2.5:3b
```

Alternative stronger models if your machine handles them:

```powershell
ollama pull qwen2.5:7b
ollama pull llama3.1:8b
```

### 7. faster-whisper

Used for local transcription.

Installed through `requirements.txt`.

## Strongly recommended

### 8. Obsidian

Not required, but recommended.

Why:
- Opens a folder of Markdown files as a vault.
- Gives you local search.
- Works well with future RAG workflows.
- Keeps notes portable.

You do **not** need Obsidian for the app to function. The app should save Markdown files whether or not Obsidian is installed.

## Optional

### 9. NVIDIA GPU setup

Only needed if CPU transcription is too slow.

Start CPU-only first.

### 10. .NET 8 SDK

Needed later if building a Windows tray app or NAudio meeting recorder.

### 11. NAudio

Needed later for advanced Windows audio capture.

### 12. PowerToys

Optional productivity helper, not required.

### 13. Windows Terminal

Optional but helpful.

## Not needed for MVP

- Supabase
- Pinecone
- BigQuery
- paid transcription API
- OpenAI API key
- Docker
- installer builder
- code signing certificate
- Obsidian plugins
