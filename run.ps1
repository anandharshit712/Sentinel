<#
.SYNOPSIS
  Sentinel one-command launcher: DB migrate -> build dashboard -> Neuro-SAN (:8080) + Gateway (:8000).

.EXAMPLE
  .\run.ps1              # bring the whole stack up (servers get their own log windows), open browser
  .\run.ps1 -OneWindow   # run both servers in THIS terminal (interleaved logs), Ctrl-C stops both
  .\run.ps1 -Fresh       # force-rebuild the dashboard first
  .\run.ps1 -Demo        # after startup, run both demo runs (scripts/verify_c.py)
  .\run.ps1 -Stop        # kill whatever is on :8080 and :8000, then exit
  .\run.ps1 -NoBrowser   # don't auto-open the dashboard
#>
[CmdletBinding()]
param(
  [switch]$OneWindow,
  [switch]$Fresh,
  [switch]$Demo,
  [switch]$Stop,
  [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Py   = Join-Path $Root '.venv\Scripts\python.exe'

function Stop-Port([int]$Port) {
  Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -Expand OwningProcess -Unique |
    ForEach-Object { try { Stop-Process -Id $_ -Force -ErrorAction Stop; Write-Host "  killed pid $_ on :$Port" } catch {} }
}

function Test-Port([int]$Port) {
  try { $c = [Net.Sockets.TcpClient]::new(); $c.Connect('localhost', $Port); $c.Close(); $true } catch { $false }
}

function Wait-Port([int]$Port, [string]$Name, [int]$TimeoutSec = 120) {
  $end = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $end) { if (Test-Port $Port) { Write-Host "  $Name up on :$Port" -Foreground Green; return } ; Start-Sleep -Milliseconds 500 }
  throw "$Name did not come up on :$Port within ${TimeoutSec}s"
}

function Show-Ready {
  Write-Host ''
  Write-Host '  Dashboard : http://localhost:8000/' -Foreground Green
  Write-Host '  Gateway   : http://localhost:8000/api/v1/runs' -Foreground DarkGray
  Write-Host '  Demo      : .\.venv\Scripts\python.exe scripts\verify_c.py  (or run.ps1 -Demo)' -Foreground DarkGray
  Write-Host ''
  if (-not $NoBrowser) { Start-Process 'http://localhost:8000/' }
}

# --- stop mode ---------------------------------------------------------------
if ($Stop) { Write-Host '==> Stopping servers'; Stop-Port 8000; Stop-Port 8080; Write-Host 'stopped.'; return }

# --- preflight ---------------------------------------------------------------
if (-not (Test-Path $Py)) { throw ".venv python not found at $Py — create the venv first (see README)." }
Set-Location $Root
$env:PYTHONPATH = '.'
$env:GATEWAY_PORT = '8000'

# Windows MAX_PATH (260): deeply-nested repos fail `git clone` with exit 128 ("Filename too long").
# core.longpaths lets git use extended-length paths. --global needs no admin; covers all clones this user runs.
try { git config --global core.longpaths true; Write-Host '==> git core.longpaths enabled (long-path clones)' -Foreground Cyan }
catch { Write-Host '  (could not set git core.longpaths — is git on PATH?)' -Foreground Yellow }

# --- 1. DB schema (idempotent) ----------------------------------------------
Write-Host '==> DB migrate (alembic upgrade head)' -Foreground Cyan
& $Py -m alembic -c db\alembic.ini upgrade head

# --- 2. dashboard build (only if missing, or -Fresh) ------------------------
$dist = Join-Path $Root 'frontend\dist\index.html'
if ($Fresh -or -not (Test-Path $dist)) {
  Write-Host '==> Build dashboard' -Foreground Cyan
  Push-Location (Join-Path $Root 'frontend')
  if (-not (Test-Path 'node_modules')) { npm install }
  npm run build
  Pop-Location
} else { Write-Host '==> Dashboard already built (use -Fresh to rebuild)' -Foreground Cyan }

# --- 3. servers --------------------------------------------------------------
Write-Host '==> Neuro-SAN network (:8080)' -Foreground Cyan
Stop-Port 8080
Write-Host '==> Gateway (:8000)' -Foreground Cyan
Stop-Port 8000

if ($OneWindow) {
  # both servers in THIS console (interleaved logs); Ctrl-C stops both.
  $srv = Start-Process $Py 'scripts\run_server.py'  -WorkingDirectory $Root -NoNewWindow -PassThru
  Wait-Port 8080 'Neuro-SAN'
  $gw  = Start-Process $Py 'scripts\run_gateway.py' -WorkingDirectory $Root -NoNewWindow -PassThru
  Wait-Port 8000 'Gateway'
  Show-Ready
  if ($Demo) { & $Py scripts\verify_c.py }
  Write-Host 'Running. Press Ctrl-C to stop both servers.' -Foreground Yellow
  try { Wait-Process -Id $srv.Id, $gw.Id } finally { Stop-Port 8000; Stop-Port 8080 }
} else {
  # each server in its own window so logs stay readable (recommended for the demo).
  Start-Process $Py 'scripts\run_server.py'  -WorkingDirectory $Root
  Wait-Port 8080 'Neuro-SAN'
  Start-Process $Py 'scripts\run_gateway.py' -WorkingDirectory $Root
  Wait-Port 8000 'Gateway'
  Show-Ready
  if ($Demo) { & $Py scripts\verify_c.py }
  Write-Host 'Servers run in their own windows. Stop with: .\run.ps1 -Stop' -Foreground Yellow
}
