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
| `gpw serve` | The always-running dictation service the hotkey script talks to, on 127.0.0.1 only. It keeps the last 10 dictations in memory for the recent-dictations window. `--dry-run` pastes a fixed sentence instead of calling OpenAI. Normally started for you by the hotkey script |
| `gpw latency` | Release-to-paste report from the dictation timing log: median, 90th percentile, and where the time goes. `--last 50` for the most recent 50 |
| `gpw dictionary` | Shows where the personal dictionary is, how many terms and fixes it has, how many terms go out with each dictation, and any error in the file. Creates a starter file if there is none |
| `gpw snippets` | Shows where the snippets file is, the phrases you can say, and any error in the file. Creates a starter file if there is none |

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
| Select a word, press Ctrl+Alt+D (left Ctrl) | Adds it to the personal dictionary |
| Hold Right Ctrl, say "insert signature", let go | Pastes that snippet |
| Select text, hold Right Ctrl + Right Shift, say the change, let go | Voice edit: the selection is rewritten ("make this shorter"). Ctrl+Z undoes it in most apps |
| Ctrl+Alt+H, or left-click the tray dot | Opens the last 10 dictations, newest first, with a copy button on each |
| Ctrl+Alt+L | Pastes the latest dictation again, into whatever has focus now. Your clipboard is put back afterwards |

The tray dot is green when ready, red while listening, pink in hands-free and amber while transcribing. Left-click it for recent dictations. Right-click it for Recent dictations, today's dictation log, the dictionary, the snippets, the log folder, Restart (after config changes) and Exit.

- Clips with no speech are skipped: no API call and no paste.
- Every dictation is appended to `C:\GhostPepperWindows\notes\dictation\YYYY-MM-DD.md` (`dictation.daily_log`).
- Stage timings go to `C:\GhostPepperWindows\logs\`. Transcript text is never logged unless `privacy.debug_log_transcripts` is on.
- To try the keys and paste without spending anything: `.\app\scripts\start_dictation.ps1 -DryRun`.
- Keys, timings, sounds and the silence threshold are in the `hotkeys:` and `dictation:` blocks of the config.
- Windows blocks pasting into apps that run as administrator unless AutoHotkey runs as administrator too.

### Voice edit

Rewrite selected text by saying what to change.

1. Select the text in any app.
2. Hold Right Ctrl and Right Shift together (either one first). The start beep plays and a tooltip says "Voice edit".
3. Say the change: "make this shorter", "fix grammar only", "turn this into bullets", "make it more formal".
4. Let go. After a second or two the selection is replaced with the rewrite. Ctrl+Z undoes it in most apps.

- Leave the text selected until the rewrite lands; clicking elsewhere moves where it pastes.
- With nothing selected you get a "Select text first" tooltip and nothing changes. If no instruction is heard, or OpenAI fails, a tooltip says why and the selection stays as it was. An error message is never pasted.
- It keeps the text's language and formatting unless you ask otherwise, and keeps dictionary spellings (`terms:` and the right side of `replace:`). Your spoken instruction gets the same dictionary hints and fixes as dictation.
- Selections up to 3,000 words are sent (`voice_edit.max_words`); longer ones get a tooltip instead.
- Esc while it works cancels it. Pressing another key with Right Ctrl + Right Shift (such as an arrow) drops it quietly.
- The rewrite shows up in recent dictations as `[voice edit]`, so you can copy it again. It is not written to the daily dictation log.
- The log gets timings and word counts only. The selection, the instruction and the result are never logged unless `privacy.debug_log_transcripts` is on.
- The model is `openai.text_model` (`gpt-6-luna`) with the prompt in `app/prompts/voice_edit.md`. In a dry run the selection comes back in capitals, with no OpenAI call.
- Change the keys with `hotkeys.voice_edit_hold` (the dictate key plus one more key), or set it to `""` to turn voice edit off.
- Some apps copy the whole line when nothing is selected (VS Code does), so there the voice edit works on that line.

### Recent dictations

For a dictation that pasted into the wrong window, or nowhere.

- Press Ctrl+Alt+H or left-click the tray dot. A small window opens near the tray with the last 10 dictations, newest first. Each row shows the time, the program it was pasted into and the first two lines. Hover a row to see the whole text.
- Click a row's copy button (two overlapping rectangles) to put its exact text on the clipboard, line breaks included. The row shows "Copied" for a moment. Paste it wherever you like with Ctrl+V.
- Press 1 to 9, or 0 for the tenth row, to copy that row and close the window. Esc or a click anywhere else closes it. Ctrl+Alt+H again closes it too.
- Snippets show as `[snippet: <name>]` and copy the snippet's text. Cancelled and silent clips are not listed.
- Ctrl+Alt+L pastes the latest one again without opening the window, which covers the usual case: the wrong window a moment ago.
- The list lives in the service's memory, not on disk. After a restart it refills from today's daily log when `dictation.daily_log` is on, and starts empty when it is off. Dictations from before a restart show no program name.
- Change the keys with `hotkeys.recent` and `hotkeys.paste_last`, or set either to `""` to turn it off. The service refuses to start if two settings in `hotkeys:` name the same key.

### Personal dictionary and cleanup

`C:\GhostPepperWindows\dictionary.yaml` is created with examples the first time the service starts. Open it from the tray menu. Changes apply to the next dictation, with no restart.

- `terms:` names and acronyms sent to GPT-Transcribe as spelling hints with every dictation, top of the list first (at most `dictionary.max_keywords`).
- `replace:` fixes applied after transcription, as `"heard": "Wanted"`. They ignore case and only match whole words.
- Auto-learn (experimental, `dictionary.auto_learn`): for 15 seconds after a paste, it watches the text field. If you fix a name or acronym in the pasted text (cavaliro to Kavaliro), it saves that fix and shows a tooltip. Plain lowercase edits are never learned. It reads the field through Windows UI Automation; nothing leaves the PC.

Cleanup never changes word choice:

- `cleanup.remove_fillers` drops um, uh and erm.
- `cleanup.spoken_commands`: say "new line" or "new paragraph" to break the text there. "a new line of credit" stays as words.
- `cleanup.ai_pass` (off by default) adds a text-model pass for punctuation and false starts. It adds a round trip, and its answer is thrown away if it changed, added or reordered a word.

### Snippets

`C:\GhostPepperWindows\snippets.yaml` is created with an example the first time the service starts. Open it from the tray menu. Changes apply to the next dictation, with no restart.

```yaml
signature: |
  Your Name
  Your Title
outreach: "One line works too. Use quotes if it contains a colon."
```

- Say "insert <name>" as the whole dictation. Case, punctuation and um or uh don't matter, so "Um, Insert signature." works.
- Anything else stays as words: "please insert signature here", or "Thanks. Insert signature." as one dictation. A name with no snippet is pasted as words too. To add a signature after a sentence, dictate the sentence first and the snippet second.
- The text is pasted exactly as written, with several lines if it has them. No dictionary fixes, cleanup or text model touch it, and auto-learn does not watch it.
- OpenAI still has to hear the phrase, so release to paste takes about as long as any short dictation. Everything after the transcript is local. `gpw latency` reports both times.
- If a name keeps being misheard, add it to `terms:` in the dictionary, or add a fix to `replace:`.
- The daily log records `[snippet: <name>]` and what was heard, not the snippet's text.

## Success definition

Each phase in `docs/BUILD_PLAN.md` has its own "Done when" checklist.