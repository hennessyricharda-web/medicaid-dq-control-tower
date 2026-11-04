<#
.SYNOPSIS
  Stops the live demo started by run_demo.ps1: the Tower, the agent/API, and the demo database (local PostgreSQL or the Docker container).
  It stops only the processes run_demo.ps1 recorded, plus whatever still listens on the demo ports.
  Pass -KeepDatabase to leave the database running.
#>
param([switch]$KeepDatabase)
$Root = $PSScriptRoot
$pidFile = Join-Path $Root ".demo_pids"
if (Test-Path $pidFile) {
  foreach ($id in Get-Content $pidFile) {
    if ($id) { & taskkill /PID $id /T /F *> $null }   # /T also stops child processes (node, uvicorn)
  }
  Remove-Item $pidFile -Force
}
foreach ($p in 3000, 8080, 8081) {
  Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
}
$pgFile = Join-Path $Root ".demo_pg"
if (-not $KeepDatabase -and (Test-Path $pgFile)) {
  $mode = (Get-Content $pgFile -Raw).Trim()
  if ($mode -like "native|*") {
    $parts = $mode.Split("|")
    & (Join-Path $parts[1] "pg_ctl.exe") -D $parts[2] -m fast stop *> $null
  } elseif ($mode -eq "docker") {
    docker stop dq-demo-pg *> $null
  }
  Remove-Item $pgFile -Force
}
Write-Host "Demo stopped."
