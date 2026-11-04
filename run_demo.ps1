<#
.SYNOPSIS
  Starts the full live demo on Windows. It brings up three things:
  - the control-plane database (Postgres 16; Docker is NOT required);
  - the DQ agent and API (FastAPI on :8080, test sign-in service on :8081);
  - the Command Tower (Next.js on :3000).

.DESCRIPTION
  Prerequisites: Python 3.11+, Node.js 20+, and PostgreSQL 16 binaries. These need no installer, no admin rights
  and no Windows service; the script uses them in this order:
    1. -PgBin <folder containing pg_ctl.exe>
    2. C:\demo\pgsql\bin. To set this up, unzip the PostgreSQL 16 "binaries" zip from
       https://www.enterprisedb.com/download-postgresql-binaries into C:\demo, so the result is C:\demo\pgsql\bin\pg_ctl.exe.
    3. pg_ctl.exe on PATH, e.g. from an existing PostgreSQL install.
    4. Docker, only if it is installed and running.
  The database files go in .pgdata inside the repo (gitignored). The server listens on 127.0.0.1:55432 only.
  This is a demo stack: the database uses trust authentication on localhost only, and the sign-in service is a
  test IdP. Do not expose these ports.

  The demo starts in nightly mode: 15 of the 22 synthetic nights are preloaded, and the operator
  (sign in as "maker") presses "Run next night" in the Tower to process the rest.

.PARAMETER Tenant
  Optional path to a tenant profile YAML kept OUTSIDE the repo (organisation name and approver organisation).
  Defaults to ..\_private_state_a_sources\tenant_profile.yaml if that file exists.

.PARAMETER PreloadNights
  How many nights to load at startup (0-22). Default 15. Use 0 to run every night from the Tower.

.PARAMETER FullLoad
  Load all nights at startup, with no nightly simulator.

.PARAMETER Dev
  Install the "dev" extra (pytest, ruff) alongside the usual [dbt] extra.

.PARAMETER DbOnly
  Start portable PostgreSQL only, then exit. Does not start the API or the Tower.

.EXAMPLE
  .\run_demo.ps1
  .\run_demo.ps1 -PreloadNights 5
  .\run_demo.ps1 -Dev
  .\run_demo.ps1 -DbOnly
  .\stop_demo.ps1
#>
param(
  [string]$Tenant = "",
  [string]$PgBin = "",
  [ValidateRange(0, 22)][int]$PreloadNights = 15,
  [switch]$FullLoad,
  [switch]$Dev,
  [switch]$DbOnly
)
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$PgContainer = "dq-demo-pg"
$PgPort = 55432

