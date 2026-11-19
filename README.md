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
| `gpw meeting list` | Lists meeting captures, newest first, with their status (done, failed, interrupted, ...) and where the note went |
| `gpw meeting retry "2026-11-18 1405"` | Turns a failed or cut-off meeting capture into a note. Parts already transcribed are not sent again |

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
| Ctrl+Alt+H, or left-click the tray dot | Opens the last 10 dictations, newest first, with a copy button on each |
| Ctrl+Alt+L | Pastes the latest dictation again, into whatever has focus now. Your clipboard is put back afterwards |
| Ctrl+Alt+M | Starts meeting capture, or stops it. See [Meeting capture](#meeting-capture) |

The tray dot is green when ready, red while listening, pink in hands-free, amber while transcribing and blue while a meeting is being captured. Left-click it for recent dictations. Right-click it for Recent dictations, meeting capture, today's dictation log, the dictionary, the snippets, the log folder, Restart (after config changes) and Exit.

- Clips with no speech are skipped: no API call and no paste.
- Every dictation is appended to `C:\GhostPepperWindows\notes\dictation\YYYY-MM-DD.md` (`dictation.daily_log`).
- Stage timings go to `C:\GhostPepperWindows\logs\`. Transcript text is never logged unless `privacy.debug_log_transcripts` is on.
- To try the keys and paste without spending anything: `.\app\scripts\start_dictation.ps1 -DryRun`.
- Keys, timings, sounds and the silence threshold are in the `hotkeys:` and `dictation:` blocks of the config.
- Windows blocks pasting into apps that run as administrator unless AutoHotkey runs as administrator too.

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

## Meeting capture

Transcribe a Teams or Zoom call, or a webinar, from this PC's own audio. No bot joins the call and nothing is installed in Teams or Zoom. A note with a summary and a Me/Them transcript lands in `C:\GhostPepperWindows\notes\meetings\` shortly after the call.

**Consent.** Teams and Zoom tell everyone when *they* record a call. This capture tells nobody. It is on you to ask the people on the call before you record them, wherever the law or your company requires it, and in many places it does. Capture never starts on its own.

1. Join the call as usual, so it plays on this PC's speakers or headset.
2. Press Ctrl+Alt+M, or right-click the tray dot and choose **Start meeting capture**. The dot turns blue and its tooltip counts the time.
3. When the call ends, press Ctrl+Alt+M again, or choose **Stop meeting capture**.
4. A notice says **Meeting note ready**. Click it to open the note. The note is `notes\meetings\YYYY-MM-DD HHMM <title>.md`: a title, a short overview, decisions, action items and open questions, then the full transcript with lines like `[00:12:03] Me: ...` and `[00:12:09] Them: ...`.

What it records:

- **Them** is whatever plays on the default speaker or headset, so everyone on the far end is "Them". **Me** is your mic (the dictation mic). Each goes to its own file while it records, under `C:\GhostPepperWindows\meetings\<YYYY-MM-DD HHMM>\`, so a crash keeps everything up to that point.
- Use a headset if you can. On laptop speakers the mic also hears the call; lines where your mic only repeated the call are dropped, but a headset is cleaner.
- Plugging in a headset mid-call is fine: the capture follows the new default device.
- **Mute my mic track** in the tray menu keeps a side conversation out of the transcript. Muting yourself in Teams or Zoom does not mute this track.
- Dictation keeps working during a capture. What you dictate is pasted as usual and kept out of the meeting.
- For a webinar where you only listen, set `meetings.capture_mic: false` to record the call audio only.
- Capture stops on its own after `meetings.max_hours` (4). If nothing has been heard from the call after 2 minutes, a tooltip says so: the call is probably playing on another device.

After the call:

- Only the stretches with speech are sent to GPT-Transcribe, in batches, with your dictionary terms as spelling hints. Dictionary fixes are applied; light cleanup is not, so the transcript stays word for word. `openai.summary_model` writes the summary from `app/prompts/meeting_capture.md`.
- The two audio files are deleted once the note is written, unless `meetings.keep_audio: true`.
- If something fails (no internet, for example), the audio stays, a notice says so, and the tray menu offers **Retry failed meeting**. `gpw meeting retry "<folder>"` does the same from PowerShell, and `gpw meeting list` shows every capture and its status. Parts already transcribed are not paid for twice.
- If the service was killed mid-call, the audio up to that moment is kept and shows as `interrupted` in `gpw meeting list`; `gpw meeting retry` turns it into a note. A capture that was stopped but not yet processed is finished the next time the service starts.
- Restart and Exit in the tray menu ask first while a meeting is being captured, then stop it so its note is written when Ghost Pepper next runs.
- Transcript text and audio never go into the service log, only counts and timings. Cost is about $0.27 per hour of speech plus a cent or two for the summary.
- Settings: `hotkeys.meeting`, the `meetings:` block, and `files.meetings` for where the audio is kept while it is processed. `.\app\scripts\start_dictation.ps1 -DryRun` tries a capture end to end without calling OpenAI.

## Success definition

Each phase in `docs/BUILD_PLAN.md` has its own "Done when" checklist.