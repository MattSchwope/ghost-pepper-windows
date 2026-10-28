<#
Ghost Pepper Windows kickstart. Run this one line in PowerShell, from any folder:

  irm https://raw.githubusercontent.com/MattSchwope/ghost-pepper-windows/main/kickstart.ps1 | iex

It installs whatever is missing (Git, Python, GitHub CLI, AutoHotkey v2, Claude Code),
logs GitHub in, clones or updates the repo, sets up Python, saves your OpenAI key
to .env, then starts Claude Code building the next phase.
Safe to re-run any time: every step skips what is already done.

Where the repo goes: run it from inside an existing clone to use that one, or set
GPW_REPO_DIR to a folder first. Otherwise it clones to ghost-pepper-windows in your
user folder. The folder it used is saved to GPW_REPO_DIR, so later runs from any
folder find it again.
#>

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$RepoUrl = 'https://github.com/MattSchwope/ghost-pepper-windows'
$DefaultRepoDir = Join-Path $HOME 'ghost-pepper-windows'
$GitName = 'Matt Schwope'
$GitEmail = '205084476+MattSchwope@users.noreply.github.com'

function Step([string]$Message) { Write-Host ''; Write-Host "==> $Message" -ForegroundColor Cyan }
function Ok([string]$Message) { Write-Host "    $Message" -ForegroundColor Green }
function Note([string]$Message) { Write-Host "    $Message" -ForegroundColor Yellow }

function Has([string]$Command) { [bool](Get-Command $Command -ErrorAction SilentlyContinue) }

function Update-SessionPath {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machine;$user;$HOME\.local\bin;$env:Path"
}

function Install-WithWinget([string]$Id, [string]$Label) {
    if (-not (Has 'winget')) {
        throw "winget is missing. Install 'App Installer' from the Microsoft Store, then run this again."
    }
    Write-Host "    Installing $Label (this can take a minute)..."
    $ErrorActionPreference = 'Continue'
    winget install --id $Id -e --silent --accept-source-agreements --accept-package-agreements | Out-Null
    Update-SessionPath
}

