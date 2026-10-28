# ADR-002: OpenAI as the default engine, Ollama removed

## Status

Accepted, October 27, 2026. Supersedes the local-only and Ollama parts of ADR-001 and AGENTS.md.

## Decision

- Speech to text uses OpenAI `gpt-transcribe` by default.
- Text cleanup, voice edits and summaries use OpenAI text models (`gpt-6-luna`, `gpt-6.1-sol`).
- Ollama is removed.
- Local faster-whisper returns in Phase 6 as an offline and sensitive-audio fallback.

## Why

- Accuracy on fuzzy audio and on names matters more than staying fully local.
- GPT-Transcribe accepts keyword hints and context, which feed straight from the personal dictionary.
- One API key replaces running and tuning a local LLM server.
- Cost is a few dollars a month at expected volume.

## Consequences

- Audio and text leave the machine by default. OpenAI does not train on API data. Zero Data Retention is available on approval.
- Every cloud call stays behind visible config (`openai:` block), so the original "no hidden external calls" rule still holds.
- Nothing works offline until Phase 6.
- Team rollout needs a company policy check on candidate audio.
