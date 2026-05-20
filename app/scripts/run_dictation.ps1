# Run one fixed-duration dictation pipeline.
# Called by AutoHotkey during MVP phase.

param(
    [int]$Duration = 8,
    [string]$Profile = "general_cleanup"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $RepoRoot

$env:PYTHONPATH = "$RepoRoot\app\src"

.\.venv\Scripts\python.exe -m gpw.cli --config app\config.example.yaml record-once --duration $Duration --profile $Profile
