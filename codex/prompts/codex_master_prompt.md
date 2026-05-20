# Master Codex Prompt

You are building a Windows-native local-first voice dictation and meeting-note tool.

Read and follow:
- `AGENTS.md`
- `docs/01_product_brief.md`
- `docs/02_architecture_windows.md`
- `docs/03_build_phases.md`
- `docs/05_security_privacy.md`
- `docs/06_testing_acceptance_criteria.md`

Build only the task requested. Do not jump ahead.

The MVP pipeline is:

```text
hotkey → record mic → transcribe locally → clean locally → paste → save Markdown
```

Do not add:
- cloud transcription
- telemetry
- login
- user accounts
- web app
- cloud database
- Supabase
- vector store
- installer
- diarization

Unless the task explicitly requests it.

At the end, return:
1. What changed
2. How to run it
3. How to test it
4. What remains
