# Porting notes (Phase 0)

Working code from github.com/MattSchwope/transcribe, copied here so Phase 0 needs no other repo.

| File | Port to | Notes |
|---|---|---|
| audio.py | app/src/gpw/audio.py | Cleanup filters, pause detection on the ORIGINAL audio (volume leveling hides pauses), chunk planning |
| engines.py | app/src/gpw/engines.py | GPT-Transcribe via extra_body keywords/languages; LocalEngine moves to Phase 6 |
| transcribe.py | app/src/gpw/longform.py + cli.py | Chunk cache, context carry-over, skip-if-done |

Tested behavior to keep: pause-aligned cuts, resume after a failed chunk, keywords sent as `keywords[]` form fields, PyAV pin `av<19` for the local engine.

Delete this folder in the Phase 0 PR once the port is done.
