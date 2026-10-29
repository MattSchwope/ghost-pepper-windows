# Start Ghost Pepper dictation: the AutoHotkey script, which starts the Python service.
# Run from anywhere:  .\app\scripts\start_dictation.ps1           (real transcription)
#                     .\app\scripts\start_dictation.ps1 -DryRun   (pastes a fixed sentence; no OpenAI calls)
# Stop it from the tray icon: right-click the coloured dot, then Exit.

param([switch]$DryRun)

$ErrorActionPreference = "Stop"

$Ahk = @(
    "$env:ProgramFiles\AutoHotkey\v2\AutoHotkey64.exe",
    "$env:LOCALAPPDATA\Programs\AutoHotkey\v2\AutoHotkey64.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Ahk) {
    throw "AutoHotkey v2 is not installed. Install it with: winget install AutoHotkey.AutoHotkey"
}

$Script = Join-Path $PSScriptRoot "hotkey.ahk"
$ArgList = @("`"$Script`"")
if ($DryRun) { $ArgList += "--dry-run" }
Start-Process -FilePath $Ahk -ArgumentList $ArgList

Write-Host "Ghost Pepper is starting. In a few seconds a green dot appears in the system tray"
Write-Host "(click the ^ arrow next to the clock if you do not see it)."
Write-Host "Hold Right Ctrl, talk, let go. Double-tap Right Ctrl for hands-free. Esc cancels."
if ($DryRun) { Write-Host "Dry run: it pastes a fixed sentence and sends nothing to OpenAI." }
