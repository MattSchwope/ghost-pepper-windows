# Run one fixed-duration microphone recording.
# Called by AutoHotkey during the first MVP audio phase.

param(
    [int]$Duration = 8
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $RepoRoot

$PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    $PythonExe = "python"
}

& $PythonExe -m gpw.cli --config app\config.example.yaml record-audio --duration $Duration
exit $LASTEXITCODE
