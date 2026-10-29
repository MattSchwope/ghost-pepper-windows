# Ghost Pepper Windows

A Wispr Flow replacement for Windows, plus a long-recording-to-Markdown pipeline.

**Hold a hotkey → talk → fuzzy-audio cleanup → OpenAI GPT-Transcribe → personal dictionary + light cleanup → pasted into any app**

**Drop a recording in the inbox → transcript + profile summary → Markdown note**

## Quick start (Windows)

Paste this into PowerShell from any folder. It installs what's missing, clones this repo to your user folder (or updates your existing clone), asks for your OpenAI key once, and starts Claude Code on the next phase:

```powershell
irm https://raw.githubusercontent.com/MattSchwope/ghost-pepper-windows/main/kickstart.ps1 | iex
```

Run the same line any time to update and keep building. Every step skips what is already done.

To keep the repo somewhere else, run the line once from inside your clone, or set `GPW_REPO_DIR` to the folder first. The folder is saved to `GPW_REPO_DIR`, so later runs from any folder use it.

## Start here

1. Read `docs/BUILD_PLAN.md`. It is the source of truth for scope and phase order.
2. Give your coding agent (Claude Code or Codex) `AGENTS.md`.
3. Build one phase at a time, starting with Phase 0.

`00_START_HERE.md`, `docs/01_*` through `docs/08_*` and `codex/` describe the original local-only plan. They are kept for history only.

## Repo bootstrap

From the repo root, create a virtual environment and install the package in editable mode:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
```

Verify the command-line entry point:

```powershell
.\.venv\Scripts\gpw.exe --help
```

Run the included tests (no API calls; OpenAI is mocked):

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## Commands

Copy `.env.example` to `.env` and paste your OpenAI key first. ffmpeg ships inside the `imageio-ffmpeg` package.

| Command | What it does |
|---|---|
| `gpw transcribe-file clip.m4a` | Prints the transcript from GPT-Transcribe. Long files are cut at pauses into ~10 minute chunks. `--prep off\|light\|strong`, `--context "one line on the topic"` |
| `gpw eval --prep off,light,strong` | Runs every clip in `eval/clips/` at each prep level and prints a WER table. Hypotheses are cached in `eval/results/`; `--fresh` redoes them |
| `gpw record-once --duration 8` | Beeps, records for that many seconds, beeps again, then transcribes and copies the text. Uses the Windows default mic unless `recording.input_device` names another. Kept for quick mic checks; daily dictation uses hold-to-talk below |
| `gpw serve` | The always-running dictation service the hotkey script talks to, on 127.0.0.1 only. `--dry-run` pastes a fixed sentence instead of calling OpenAI. Normally started for you by the hotkey script |
| `gpw latency` | Release-to-paste report from the dictation timing log: median, 90th percentile, and where the time goes. `--last 50` for the most recent 50 |

The default config is `app/config.example.yaml`. App data lives under `C:\GhostPepperWindows`. Models, audio prep and keyword hints are set there; the only cloud service is OpenAI, under the `openai:` block.

## Dictation (hold to talk)

Needs AutoHotkey v2 (the kickstart installs it). Start it from the repo folder:

```powershell
.\app\scripts\start_dictation.ps1
```

A green dot appears in the system tray, and the script starts the Python service in the background.

| Do this | What happens |
|---|---|
| Hold Right Ctrl, talk, let go | The text is pasted where your cursor is. Your clipboard is put back afterwards |
| Tap Right Ctrl twice | Hands-free: talk as long as you like (up to 10 minutes). Tap Right Ctrl once to stop and paste |
| Esc while recording or transcribing | Cancels. Nothing is pasted |
| Right Ctrl plus another key (Ctrl+C) | Works as normal; the recording is dropped quietly |

The tray dot is green when ready, red while listening, pink in hands-free and amber while transcribing. Right-click it for today's dictation log, the log folder, Restart (after config changes) and Exit.

- Clips with no speech are skipped: no API call and no paste.
- Every dictation is appended to `C:\GhostPepperWindows\notes\dictation\YYYY-MM-DD.md` (`dictation.daily_log`).
- Stage timings go to `C:\GhostPepperWindows\logs\`. Transcript text is never logged unless `privacy.debug_log_transcripts` is on.
- To try the keys and paste without spending anything: `.\app\scripts\start_dictation.ps1 -DryRun`.
- Keys, timings, sounds and the silence threshold are in the `hotkeys:` and `dictation:` blocks of the config.
- Windows blocks pasting into apps that run as administrator unless AutoHotkey runs as administrator too.

## Success definition

Each phase in `docs/BUILD_PLAN.md` has its own "Done when" checklist.