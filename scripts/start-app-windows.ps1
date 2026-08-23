# Start script for AI Modeling Kobo (Windows).
# Called by Task Scheduler task "AIModelingKobo" at logon.
# Binds to 127.0.0.1 only; exposed via tailnet-only `tailscale serve --https=8000`.
# NOTE: keep this file ASCII-only (Windows PowerShell 5.1 misreads UTF-8 without BOM).
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
# claude.exe (~\.local\bin) and portable OpenSCAD are not on PATH; add them process-locally.
$env:Path = "$env:USERPROFILE\.local\bin;$env:LOCALAPPDATA\Programs\OpenSCAD-portable\openscad-2021.01;$env:Path"
$log = Join-Path $root 'data\server.log'
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
& "$root\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 *>> $log
