# Testing and Acceptance Criteria

## Manual MVP test

1. Open Notepad.
2. Press the configured hotkey.
3. Speak: "This is a test message for Ghost Pepper Windows."
4. Release hotkey or wait for fixed-duration MVP recording.
5. Confirm cleaned text appears in Notepad.
6. Confirm a Markdown note appears in the notes folder.
7. Confirm raw transcript is preserved in the note.
8. Confirm logs show each pipeline step.

## Acceptance criteria

### Recording

- Records default microphone.
- Saves WAV file.
- Handles no microphone error.

### Transcription

- Uses faster-whisper locally.
- Produces raw transcript.
- Handles empty transcript.

### Cleanup

- Calls Ollama localhost.
- Uses prompt files.
- Falls back to raw transcript if Ollama is unavailable.

### Clipboard/paste

- Copies final text.
- Pastes into active application.
- Does not crash if paste fails.

### Markdown

- Saves YAML frontmatter.
- Saves cleaned text.
- Saves raw transcript.
- Saves metadata.

### Logging

- Logs pipeline start/end.
- Logs file paths.
- Logs errors.
- Does not log full transcripts unless enabled.

## Unit tests

Add tests for:
- config loading
- prompt loading
- Markdown rendering
- note path generation
- cleanup fallback behavior
