# Build Plan v2

Updated: October 27, 2026. This plan supersedes `docs/03_build_phases.md` and the task files in `codex/tasks/`.

## Goal

Replace Wispr Flow on Windows with a tool we own.

1. **Primary:** hold a hotkey, talk, and clean text lands in whatever app has focus.
2. **Secondary:** drop a long recording in a folder and get a Markdown note back.

Both paths share one engine: fuzzy-audio cleanup, OpenAI GPT-Transcribe and a personal dictionary.

## Decisions

| Topic | Decision |
|---|---|
| Transcription | OpenAI `gpt-transcribe` by default. Local Whisper fallback in a later phase. |
| Text cleanup, voice edits, summaries | OpenAI text models. Ollama is removed. |
| Hotkey | Both modes: hold to talk, double-tap for hands-free. |
| Dictation cleanup | Light: punctuation, capitalization, fillers and false starts removed. Word choice is never changed. |
| Extras | Personal dictionary, snippets, voice-edit of selected text. |
| Fuzzy audio | Required. Sources: noisy dictation, recorded talks, phone calls. |
| Long recordings | Watched folder. The subfolder picks the profile. Summary on top, full transcript below. |
| Output | Plain Markdown, Obsidian-friendly. |
| Users | Matt first. Team packaging later. |

Decision records: `docs/decisions/ADR-002-openai-default-engine.md` and `docs/decisions/ADR-003-ahk-front-python-service.md`.

## Priorities

| # | Phase | Why this order |
|---|---|---|
| 0 | Foundation: port `transcribe`, swap Ollama for OpenAI, eval harness | Everything else builds on it |
| 1 | Dictation core | The daily-driver Wispr replacement |
| 2 | Dictionary, light cleanup, snippets | Accuracy on names plus speed |
| 3 | Voice-edit selection | Highest-value extra after accuracy |
| 4 | Long recordings | Secondary use case; most code already exists in `transcribe` |
| 5 | Noise lab | Tune fuzzy-audio cleanup with real data |
| 6 | Local fallback | Offline and sensitive audio |
| 7 | Team packaging | After it works for Matt |

Phase 1b (live streaming) runs only if Phase 1 misses its latency target.

## Architecture

```text
AutoHotkey v2 (front end)
  ├─ hotkeys: hold, double-tap, voice-edit, cancel
  ├─ copies selection for voice-edit
  └─ pastes result, then restores the clipboard
        │  HTTP on 127.0.0.1
        ▼
Python service (always running, warm)
  ├─ mic capture (sounddevice)
  ├─ clip prep: trim silence, highpass, level, optional denoise
  ├─ engine: gpt-transcribe + dictionary keywords
  ├─ post: dictionary fixes → snippet match → light cleanup
  ├─ voice-edit: selection + spoken instruction → text model
  ├─ watcher thread: inbox/<profile>/ → long-recording pipeline
  └─ Markdown writer + stage timing logs
```

The service stays warm. Spawning Python on every keypress would add a second or more of startup, which kills the instant feel. AHK owns the keys because it is the most reliable way to grab hotkeys and paste on Windows.

### What carries over

| Source | Kept | Changed or removed |
|---|---|---|
| ghost-pepper-windows | config, logging, Markdown store, prompt profiles, recorder, AHK script, tests | Ollama cleaner removed. 8-second fixed recording replaced by hold/release. faster-whisper moves to Phase 6. |
| transcribe | audio cleanup chain, pause-aware chunking, GPT-Transcribe engine, keyword hints, chunk cache | CLI folds into `gpw`. `.txt` output becomes Markdown. |

### Models

| Job | Default model | Notes |
|---|---|---|
| Speech to text | `gpt-transcribe` | $0.0045/min. Supports keywords, prompt and languages. |
| Light cleanup, voice edit | `gpt-6-luna`, reasoning `none` | Fastest and cheapest tier. |
| Long-recording summaries | `gpt-6.1-sol` | Quality over speed. Pennies per hour of audio. |
| Live streaming (1b only) | `gpt-live-transcribe` | About $0.017/min. |

