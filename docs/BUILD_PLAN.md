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
| Extras | Personal dictionary, on-screen name spelling, snippets, voice-edit of selected text. |
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
| 2b | On-screen name spelling | Names with several spellings (Sean, Shawn; Kelly, Kelli) that one dictionary entry can't settle |
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
  ├─ screen names: focused window text → names, read while recording (UI Automation)
  ├─ engine: gpt-transcribe + screen names + dictionary keywords
  ├─ post: dictionary fixes → name spelling (screen, then preferred) → snippet match → light cleanup
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
  screen_names: true           # Phase 2b: spell names the way the focused window shows them

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
- [x] `gpw transcribe-file clip.m4a` returns text through GPT-Transcribe. Proven live by Matt on October 28, 2026 with `gpw record-once`, which runs the same prep and GPT-Transcribe path.
- [x] `gpw eval --prep off,light,strong` prints a WER table.
- [x] Tests pass. No Ollama references remain.

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

How to check: `gpw latency --last 50` reports the median release-to-paste for 7-13 s clips and how many pastes restored the clipboard byte for byte. `start_dictation.ps1 -DryRun` tries keys and paste with no OpenAI calls.

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
- [x] Light cleanup changes no content words, verified by a word-diff test.
- [ ] Snippets paste in under 0.3 seconds.

How to check: `gpw dictionary` shows the file and what is sent. `test_light_cleanup_changes_no_content_words` is the word-diff test, and `words_kept()` applies the same check to the text-model pass at run time. Dictionary, light cleanup, add-word and auto-learn landed first (October 28, 2026) so they could be tried alongside the Phase 1 checks; snippets followed on November 4, 2026.

For snippets, `gpw latency` reports `transcript_to_paste` against the 0.3-second target, plus release-to-paste for the same dictations. See open question 12 for why those differ. `test_snippet_comes_back_well_inside_the_paste_target` covers the service side. The box stays open until real pastes are in the log.

## Phase 2b: On-screen name spelling

Many names have several spellings: Sean, Shawn, Shaun; Kelly, Kelli, Kellie; Catherine, Katherine, Kathryn. A `replace:` entry forces one spelling everywhere, so `"kelly": "Kelli"` misspells every other Kelly. The right spelling is usually already on screen: the To line of an email, the thread, a chat header, a LinkedIn profile.

**Per dictation**
1. On key down, a worker thread reads the text of the focused window through UI Automation, while the mic records, so it adds no wait after release. It reuses auto-learn's reader, widened from the focused field to the window.
2. Pick out likely names: capitalized words and two-word runs, minus sentence starts and common words. Only these leave the reader. The screen text stays in memory and is dropped after the dictation.
3. Send the screen names first in that request's GPT-Transcribe keywords, then dictionary terms, within `dictionary.max_keywords`.
4. After transcription, match each capitalized word in the transcript to the screen names by sound (a phonetic key such as Double Metaphone) and spelling similarity. If exactly one screen name matches, use its spelling. If two different spellings on screen match, or none does, leave the word alone.
5. Names not on screen fall back to a preferred spelling in the dictionary (below), and otherwise stay as transcribed.

**Dictionary changes**

```yaml
prefer:            # default spelling when the screen gives no clue; the screen wins
  "kelly": "Kelli"
  "catherine": "Katherine"
```

- Name-only `replace:` entries move to `prefer:`. `replace:` keeps acronyms and real mishearings (`"cavaliro": "Kavaliro"`), which always apply.
- Auto-learn saves a corrected name to `prefer:` instead of `replace:`, and skips the save when the screen already showed the spelling you typed.
- `gpw dictionary` lists `prefer:` and warns about `replace:` entries that look like name variants, or that add words instead of fixing one (`"clearance": "clearance TS/SCI"`).

**Privacy and limits**
- `dictation.screen_names` turns it off, and `dictation.screen_names_skip` lists apps never read (password managers, banking). Password fields are skipped, as in auto-learn.
- Never log screen text or names unless `privacy.debug_log_transcripts` is on. Log per dictation which app was read, how many names were found, and whether a name was changed.
- Apps that expose no text (remote desktop, images, scanned PDFs) get no screen names. OCR stays a later idea.
- Reading Chrome and Electron apps (Teams, Slack, new Outlook) turns on their accessibility tree. See open question 10.

