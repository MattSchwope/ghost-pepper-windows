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

Write-Host "Checking Ollama..."
try {
    $response = Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -UseBasicParsing -TimeoutSec 5
    Write-Host "Ollama is reachable."
} catch {
    Write-Warning "Ollama is not reachable at http://localhost:11434. Install/start Ollama and run: ollama pull qwen2.5:3b"
}

Write-Host "Setup complete."
Write-Host "Try: .\.venv\Scripts\python.exe -m gpw.cli --help"
Write-Host "Then: .\.venv\Scripts\python.exe -m gpw.cli --config app\config.example.yaml record-audio --duration 5"