All models are set in `config.yaml`. Do not use `whisper-1`, `gpt-4o-transcribe` or `gpt-4o-mini-transcribe`. They shut down Feb 26, 2027.

### Config sketch

```yaml
openai:
  api_key_env: OPENAI_API_KEY
  transcribe_model: gpt-transcribe
  text_model: gpt-6-luna
  summary_model: gpt-6.1-sol

hotkeys:                       # proposed defaults, all configurable
  dictate_hold: RCtrl          # hold to talk
  hands_free: RCtrl double-tap # tap once to stop
  voice_edit_hold: RCtrl+RShift
  cancel: Esc

dictation:
  cleanup: light               # verbatim | light
  audio_prep: light            # off | light | strong
  daily_log: true              # append every dictation to notes/dictation/YYYY-MM-DD.md
  hands_free_max_minutes: 10

files:
  dictionary: C:/GhostPepperWindows/dictionary.yaml
  snippets: C:/GhostPepperWindows/snippets.yaml
  inbox: C:/GhostPepperWindows/inbox     # can point at a OneDrive folder
  notes: C:/GhostPepperWindows/notes
```

## Phase 0: Foundation

**Scope**
- Port `audio.py` and the OpenAI engine from `MattSchwope/transcribe` into `app/src/gpw/`.
- Replace `cleaner.py` (Ollama) with an OpenAI text client.
- Add the `openai:` config block. Read the key from `.env` or an environment variable, never from source.
- Update `pyproject.toml`: add `openai` and `imageio-ffmpeg`. Make `faster-whisper` optional.
- Build the eval harness: `gpw eval` runs clips through a setting and reports word error rate (WER) per clip with `jiwer`.

**Matt's homework:** collect 15 short clips (30 to 60 seconds) in `eval/clips/` (git-ignored). Include 5 noisy dictations, 5 cuts from recorded talks and 5 from phone calls. Add a hand-corrected `.ref.txt` for each.

**Done when**
- [ ] `gpw transcribe-file clip.m4a` returns text through GPT-Transcribe.
- [ ] `gpw eval --prep off,light,strong` prints a WER table.
- [ ] Tests pass. No Ollama references remain.

## Phase 1: Dictation core

**Scope**
- Python service with endpoints: `/start`, `/stop`, `/cancel`, `/health`. It binds to 127.0.0.1 only.
- AHK v2 script: hold to talk, double-tap for hands-free, Esc to cancel, tray icon plus start and stop sounds.
- Mic opens on key down. A warm mic with pre-roll is a config option if first words get clipped.
- Clip prep in numpy, not ffmpeg, so short clips add no process overhead: trim silence, highpass, level.
- Skip near-silent clips. No API call and no paste, which avoids phantom "Thank you." outputs.
- Paste via AHK with clipboard save and restore.
- Log stage timings (record end, upload, response, paste). Never log transcript text unless `debug_log_transcripts: true`.
- Daily Markdown dictation log.

**Done when**
- [ ] Works in Outlook, Chrome (LinkedIn and Gmail), Teams, Slack, Notepad and VS Code.
- [ ] Median release-to-paste is 1.5 seconds or less for a 10-second clip, measured from the logs over 50 dictations.
- [ ] Hands-free handles a 3-minute dictation.
- [ ] The clipboard comes back intact after every paste.

## Phase 1b: Live streaming (only if Phase 1 misses 1.5 seconds)

- Stream mic audio to `gpt-live-transcribe` while the key is held, so text is ready at release.
- Keep batch `gpt-transcribe` as the fallback.
- Done when the median release-to-paste is 0.7 seconds or less.

## Phase 2: Dictionary, light cleanup, snippets

**Personal dictionary** (`dictionary.yaml`)