**Done when**
- [ ] With one spelling of a name on screen, a dictation that says the name pastes that spelling in at least 95% of tries, in Outlook, Chrome (Gmail and LinkedIn), Teams and Slack.
- [ ] With two spellings of the same name on screen, the transcribed word is left alone (test).
- [ ] Median release-to-paste with `screen_names` on is within 0.1 seconds of off, over 50 dictations.
- [ ] Screen text is never written to disk or logs, and only the picked-out names are sent to OpenAI (test).

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
| Screen/OCR context to help spelling | Phase 2b, from on-screen text via UI Automation, with an off switch. OCR stays a later idea. |
| Meeting detection, Granola import, meeting Q&A, "2nd Brain" wiki over notes | Later ideas, not scheduled |

Upstream is Swift, so nothing ports as code. Borrow designs, not files.

## Later ideas (not scheduled)

- Ask questions across the Markdown notes archive.
- OCR for name spelling where UI Automation finds no text (remote desktop, images, scanned PDFs). Opt-in.
- A contacts list (Outlook or an ATS export) as another source of known name spellings.
- System-audio capture for Teams and Zoom calls.

## Open questions (decide during the build)

1. Final hotkey choices, after a day of use.
2. Daily dictation log on or off by default.
3. Whether to keep `transcribe` as a standalone CLI or archive it after Phase 4.
4. Whether to delete the pre-v2 docs (`00_START_HERE.md`, `docs/01_*`-`08_*`, `codex/`). They still describe the Ollama plan; the README marks them as history.
5. Phase 1 clip prep in numpy should match the ffmpeg `light` chain closely enough that `gpw eval` results carry over. Phase 1 finding: it tracks ffmpeg within about 2 dB (`test_light_prep_tracks_ffmpeg_light_chain`). Both leave clips under about 4 seconds unboosted, because dynaudnorm fades its gain in from 1.0 over about 2 seconds at each end. For short dictations `light` is in effect highpass only. Phase 5 could test a flat gain for dictation.
6. Clipboard history: pasted dictations land in Windows clipboard history (Win+V) and cloud clipboard sync when those are on. Excluding them needs the `ExcludeClipboardContentFromMonitorProcessing` clipboard format. Decide in Phase 2 or 7.
7. With Right Ctrl as the dictate key, every Right Ctrl shortcut briefly opens the mic and plays the start cue before cancelling quietly. Part of question 1.
8. Nothing is recorded after the key is released. If last words get clipped, add a short post-roll (`dictation.tail_ms`), which adds the same time to latency.
9. GPT-Transcribe's keyword cap is untested. `dictionary.max_keywords` defaults to 100. If OpenAI refuses a request with keywords, the service retries once without them, pastes, and shows a tooltip. A live `--live` check could find the real cap.
10. Auto-learn reads text fields through UI Automation. Querying Chrome or Electron apps (Slack, Teams, new Outlook) switches on their accessibility tree, which can cost some CPU. Which apps it works in shows in the log as `Auto-learn ... app=<exe> paste_found=<true|false>`.
11. Auto-learn only learns fixes whose new form has a capital letter or digit (names, acronyms), and never a changed number (100 -> 200). A fix like mark -> Marc would turn every later "mark" into "Marc". Remove bad ones from `replace:` in the dictionary.
12. "Snippets paste in under 0.3 seconds" can only hold from the transcript to the paste. The phrase has to reach GPT-Transcribe first, and short clips took a median of about 1.5 seconds there in the first live logs. Measuring from key release would need a way around speech, such as a hotkey per snippet, or a faster transcript from Phase 1b or Phase 6.
13. Snippets fire only when "insert <name>" is the whole dictation. "Thanks for your time. Insert signature." stays as words. Firing on a sentence of its own inside a longer dictation would be handy for email sign-offs, at some risk of accidental fires.
14. Phase 2b reads only the window you dictate into. Often the name was on a different screen a moment ago, such as a LinkedIn profile before switching to Outlook. A short memory of names seen in recent windows (a few minutes, in memory only) would catch that, at the cost of more screen reading.
15. Phase 2b changes only capitalized words, so "mark the date" never becomes "Marc". Names GPT-Transcribe returns in lowercase are not fixed. Check the logs for how often that happens.
