# Desktop shortcut target: make sure the server is up, then open the app.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$url = 'http://localhost:4173'

function Test-Up {
  try { (Invoke-WebRequest "$url/api/health" -Method Head -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200 }
  catch { $false }
}

if (-not (Test-Up)) {
  Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$root\start-server.ps1`""
  $deadline = (Get-Date).AddSeconds(45)
  while (-not (Test-Up) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 500 }
}

Start-Process $url
