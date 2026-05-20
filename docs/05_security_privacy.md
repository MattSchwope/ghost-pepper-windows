# Security and Privacy Requirements

## Default privacy posture

- Local audio only.
- Local transcription only.
- Local LLM cleanup only.
- Local Markdown storage only.
- No telemetry.
- No cloud sync unless explicitly enabled later.

## Data created by the app

| Data | Default location | Retention |
|---|---|---|
| Raw WAV | `C:\GhostPepperWindows\audio\` | keep until user deletes |
| Markdown notes | `C:\GhostPepperWindows\notes\` | keep |
| Logs | `C:\GhostPepperWindows\logs\` | keep rolling logs |
| Config | `C:\GhostPepperWindows\config.yaml` | keep |

## Sensitive logging

Do not log full transcript text unless explicitly enabled.

Recommended config:

```yaml
debug_log_transcripts: false
```

## Audio retention option

Add later:

```yaml
delete_audio_after_success: false
```

## Network rules

- Ollama should be accessed through `http://localhost:11434`.
- Do not expose Ollama to the public internet.
- Do not add external API calls without a visible config flag.

## Failure behavior

If cleanup fails:
- paste raw transcript
- save Markdown with `cleanup_failed: true`
- log the exception

If transcription fails:
- do not paste garbage
- show a clear error
- save audio for debugging
