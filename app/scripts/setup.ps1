# Ghost Pepper Windows MVP setup script
# Run from repo root in PowerShell.

$ErrorActionPreference = "Stop"

Write-Host "Creating local app folders..."
New-Item -ItemType Directory -Force -Path "C:\GhostPepperWindows\audio" | Out-Null
New-Item -ItemType Directory -Force -Path "C:\GhostPepperWindows\notes" | Out-Null
New-Item -ItemType Directory -Force -Path "C:\GhostPepperWindows\logs" | Out-Null

Write-Host "Creating Python virtual environment..."
python -m venv .venv

Write-Host "Installing Python dependencies..."
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[test]"

Write-Host "Checking OpenAI key..."
if ((Test-Path ".env") -and (Select-String -Path ".env" -Pattern "^OPENAI_API_KEY=sk-[^.]" -Quiet)) {
    Write-Host "OPENAI_API_KEY found in .env."
} elseif ($env:OPENAI_API_KEY) {
    Write-Host "OPENAI_API_KEY found in the environment."
} else {
    Write-Warning "No OpenAI key found. Copy .env.example to .env and paste your key."
}

Write-Host "Setup complete."
Write-Host "Try: .\.venv\Scripts\gpw.exe --help"
Write-Host "Then: .\.venv\Scripts\gpw.exe transcribe-file path\to\clip.m4a"
