# Starts AIFinance Pro (the multi-user server) on http://localhost:4173.
#
# 4173 was the single-user PowerShell server's port for the whole life of
# this project, so it is the address in every bookmark and habit. Rather
# than teach a new port, the real server takes the old one over: whatever
# is holding 4173 is stopped first, then uvicorn binds it.
#
# Secrets come from .env.local (gitignored), one KEY=VALUE per line:
#   AIFP_ADMIN_EMAILS=you@example.com
#   AIFP_DEMO_PASSWORD=a-real-password-of-12-plus-chars
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File start-server.ps1

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$port = 4173

$envFile = Join-Path $root '.env.local'
if (Test-Path $envFile) {
  Get-Content $envFile | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') {
      [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2].Trim('"'), 'Process')
    }
  }
} else {
  Write-Warning ".env.local not found - the creator dashboard and demo account will be off."
}

# Free the port. The old static server is an HttpListener, which http.sys
# owns on behalf of a PowerShell process; the socket says "System", so the
# process is found by its command line instead.
Get-CimInstance Win32_Process | Where-Object {
  $_.CommandLine -match 'static-server\.ps1' -or
  ($_.CommandLine -match "uvicorn.*--port $port")
} | ForEach-Object {
  Write-Host "Stopping PID $($_.ProcessId): $($_.Name)"
  Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
  Where-Object { $_.OwningProcess -gt 4 } |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

$py = Join-Path $root '.venv\Scripts\python.exe'
$log = Join-Path $root '.server.log'
$p = Start-Process -FilePath $py `
  -ArgumentList '-u', '-m', 'uvicorn', 'server.app:app', '--host', '127.0.0.1', '--port', "$port" `
  -WorkingDirectory $root -RedirectStandardOutput $log -RedirectStandardError "$log.err" `
  -WindowStyle Hidden -PassThru

Start-Sleep -Seconds 6
if ($p.HasExited) {
  Write-Error "Server exited. Last lines of $log.err:`n$(Get-Content "$log.err" -Tail 15 | Out-String)"
}
Write-Host "AIFinance Pro is running: http://localhost:$port  (PID $($p.Id))"
Write-Host "Log: $log.err"
