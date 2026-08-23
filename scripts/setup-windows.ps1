# One-shot setup for running AI Modeling Kobo on a Windows mini PC.
# Run from anywhere:  powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1
# Prerequisites (install manually, each is a normal installer):
#   - Python 3.11+          https://www.python.org/downloads/  (check "Add to PATH")
#   - Git                   https://git-scm.com/
#   - Tailscale (logged in) https://tailscale.com/download
#   - Claude Code (logged in with the Max account): irm https://claude.ai/install.ps1 | iex
# NOTE: keep this file ASCII-only (Windows PowerShell 5.1 misreads UTF-8 without BOM).

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$ok = @(); $warn = @()

function Find-Exe([string[]]$candidates) {
    foreach ($c in $candidates) {
        if ($c -notmatch '[\\/]') { $cmd = Get-Command $c -ErrorAction SilentlyContinue; if ($cmd) { return $cmd.Source } }
        elseif (Test-Path $c) { return $c }
    }
    return $null
}

# --- 1. prerequisite checks -------------------------------------------------
$python = Find-Exe @('python')
if (-not $python) { throw 'Python not found on PATH. Install it first (check "Add to PATH").' }
$ok += "Python: $python"

$tailscaleExe = Find-Exe @('tailscale', 'C:\Program Files\Tailscale\tailscale.exe')
if ($tailscaleExe) { $ok += "Tailscale: $tailscaleExe" } else { $warn += 'Tailscale not found - phone access will not be configured.' }

$claudeExe = Find-Exe @('claude', "$env:USERPROFILE\.local\bin\claude.exe")
if ($claudeExe) { $ok += "Claude Code: $claudeExe" } else { $warn += 'Claude Code CLI not found - install it and log in, or the AI backend will fail.' }

if ([Environment]::GetEnvironmentVariable('ANTHROPIC_API_KEY','User') -or [Environment]::GetEnvironmentVariable('ANTHROPIC_API_KEY','Machine') -or $env:ANTHROPIC_API_KEY) {
    $warn += 'ANTHROPIC_API_KEY is set on this machine. Remove it to use the Max subscription instead of pay-per-use API.'
}

# --- 2. venv + dependencies -------------------------------------------------
if (-not (Test-Path "$root\.venv\Scripts\python.exe")) { & $python -m venv "$root\.venv" }
& "$root\.venv\Scripts\python.exe" -m pip install --quiet -r "$root\requirements.txt" bambulabs_api
$ok += 'Python dependencies installed (.venv)'

# --- 3. .env ----------------------------------------------------------------
if (-not (Test-Path "$root\.env")) { Copy-Item "$root\.env.example" "$root\.env"; $ok += '.env created from .env.example' }
else { $ok += '.env already exists (kept as is)' }

# --- 4. OpenSCAD (portable, no admin needed) --------------------------------
$scadDir = "$env:LOCALAPPDATA\Programs\OpenSCAD-portable\openscad-2021.01"
$openscad = Find-Exe @('openscad', "$scadDir\openscad.exe", 'C:\Program Files\OpenSCAD\openscad.exe')
if (-not $openscad) {
    Write-Host 'Downloading portable OpenSCAD (~21 MB)...'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $zip = "$env:TEMP\OpenSCAD-portable.zip"
    Invoke-WebRequest -UseBasicParsing 'https://files.openscad.org/OpenSCAD-2021.01-x86-64.zip' -OutFile $zip -TimeoutSec 600
    Expand-Archive $zip "$env:LOCALAPPDATA\Programs\OpenSCAD-portable" -Force
    Remove-Item $zip
    $openscad = "$scadDir\openscad.exe"
}
$ok += "OpenSCAD: $openscad"

# --- 5. auto-start task (runs scripts\start-app-windows.ps1 at logon) -------
# Two triggers: at logon, plus every 15 min as a self-heal. MultipleInstances=IgnoreNew
# makes the repeating trigger a no-op while the app is alive, and a restart after a crash
# (e.g. the OOM killer on a memory-tight machine).
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$root\scripts\start-app-windows.ps1`""
$atLogon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$selfHeal = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'AIModelingKobo' -Action $action -Trigger @($atLogon, $selfHeal) -Settings $settings -Force | Out-Null
$ok += 'Scheduled task "AIModelingKobo" registered (logon + 15-min self-heal)'

# --- 6. phone access via Tailscale (tailnet-only HTTPS) ---------------------
$appUrl = 'http://127.0.0.1:8000'
if ($tailscaleExe) {
    & $tailscaleExe serve --bg --https=8000 http://127.0.0.1:8000 | Out-Null
    try {
        $dns = ((& $tailscaleExe status --json | ConvertFrom-Json).Self.DNSName).TrimEnd('.')
        $appUrl = "https://${dns}:8000"
    } catch { $warn += 'Could not read tailnet DNS name; check "tailscale serve status" for the URL.' }
    $ok += 'Tailscale serve enabled on https port 8000 (tailnet only, not public)'
}

# --- 7. start now and verify ------------------------------------------------
Start-ScheduledTask -TaskName 'AIModelingKobo'
Start-Sleep -Seconds 8
try {
    $r = Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:8000/' -TimeoutSec 15
    $ok += "App is running (HTTP $($r.StatusCode))"
} catch {
    $warn += "App did not answer on 127.0.0.1:8000 - check data\server.log"
}

# --- summary ----------------------------------------------------------------
Write-Host ''
Write-Host '===== setup result ====='
$ok   | ForEach-Object { Write-Host "  OK  $_" }
$warn | ForEach-Object { Write-Host "  !!  $_" -ForegroundColor Yellow }
Write-Host ''
Write-Host "Open this on your phone (Tailscale ON) and use 'Add to Home Screen':"
Write-Host "  $appUrl"
