# transcribe

Transcribe presentation recordings with OpenAI GPT-Transcribe. Built for fuzzy phone audio.

## What it does

- Cleans the audio first: rumble filter and volume leveling, with optional noise reduction.
- Splits long recordings at natural pauses into ~10 minute chunks (the API caps uploads at 25 MB).
- Sends your topic, your keyword list and the end of the previous chunk with every chunk.
- Writes a timestamped transcript to `transcripts/`.
- Caches finished chunks, so a dropped connection never repeats paid work.
- Offers a local Whisper mode for recordings that should not leave your computer.

## Setup (Windows, one time)

```powershell
git clone https://github.com/MattSchwope/transcribe.git
cd transcribe
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Open `.env` and paste your OpenAI API key. ffmpeg ships inside the `imageio-ffmpeg` package, so there is nothing else to install.

## Use

1. Copy recordings into `recordings/` (m4a, mp3, wav, mp4 and most phone formats work).
2. Optional: copy `terms.example.txt` to `terms.txt` and list names, acronyms and products.
3. Optional: add `talk1.context.txt` next to `talk1.m4a` with the topic and speaker names.
4. Preview chunks and cost: `python transcribe.py --dry-run`
5. Run it: `python transcribe.py --context "SOF Week panel on ISR contracting"`

Transcripts land in `transcripts/`. Re-running skips finished recordings unless you pass `--force`.

## Fuzzy audio

| Situation | Try |
|---|---|
| Normal phone recording | Default `--clean light` (rumble filter + volume leveling) |
| Speaker far away, HVAC hiss, crowd noise | `--clean strong` (adds noise reduction) |
| Words come out garbled after cleanup | `--clean off` (heavy processing can hurt; the model is trained on noisy audio) |
| Want to hear what was sent | `--keep-audio` saves the cleaned WAV next to the transcript |

Context and keywords are the biggest lever. Names, acronyms and company names are where noisy transcripts fail first.

To compare settings on one talk: `python transcribe.py recordings\talk1.m4a --clean strong --out transcripts\strong`

## Options

| Flag | Default | Purpose |
|---|---|---|
| `--context` | none | One line on what the talk covers |
| `--keywords` | none | Comma-separated terms, added to `terms.txt` |
| `--clean` | `light` | `off`, `light` or `strong` |
| `--chunk-minutes` | `10` | Chunk length, 2 to 40 |
| `--languages` | `en` | Expected spoken languages |
| `--model` | `gpt-transcribe` | Any OpenAI transcription model |
| `--engine` | `openai` | `local` runs Whisper on this computer |
| `--dry-run` | off | Show chunks and cost, send nothing |
| `--keep-audio` | off | Save the cleaned audio |
| `--force` | off | Redo recordings that already have transcripts |

## Cost

GPT-Transcribe is $0.0045 per minute. A one-hour talk costs about $0.27. The estimate prints before each run.

## Privacy

- Recordings, transcripts, `terms.txt`, `*.context.txt` and `.env` are git-ignored. Only code goes to GitHub.
- The OpenAI engine uploads audio chunks to OpenAI. OpenAI's data controls page says the transcription endpoint is not used for training. Zero Data Retention is available with approval.
- For sensitive recordings, use the local engine. After a one-time model download, nothing leaves the machine:

```powershell
pip install -r requirements-local.txt
python transcribe.py --engine local
```

## Limits

- Timestamps mark each chunk (every ~10 minutes), since GPT-Transcribe returns text only. Use `--chunk-minutes 3` for finer markers at the cost of less context per chunk. The local engine marks about every minute.
- No speaker labels. OpenAI's diarization model shuts down Feb 26, 2027 along with whisper-1 and gpt-4o-transcribe.
