# Codex Workflow

## How to use Codex for this project

1. Create a new GitHub repo.
2. Add this kit to the repo.
3. Commit the initial files.
4. Open Codex.
5. Connect Codex to the GitHub repo.
6. Give Codex `AGENTS.md` as the governing instruction file.
7. Run one task prompt at a time from `codex/tasks/`.
8. Review each diff before merging.
9. Test locally after each task.

## Recommended task order

1. `00_repo_bootstrap.md`
2. `01_mvp_audio_hotkey.md`
3. `02_transcription_engine.md`
4. `03_ollama_cleanup.md`
5. `04_clipboard_paste.md`
6. `05_markdown_history.md`
7. `06_prompt_profiles.md`
8. `07_meeting_mode_naudio.md`
9. `08_packaging.md`

## Codex prompt style

Each task prompt should include:
- goal
- files to edit
- files not to edit
- constraints
- acceptance criteria
- manual test steps
- expected output

## Rules for reviewing Codex output

Reject changes that:
- add cloud APIs without permission
- remove raw transcript storage
- skip logs
- hardcode your personal path
- mix meeting mode into the dictation MVP
- add a large framework too early
- require admin rights without explanation