```yaml
terms: [Kavaliro, TS/SCI, CI Poly, SOCOM, FSP]   # sent as GPT-Transcribe keywords
replace:                                         # applied after transcription
  "cavaliro": "Kavaliro"
  "ts sci": "TS/SCI"
```

- Terms go out as keyword hints with every request. Test how many the API accepts, and send the most used first if there is a cap.
- Replacements are case-insensitive, whole-word regex fixes.
- Add-a-word action: select a word, press a hotkey, and it is appended to `terms`.
- Auto-learn corrections (experimental, ported from upstream Ghost Pepper's post-paste learning). For 15 seconds after a paste, read the focused text field through Windows UI Automation. If Matt changes one or two words, save `wrong -> right` to `replace`. App support varies, so log which apps it works in.

**Light cleanup**
- Step 1, rules: dictionary fixes, filler removal (um, uh), spoken commands ("new line", "new paragraph").
- Step 2, an optional `gpt-6-luna` pass with a strict prompt: fix punctuation, drop false starts, never change word choice.
- Turn step 2 on only if the eval shows a real gain worth the added latency.

**Snippets** (`snippets.yaml`)
- Triggered only by the phrase "insert <name>", such as "insert signature" or "insert outreach". This prevents accidental fires.
- Multi-line text is supported. The match happens locally, with no API call.

**Done when**
- [ ] Dictionary terms are spelled right in at least 95% of eval clips that contain them.
- [ ] Light cleanup changes no content words, verified by a word-diff test.
- [ ] Snippets paste in under 0.3 seconds.

## Phase 3: Voice-edit selection

1. Select text and hold the voice-edit hotkey.
2. AHK copies the selection, then Python records the spoken instruction ("make this shorter", "fix grammar only", "turn into bullets").
3. `gpt-6-luna` returns only the rewritten text.
4. AHK pastes over the selection and restores the clipboard. Ctrl+Z undoes it in most apps.

**Done when**
- [ ] It works on selections up to about 2,000 words.
- [ ] With nothing selected, it shows a tooltip and changes nothing.

## Phase 4: Long recordings

**Inbox layout** (the subfolder picks the profile)

```text
inbox/
  meeting/          → meeting_summary.md   (default for files dropped in inbox/ itself)
  presentation/     → presentation.md      (new: takeaways, claims, follow-ups)
  bd-call/          → bd_call.md
  candidate-screen/ → candidate_screen.md
  client-intake/    → client_intake.md
  hm-debrief/       → hiring_manager_debrief.md
  _done/  _failed/
```

**Pipeline**
1. Watcher (`watchdog`) waits until the file size stops changing. That covers OneDrive still syncing from a phone.
2. File prep: `light` by default, `strong` per profile or per file.
3. Pause-aware chunks of about 10 minutes, with context carried between chunks.
4. GPT-Transcribe with dictionary keywords and the profile name as context.
5. `gpt-6.1-sol` writes the profile summary.
6. A Markdown note goes to `notes/<profile>/YYYY-MM-DD <title>.md`, the source moves to `inbox/_done/`, and a Windows toast appears.

**Note format**

```markdown
---
type: recording
profile: presentation
date: 2026-10-27
duration: "00:47:12"
source: talk1.m4a
engine: gpt-transcribe
audio_prep: strong
---
# <Title from summary>
## Summary
...profile sections...
## Transcript
[00:00:00] ...
[00:09:58] ...
```

**Done when**
- [ ] A one-hour recording dropped into `inbox/presentation/` becomes a note with no other steps.
- [ ] A failed file lands in `_failed/` with the reason in the log, and a re-drop resumes from cached chunks.

## Phase 5: Noise lab

Cleanup can help or hurt recognition, so every change has to win on the eval set before it ships.

| Rung | Method | Cost |
|---|---|---|
| 1 | Off | none |
| 2 | Light: highpass + volume leveling | ships in Phase 0 |
| 3 | Strong: adds ffmpeg `afftdn` spectral denoise | ships in Phase 0 |
| 4 | ML denoise: RNNoise (light, real-time) | new dependency |
| 5 | ML denoise: DeepFilterNet (heavier, recordings only) | PyTorch dependency |

- Run the full eval per source type: dictation, talks, calls.
- Pick a default per source type. A noisy talk and a car dictation may want different rungs.
- Add an automatic noise estimate that picks the rung per file.

**Done when** each source type has a default backed by a WER table in `docs/eval-results.md`.

## Phase 6: Local fallback

- faster-whisper `large-v3-turbo` on CPU. Pin `av<19`, since PyAV 19 breaks faster-whisper 1.2.1 decoding.
- Used automatically when offline. A profile can force it with `engine: local` for sensitive audio.
- Dictation text cleanup stays rule-based when offline.

## Phase 7: Team packaging

- Python service via PyInstaller, AHK compiled to an exe, and one Inno Setup installer.
- Per-user API key stored in Windows Credential Manager (`keyring`).
- Shared dictionary and snippets read from a team folder and merged with personal ones.
- Settings window for hotkeys, models and folders.
- Before rollout: confirm Kavaliro policy on sending candidate audio to OpenAI. OpenAI does not train on API audio. Zero Data Retention is available on approval.

## Cost estimate

| Use | Assumption | Monthly |
|---|---|---|
| Dictation | 20 min of speech per workday | ~$2.00 |
| Light cleanup and edits | `gpt-6-luna` | under $0.25 |
| Long recordings | 10 hours | ~$2.70 |
| Summaries | `gpt-6.1-sol`, 10 recordings | ~$0.50 |
| **Total** | | **~$5.50** |

Wispr Flow Pro is $12 to $15 per month for comparison.

## Risks

| Risk | Mitigation |
|---|---|
| Hotkeys and paste fail in apps run as administrator (Windows blocks input from non-admin apps) | Document it. Optional "run AHK as admin" setting. |
| Release-to-paste latency feels slow | Stage timing logs from day one. Phase 1b streaming. |
| Phantom text on silence | Energy check before any API call. |
| Clipboard clobbered | AHK saves and restores `ClipboardAll`. |
| No speaker labels | GPT-Transcribe has none, and the old diarize model shuts down Feb 26, 2027. Revisit if OpenAI adds diarization. |
| Antivirus flags keyboard hooks or the packaged exe | Code signing in Phase 7. |
| API key leak | `.env` and `keyring` only. `.gitignore` covers `.env`, audio, notes and eval clips. |

## Upstream Ghost Pepper (matthartman/ghost-pepper)

Status as of October 27, 2026: macOS only (Apple Silicon), fully on-device, no Windows port. Latest release is v2.4.4, with the last commit on July 27, 2026.

| Upstream feature | Use here |
|---|---|
| Preferred transcriptions + "commonly misheard" list | Same idea as `dictionary.yaml` (Phase 2) |
| Post-paste learning of corrections | Phase 2, experimental |
| Transcription Lab for comparing models | Same role as `gpw eval` (Phases 0 and 5) |
| Streaming ASR models for lower latency (July) | Validates Phase 1b |
| Screen/OCR context to help spelling | Later idea. Privacy cost, so opt-in only. |
| Meeting detection, Granola import, meeting Q&A, "2nd Brain" wiki over notes | Later ideas, not scheduled |

Upstream is Swift, so nothing ports as code. Borrow designs, not files.

## Later ideas (not scheduled)

- Ask questions across the Markdown notes archive.
- Screen-context spelling hints, opt-in.
- System-audio capture for Teams and Zoom calls.

## Open questions (decide during the build)

1. Final hotkey choices, after a day of use.
2. Daily dictation log on or off by default.
3. Whether to keep `transcribe` as a standalone CLI or archive it after Phase 4.