function Need($cmd, $hint) {
  if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { throw "$cmd not found. $hint" }
}
function Wait-Http($url, $seconds) {
  $deadline = (Get-Date).AddSeconds($seconds)
  while ((Get-Date) -lt $deadline) {
    try { Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3 | Out-Null; return $true } catch { Start-Sleep -Seconds 2 }
  }
  return $false
}
function Port-Busy($port) {
  return [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

Write-Host "== DQ Command Tower: live demo ==" -ForegroundColor Cyan

# 1. Prerequisites
Need python "Install Python 3.11 or later (winget install Python.Python.3.12)."
if (-not $DbOnly) { Need npm "Install Node.js 20 or later (winget install OpenJS.NodeJS.LTS)." }
$pyver = & python -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ([version]$pyver -lt [version]"3.11") { throw "Python $pyver found; 3.11 or later is required." }
$ports = if ($DbOnly) { @() } else { 8080, 8081, 3000 }
foreach ($p in $ports) {
  if (Port-Busy $p) { throw "Port $p is already in use. Run .\stop_demo.ps1 (or close whatever holds it) and retry." }
}

# 2. Database: local PostgreSQL 16 binaries (preferred, no Docker), else Docker if present.
#    Trust auth on 127.0.0.1 only: a demo stack; never expose this port.
$PgMode = $null
$candidates = @()
if ($PgBin) { $candidates += $PgBin }
$candidates += (Join-Path (Split-Path $Root -Parent) "pgsql\bin")
$found = Get-Command pg_ctl -ErrorAction SilentlyContinue
if ($found) { $candidates += (Split-Path $found.Source -Parent) }
foreach ($c in $candidates) {
  if ($c -and (Test-Path (Join-Path $c "pg_ctl.exe"))) { $PgBin = $c; $PgMode = "native"; break }
}
if (-not $PgMode -and (Get-Command docker -ErrorAction SilentlyContinue)) {
  docker info *> $null
  if ($LASTEXITCODE -eq 0) { $PgMode = "docker" }
}
if (-not $PgMode) {
  throw @"
No PostgreSQL 16 found, and Docker is not available. To set one up without Docker or admin rights:
  1. Download the PostgreSQL 16 Windows x86-64 'binaries' zip from https://www.enterprisedb.com/download-postgresql-binaries
  2. Unzip it into C:\demo, so that C:\demo\pgsql\bin\pg_ctl.exe exists.
  3. Run .\run_demo.ps1 again (or pass -PgBin <folder with pg_ctl.exe>).
"@
}

if ($PgMode -eq "native") {
  $pgdata = Join-Path $Root ".pgdata"
  $pgctl = Join-Path $PgBin "pg_ctl.exe"
  if (-not (Test-Path (Join-Path $pgdata "PG_VERSION"))) {
    Write-Host "Initialising a local demo database in .pgdata (one-time) ..."
    & (Join-Path $PgBin "initdb.exe") -D $pgdata -U dq_gov -A trust -E UTF8 --no-locale | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "initdb failed." }
  }
  & $pgctl -D $pgdata status *> $null
  if ($LASTEXITCODE -ne 0) {
    Write-Host "Starting PostgreSQL ($PgBin) on 127.0.0.1:$PgPort ..."
    & $pgctl -D $pgdata -l (Join-Path $Root "demo_pg.log") -o "-p $PgPort -h 127.0.0.1" -w start | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "PostgreSQL did not start. See demo_pg.log in $Root." }
  }
  $ready = $false
  for ($i = 0; $i -lt 60; $i++) {
    & (Join-Path $PgBin "pg_isready.exe") -h 127.0.0.1 -p $PgPort -U dq_gov *> $null
    if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    Start-Sleep -Seconds 1
  }
  if (-not $ready) { throw "PostgreSQL is not accepting connections. See demo_pg.log." }
  Set-Content -Path (Join-Path $Root ".demo_pg") -Value "native|$PgBin|$pgdata"
} else {
  $exists = docker ps -a --filter "name=^$PgContainer$" --format "{{.Names}}"
  if (-not $exists) {
    Write-Host "Starting Postgres 16 container ($PgContainer) on localhost:$PgPort ..."
    docker run -d --name $PgContainer -p "127.0.0.1:${PgPort}:5432" `
      -e POSTGRES_USER=dq_gov -e POSTGRES_HOST_AUTH_METHOD=trust postgres:16 | Out-Null
  } else {
    docker start $PgContainer | Out-Null
  }
  $ready = $false
  for ($i = 0; $i -lt 60; $i++) {
    docker exec $PgContainer pg_isready -U dq_gov *> $null
    if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    Start-Sleep -Seconds 1
  }
  if (-not $ready) { throw "Postgres did not become ready. Check: docker logs $PgContainer" }
  Set-Content -Path (Join-Path $Root ".demo_pg") -Value "docker"
}

if ($DbOnly) {
  Write-Host "PostgreSQL is up on 127.0.0.1:$PgPort (-DbOnly: not starting the API or Tower)." -ForegroundColor Green
  return
}

# 3. Python environment
$venv = Join-Path $Root ".venv"
$py = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $py)) {
  Write-Host "Creating Python virtual environment (.venv) ..."
  python -m venv $venv
}
Write-Host "Installing the agent (first run takes a few minutes) ..."
& $py -m pip install --quiet --upgrade pip
$extras = if ($Dev) { "dbt,dev" } else { "dbt" }
& $py -m pip install --quiet -e "$Root[$extras]"
if ($LASTEXITCODE -ne 0) { throw "pip install failed." }

# 4. Agent + API (its own window, so you can watch the log)
if (-not $Tenant) {
  $default = Join-Path (Split-Path $Root -Parent) "_private_state_a_sources\tenant_profile.yaml"
  if (Test-Path $default) { $Tenant = $default }
}
$demoArgs = @("-m", "dqagent.cli.main", "demo", "--data", "synthetic/out/w1",
              "--pg-host", "127.0.0.1", "--pg-port", "$PgPort", "--duckdb", "dq_demo.duckdb")
if (-not $FullLoad) { $demoArgs += @("--nightly", "--preload-nights", "$PreloadNights") }
if ($Tenant) { $demoArgs += @("--tenant", $Tenant); Write-Host "Tenant profile: $Tenant" }
$env:DQ_ALLOW_TEST_GENESIS = "1"
Write-Host "Starting the DQ agent and API (loading synthetic deliveries; this takes a few minutes) ..."
$api = Start-Process -FilePath $py -ArgumentList $demoArgs -WorkingDirectory $Root -PassThru `
  -RedirectStandardOutput (Join-Path $Root "demo_api.log") -RedirectStandardError (Join-Path $Root "demo_api.err.log") -WindowStyle Minimized
Set-Content -Path (Join-Path $Root ".demo_pids") -Value $api.Id
if (-not (Wait-Http "http://localhost:8080/api/v1/tenant" 900)) {
  throw "The API did not come up within 15 minutes. See demo_api.log and demo_api.err.log in $Root."
}
Write-Host "API is up on http://localhost:8080" -ForegroundColor Green

# 5. Command Tower (live mode)
$tower = Join-Path $Root "tower"
if (-not (Test-Path (Join-Path $tower "node_modules"))) {
  Write-Host "Installing Tower dependencies ..."
  Push-Location $tower; npm install --no-audit --no-fund; Pop-Location
}
$env:NEXT_PUBLIC_DQ_MOCK = "0"
$web = Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "npm run dev" -WorkingDirectory $tower -PassThru `
  -RedirectStandardOutput (Join-Path $Root "demo_tower.log") -RedirectStandardError (Join-Path $Root "demo_tower.err.log") -WindowStyle Minimized
Add-Content -Path (Join-Path $Root ".demo_pids") -Value $web.Id
if (-not (Wait-Http "http://localhost:3000/login" 180)) { throw "The Tower did not come up. See demo_tower.log." }

Write-Host ""
Write-Host "Command Tower: http://localhost:3000" -ForegroundColor Green
Write-Host "Sign in as: maker (proposes, runs nights) | checker, checker2 (State approvers) | auditor (read-only)"
Write-Host "Stop everything with: .\stop_demo.ps1"
Start-Process "http://localhost:3000"
