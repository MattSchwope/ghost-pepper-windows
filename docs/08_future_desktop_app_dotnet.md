# Future Desktop App Plan

## Why not start with a full desktop app?

The fastest path is Python + AutoHotkey. A full Windows desktop app adds packaging, UI, permissions, installer, and audio-device complexity before the workflow is proven.

## When to move to .NET

Move to .NET after the Python MVP works and you know the workflow is valuable.

## Candidate stack

- .NET 8
- WPF or WinUI
- NAudio for audio
- Local Python worker or whisper.cpp executable for transcription
- Ollama for cleanup
- Windows tray app

## Features for the desktop version

- tray icon
- hotkey settings
- mic picker
- prompt profile picker
- note folder picker
- start/stop meeting recording
- model selection
- debug logs
- audio retention setting

## Meeting mode design

```text
MeetingRecorder
  → chunk WAV files
  → TranscriptionWorker
  → TranscriptMerger
  → SummaryGenerator
  → MarkdownMeetingStore
```

## Do not build first

- diarization
- installer
- automatic updates
- cloud sync
- team features