function Get-PythonExe {
    $ErrorActionPreference = 'Continue'
    $probe = 'import sys; print(sys.version_info[0]*100+sys.version_info[1]); print(sys.executable)'
    $candidates = @(
        @{ Exe = 'py'; Pre = @('-3') },
        @{ Exe = 'python'; Pre = @() },
        @{ Exe = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"; Pre = @() }
    )
    foreach ($c in $candidates) {
        if (-not (Has $c.Exe) -and -not (Test-Path $c.Exe)) { continue }
        $argList = @($c.Pre) + @('-c', $probe)
        $out = @(& $c.Exe @argList 2>$null)
        if ($LASTEXITCODE -eq 0 -and $out.Count -ge 2 -and [int]$out[0] -ge 311) {
            return $out[1].Trim()
        }
    }
    return $null
}

function Find-AutoHotkey {
    $paths = @(
        "$env:ProgramFiles\AutoHotkey\v2\AutoHotkey64.exe",
        "$env:LOCALAPPDATA\Programs\AutoHotkey\v2\AutoHotkey64.exe"
    )
    foreach ($p in $paths) { if (Test-Path $p) { return $p } }
    return $null
}

function Resolve-RepoDir {
    # First match wins: the clone you are standing in, then GPW_REPO_DIR, then the default.
    $ErrorActionPreference = 'Continue'
    $slug = [regex]::Escape(([Uri]$RepoUrl).AbsolutePath.Trim('/'))
    if ($PWD.Provider.Name -eq 'FileSystem') {
        $top = git -C $PWD.ProviderPath rev-parse --show-toplevel 2>$null
        if ($LASTEXITCODE -eq 0 -and $top) {
            $origin = git -C $top remote get-url origin 2>$null
            if ($LASTEXITCODE -eq 0 -and $origin -match "[:/]$slug(\.git)?/?$") {
                return [IO.Path]::GetFullPath($top)
            }
        }
    }
    # Resolved against where you are now, so a relative or ~ path is saved as a full path.
    if ($env:GPW_REPO_DIR) { return $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($env:GPW_REPO_DIR) }
    return $DefaultRepoDir
}

function Test-OpenAIKey([string]$Key) {
    # Returns 'valid', 'invalid' or 'unknown' (network problem).
    try {
        Invoke-RestMethod -Uri 'https://api.openai.com/v1/models' -Headers @{ Authorization = "Bearer $Key" } -TimeoutSec 20 | Out-Null
        return 'valid'
    } catch {
        $status = 0
        if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
        if ($status -eq 401) { return 'invalid' }
        return 'unknown'
    }
}

function Get-EnvKey([string]$EnvPath) {
    if (-not (Test-Path $EnvPath)) { return $null }
    foreach ($line in [IO.File]::ReadAllLines($EnvPath)) {
        if ($line -match '^\s*OPENAI_API_KEY\s*=\s*(.+?)\s*$') {
            $value = $Matches[1].Trim('"', "'")
            if ($value -and $value -ne 'sk-...') { return $value }
        }
    }
    return $null
}

function Set-EnvKey([string]$EnvPath, [string]$Key) {
    $lines = @()
    if (Test-Path $EnvPath) {
        $lines = @([IO.File]::ReadAllLines($EnvPath) | Where-Object { $_ -notmatch '^\s*OPENAI_API_KEY\s*=' })
    }
    $lines += "OPENAI_API_KEY=$Key"
    # UTF-8 without BOM, so Python reads the key name cleanly.
    [IO.File]::WriteAllText($EnvPath, (($lines -join "`r`n") + "`r`n"))
}

function Read-SecretKey {
    $secure = Read-Host '    Paste your OpenAI API key (hidden), then press Enter' -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return ([Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)).Trim() }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

function Invoke-Kickstart {
    # Scoped to this function so your PowerShell window keeps its normal settings afterwards.
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'
    Update-SessionPath

    Step 'Checking tools'
    if (Has 'git') { Ok 'Git found' } else { Install-WithWinget 'Git.Git' 'Git' }
    if (-not (Has 'git')) { throw 'Git did not install. Close PowerShell, open a new window and run this again.' }

    $python = Get-PythonExe
    if ($python) { Ok "Python found: $python" } else {
        Install-WithWinget 'Python.Python.3.12' 'Python 3.12'
        $python = Get-PythonExe
        if (-not $python) { throw 'Python 3.11+ did not install. Close PowerShell, open a new window and run this again.' }
        Ok "Python installed: $python"
    }

    if (Has 'gh') { Ok 'GitHub CLI found' } else { Install-WithWinget 'GitHub.cli' 'GitHub CLI' }
    if (Find-AutoHotkey) { Ok 'AutoHotkey v2 found' } else { Install-WithWinget 'AutoHotkey.AutoHotkey' 'AutoHotkey v2' }

    if (Has 'claude') { Ok 'Claude Code found' } else {
        Write-Host '    Installing Claude Code...'
        powershell -NoProfile -ExecutionPolicy Bypass -Command 'irm https://claude.ai/install.ps1 | iex' | Out-Null
        Update-SessionPath
        if (-not (Has 'claude')) { throw 'Claude Code did not install. Close PowerShell, open a new window and run this again.' }
        Ok 'Claude Code installed'
    }

    Step 'Checking GitHub login'
    $ErrorActionPreference = 'Continue'
    gh auth status --hostname github.com *> $null
    $loggedIn = ($LASTEXITCODE -eq 0)
    if (-not $loggedIn) {
        Note 'A browser window opens. Copy the code shown here, paste it there and approve.'
        gh auth login --hostname github.com --git-protocol https --web
        gh auth status --hostname github.com *> $null
        if ($LASTEXITCODE -ne 0) { $ErrorActionPreference = 'Stop'; throw 'GitHub login did not finish. Run this again.' }
    }
    gh auth setup-git --hostname github.com *> $null
    $ErrorActionPreference = 'Stop'
    Ok 'Logged in to GitHub'

    $RepoDir = Resolve-RepoDir
    Step "Getting the repo into $RepoDir"
    $ErrorActionPreference = 'Continue'
    if (Test-Path (Join-Path $RepoDir '.git')) {
        git -C $RepoDir pull --ff-only --quiet
        if ($LASTEXITCODE -eq 0) { Ok 'Updated to the latest version' } else { Note 'Could not fast-forward; keeping your local copy as is.' }
    } elseif ((Test-Path $RepoDir) -and (Get-ChildItem $RepoDir -Force | Select-Object -First 1)) {
        $ErrorActionPreference = 'Stop'
        throw "$RepoDir exists but is not the repo. Rename or remove it, then run this again."
    } else {
        git clone --quiet $RepoUrl $RepoDir
        if ($LASTEXITCODE -ne 0) { $ErrorActionPreference = 'Stop'; throw 'git clone failed. Check your internet connection and run this again.' }
        Ok 'Cloned'
    }
    git -C $RepoDir config user.name $GitName
    git -C $RepoDir config user.email $GitEmail
    $ErrorActionPreference = 'Stop'
    Ok 'Commits will use your GitHub noreply email'
    if ([Environment]::GetEnvironmentVariable('GPW_REPO_DIR', 'User') -ne $RepoDir) {
        [Environment]::SetEnvironmentVariable('GPW_REPO_DIR', $RepoDir, 'User')
        $env:GPW_REPO_DIR = $RepoDir
        Ok 'Saved this folder to GPW_REPO_DIR for next time'
    }

    Step 'Saving your OpenAI key'
    $envPath = Join-Path $RepoDir '.env'
    $key = Get-EnvKey $envPath
    if ($key) {
        Ok 'Key already in .env'
    } else {
        for ($try = 1; $try -le 3; $try++) {
            $key = Read-SecretKey
            if (-not $key) { Note 'Nothing pasted. Try again.'; continue }
            $check = Test-OpenAIKey $key
            if ($check -eq 'invalid') { Note 'OpenAI rejected that key. Try again.'; $key = $null; continue }
            if ($check -eq 'unknown') { Note 'Could not reach OpenAI to verify the key. Saving it anyway.' }
            break
        }
        if (-not $key) { throw 'No valid OpenAI key saved. Run this again when you have it.' }
        Set-EnvKey $envPath $key
        $key = $null
        Ok 'Saved to .env (git-ignored)'
    }

    Step 'Setting up Python'
    $venvPython = Join-Path $RepoDir '.venv\Scripts\python.exe'
    $ErrorActionPreference = 'Continue'
    if (-not (Test-Path $venvPython)) {
        & $python -m venv (Join-Path $RepoDir '.venv')
        if ($LASTEXITCODE -ne 0) { $ErrorActionPreference = 'Stop'; throw 'Could not create the Python virtual environment.' }
    }
    Push-Location $RepoDir
    try {
        & $venvPython -m pip install --quiet --upgrade pip
        & $venvPython -m pip install --quiet -e '.[test]'
        $pipOk = ($LASTEXITCODE -eq 0)
    } finally { Pop-Location }
    $ErrorActionPreference = 'Stop'
    if ($pipOk) { Ok 'Dependencies installed' } else { Note 'pip reported a problem. Claude Code will sort it out in Phase 0.' }

    Step 'Starting Claude Code'
    Note 'It builds the next unfinished phase, opens a PR and tells you how to test it.'
    Note 'Approve its commands as they come up, or press Shift+Tab to change how often it asks.'
    Set-Location $RepoDir
    $prompt = 'Follow .claude/skills/build/SKILL.md exactly, with no phase argument: build the next unfinished phase of docs/BUILD_PLAN.md. Setup is done: tools are installed, the repo is cloned, .venv exists and .env holds the OpenAI key.'
    claude --permission-mode acceptEdits $prompt
}

if (-not $env:GPW_KICKSTART_TEST) {
    try { Invoke-Kickstart }
    catch { Write-Host ''; Write-Host "Stopped: $($_.Exception.Message)" -ForegroundColor Red }
}
