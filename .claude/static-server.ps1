<#
  Zero-dependency static file server for this project.

  Why this exists: the project is a single static index.html with no build
  tooling, and this machine has no Node and no real Python (the python.exe on
  PATH is the 0-byte Microsoft Store alias). System.Net.HttpListener ships with
  Windows PowerShell and binds http://localhost:<port>/ without elevation, so
  it is the only dependency-free way to serve the page over http:// instead of
  file:// — which matters because file:// has different CORS/origin behaviour
  than the CDN-loaded Tailwind/Chart.js the page relies on.

  Usage: powershell -NoProfile -ExecutionPolicy Bypass -File .claude/static-server.ps1 -Port 4173
#>
param(
  [int]$Port = 4173,
  [string]$Root = "."
)

$ErrorActionPreference = 'Stop'
$rootPath = (Resolve-Path $Root).Path

$mime = @{
  '.html' = 'text/html; charset=utf-8'
  '.htm'  = 'text/html; charset=utf-8'
  '.css'  = 'text/css; charset=utf-8'
  '.js'   = 'application/javascript; charset=utf-8'
  '.mjs'  = 'application/javascript; charset=utf-8'
  '.json' = 'application/json; charset=utf-8'
  '.map'  = 'application/json; charset=utf-8'
  '.svg'  = 'image/svg+xml'
  '.png'  = 'image/png'
  '.jpg'  = 'image/jpeg'
  '.jpeg' = 'image/jpeg'
  '.gif'  = 'image/gif'
  '.ico'  = 'image/x-icon'
  '.webp' = 'image/webp'
  '.woff' = 'font/woff'
  '.woff2'= 'font/woff2'
  '.csv'  = 'text/csv; charset=utf-8'
  '.txt'  = 'text/plain; charset=utf-8'
}

# Without this the project path ("aylık finansal analiz") is mangled in logs.
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }
# Child processes inherit this; without it the parser's Turkish output comes
# back through the redirect files as mojibake.
$env:PYTHONIOENCODING = 'utf-8'

function Write-Log($msg) {
  # [Console] rather than Write-Host so the line reliably lands on stdout
  # when the process is spawned with redirected output (preview_logs).
  [Console]::Out.WriteLine($msg)
  [Console]::Out.Flush()
}

$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add("http://localhost:$Port/")

try {
  $listener.Start()
}
catch [System.Net.HttpListenerException] {
  # Almost always a previous instance of this server that is still alive:
  # HttpListener prefixes are registered in http.sys (owned by System/PID 4),
  # so netstat blames PID 4 rather than the PowerShell process holding it.
  # Raw .NET stack traces make that look like a machine-level conflict.
  Write-Log "ERROR: cannot listen on http://localhost:$Port/ - $($_.Exception.Message)"
  Write-Log ""
  Write-Log "Port $Port is already registered, most likely by an earlier run of this server."
  Write-Log "Find it:  Get-CimInstance Win32_Process -Filter `"Name='powershell.exe'`" | Where-Object { `$_.CommandLine -like '*static-server.ps1*' }"
  Write-Log "Stop it:  Stop-Process -Id <ProcessId>"
  Write-Log "Or edit .claude/launch.json to use a different port (both -Port and \"port\" must match)."
  exit 1
}

Write-Log "Serving $rootPath"
Write-Log "Listening on http://localhost:$Port/"

# ── API ───────────────────────────────────────────────────────────────────────
# The dashboard can read a PDF in the browser but cannot parse one — that needs
# pdfplumber/pikepdf on the Python side. These endpoints bridge the two: the page
# POSTs the file, the server writes it into statements\ and runs the parser.

$pythonExe = Join-Path $rootPath '.venv\Scripts\python.exe'
$statementsDir = Join-Path $rootPath 'statements'
$dataDir = Join-Path $rootPath 'data'

function Send-Json($res, $obj, $status = 200) {
  $json = $obj | ConvertTo-Json -Depth 8 -Compress
  $bytes = [Text.Encoding]::UTF8.GetBytes($json)
  $res.StatusCode = $status
  $res.ContentType = 'application/json; charset=utf-8'
  $res.Headers.Add('Cache-Control', 'no-store')
  $res.ContentLength64 = $bytes.Length
  $res.OutputStream.Write($bytes, 0, $bytes.Length)
}

function Send-Raw-Json($res, $json, $status = 200) {
  $bytes = [Text.Encoding]::UTF8.GetBytes($json)
  $res.StatusCode = $status
  $res.ContentType = 'application/json; charset=utf-8'
  $res.Headers.Add('Cache-Control', 'no-store')
  $res.ContentLength64 = $bytes.Length
  $res.OutputStream.Write($bytes, 0, $bytes.Length)
}

function Read-Body($req) {
  $reader = New-Object IO.StreamReader($req.InputStream, [Text.Encoding]::UTF8)
  try { $reader.ReadToEnd() } finally { $reader.Dispose() }
}

# Model-supplied filenames are untrusted: strip any path, keep a safe charset.
function Get-SafeName($name) {
  $leaf = [IO.Path]::GetFileName([string]$name)
  $leaf = ($leaf -replace '[^\w\.\-]', '_')
  if ([string]::IsNullOrWhiteSpace($leaf) -or $leaf -notmatch '\.pdf$') {
    $leaf = "statement-$(Get-Date -Format yyyyMMdd-HHmmss).pdf"
  }
  return $leaf
}

# Do NOT use `& $exe ... 2>&1` here. In Windows PowerShell that wraps every
# stderr line from a native command in an ErrorRecord, which under
# $ErrorActionPreference='Stop' becomes a terminating error even when the
# process exits 0 — the parser's harmless "note:" line was turning a successful
# parse into an HTTP 500. Redirect to files instead.
function Invoke-Python($argList, $pdfPassword) {
  # The password goes through the environment, never the argument list.
  # -ArgumentList joins an array with spaces and quotes nothing, so a password
  # containing a double quote broke out of its own argument: a password of
  #   x" --out "C:\somewhere\evil.json
  # made the parser write a file outside the project. Verified, then fixed.
  # It also keeps the password out of the process list.
  $o = [IO.Path]::GetTempFileName()
  $e = [IO.Path]::GetTempFileName()
  if ($pdfPassword) { $env:EKSTRE_PDF_PASSWORD = $pdfPassword }
  try {
    $p = Start-Process -FilePath $pythonExe -ArgumentList $argList `
                       -WorkingDirectory $rootPath -NoNewWindow -Wait -PassThru `
                       -RedirectStandardOutput $o -RedirectStandardError $e
    $text = ((Get-Content $o -Raw -Encoding UTF8 -ErrorAction SilentlyContinue) + "`n" +
             (Get-Content $e -Raw -Encoding UTF8 -ErrorAction SilentlyContinue)).Trim()
    return @{ ok = ($p.ExitCode -eq 0); output = $text }
  } finally {
    Remove-Item $o, $e -Force -ErrorAction SilentlyContinue
    Remove-Item Env:\EKSTRE_PDF_PASSWORD -ErrorAction SilentlyContinue
  }
}

# The filename is already reduced to [\w.-] by Get-SafeName, so it cannot carry
# a quote or a separator of its own.
function Invoke-Parser($pdfPath, $password) {
  return Invoke-Python @('tools\parse_statement_local.py', "`"$pdfPath`"") $password
}

function Invoke-Inspector($pdfPath, $password) {
  return (Invoke-Python @('tools\inspect_pdf.py', "`"$pdfPath`"", '--pages', '1') $password).output
}

function Handle-Api($req, $res, $route) {
  switch -Regex ($route) {

    '^api/upload$' {
      if ($req.HttpMethod -ne 'POST') { Send-Json $res @{ error = 'POST required' } 405; return $true }
      if (-not (Test-Path $pythonExe)) {
        Send-Json $res @{ error = 'Python venv not found. Expected .venv\Scripts\python.exe' } 500; return $true
      }
      $body = Read-Body $req | ConvertFrom-Json
      $name = Get-SafeName $body.filename
      $bytes = [Convert]::FromBase64String($body.data)
      if ($bytes.Length -gt 25MB) { Send-Json $res @{ error = 'File larger than 25 MB' } 413; return $true }

      New-Item -ItemType Directory -Force -Path $statementsDir | Out-Null
      $dest = Join-Path $statementsDir $name
      [IO.File]::WriteAllBytes($dest, $bytes)
      Write-Log "upload: $name ($([math]::Round($bytes.Length/1KB)) KB)"

      $rel = "statements\$name"
      $parse = Invoke-Parser $rel $body.password

      if ($parse.ok) {
        Send-Json $res @{ status = 'parsed'; file = $name; output = $parse.output }
      }
      elseif ($parse.output -match 'password-protected|Wrong password') {
        Send-Json $res @{ status = 'needs_password'; file = $name; output = $parse.output }
      }
      elseif ($parse.output -match 'No template matches') {
        # Unknown bank: keep the PDF and hand back the masked layout so a
        # template can be written without the amounts ever leaving the machine.
        $structure = Invoke-Inspector $rel $body.password
        Send-Json $res @{ status = 'needs_template'; file = $name; output = $parse.output; structure = $structure }
      }
      else {
        Send-Json $res @{ status = 'error'; file = $name; output = $parse.output }
      }
      return $true
    }

    '^api/fetch-mail$' {
      if ($req.HttpMethod -ne 'POST') { Send-Json $res @{ error = 'POST required' } 405; return $true }
      if (-not (Test-Path $pythonExe)) {
        Send-Json $res @{ error = 'Python venv not found.' } 500; return $true
      }
      $body = $null
      try { $body = Read-Body $req | ConvertFrom-Json } catch { }
      $a = @('tools\fetch_mail.py', '--json')
      if ($body -and $body.check) { $a += '--check' }
      if ($body -and $body.months) { $a += @('--months', [string][int]$body.months) }

      # The OAuth consent step opens a browser and waits for a person, which a
      # web request must never sit through. The script only reaches that point
      # when it is unconfigured, and says so on stderr instead; either way the
      # last JSON line is the report.
      Write-Log ('fetch-mail: ' + ($a -join ' '))
      $r = Invoke-Python $a
      $json = $null
      foreach ($line in ($r.output -split "`n")) {
        $t = $line.Trim()
        if ($t.StartsWith('{') -and $t.EndsWith('}')) { $json = $t }
      }
      if ($json) {
        Send-Raw-Json $res $json
      } else {
        Send-Json $res @{ error = 'no_report'; output = $r.output } 500
      }
      return $true
    }

    '^api/statements$' {
      $items = @()
      if (Test-Path $dataDir) {
        Get-ChildItem $dataDir -Filter '*.json' | Where-Object { $_.BaseName -ne 'index' } | ForEach-Object {
          try {
            $j = Get-Content $_.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
            $items += @{
              file        = $_.Name
              period      = ($_.BaseName -split '__')[0]
              bank        = $j.bank
              month       = $j.statementMonth
              count       = @($j.transactions).Count
              source      = $j.source
              fetchedAt   = $j.fetchedAt
              totalExpense= $j.summary.totalExpense
              totalIncome = $j.summary.totalIncome
            }
          } catch { }
        }
      }
      # Every PDF in statements\ that no parsed file names as its source has
      # not been read at all. Silently leaving those out of the list is how a
      # statement goes missing without anyone noticing it is missing.
      $unread = @()
      if (Test-Path $statementsDir) {
        $sources = ($items | ForEach-Object { $_.source }) -join ' | '
        Get-ChildItem $statementsDir -Filter '*.pdf' | ForEach-Object {
          if ($sources -notlike ('*' + $_.Name + '*')) {
            $unread += @{ name = $_.Name; size = $_.Length
                          modified = $_.LastWriteTime.ToString('yyyy-MM-dd') }
          }
        }
      }
      Send-Json $res @{ statements = @($items | Sort-Object period -Descending)
                        unread = @($unread) }
      return $true
    }

    # -- Everything the user types lives here --------------------------------
    # Category fixes, custom categories, budgets, matched pairs, cash counts,
    # subscriptions, instalment notes. localStorage alone was not enough: the
    # desktop app does not keep it across a restart, so a session of manual
    # categorising vanished every time the window closed. Same machine, same
    # folder, no network -- just a file that survives.
    # Which bank logos are actually on disk. One question instead of one
    # failed request per bank, so the console stays clean when the folder is
    # empty. Only files the user put in logos\ are ever used.
    '^api/logos$' {
      $logoDir = Join-Path $rootPath 'logos'
      $names = @()
      if (Test-Path $logoDir) {
        # -Include needs a wildcard path; without one it silently returns nothing.
        $names = @(Get-ChildItem (Join-Path $logoDir '*') -File -Include *.png,*.jpg,*.jpeg,*.svg,*.webp |
                   ForEach-Object { $_.Name })
      }
      Send-Json $res @{ files = $names }
      return $true
    }

    '^api/state$' {
      # Deliberately NOT in data\: write_index() globs data\*.json and would
      # list this as a statement file for the dashboard to try to parse.
      $stateFile = Join-Path $rootPath 'user-state.json'
      if ($req.HttpMethod -eq 'GET') {
        if (Test-Path $stateFile) {
          Send-Raw-Json $res (Get-Content $stateFile -Raw -Encoding UTF8)
        } else {
          Send-Json $res @{}
        }
        return $true
      }
      if ($req.HttpMethod -ne 'POST') { Send-Json $res @{ error = 'GET or POST' } 405; return $true }
      $body = Read-Body $req
      if ([string]::IsNullOrWhiteSpace($body)) { Send-Json $res @{ error = 'Empty body' } 400; return $true }
      # Parse before writing: a malformed body must never replace a good file.
      try { $null = $body | ConvertFrom-Json }
      catch { Send-Json $res @{ error = 'Not valid JSON' } 400; return $true }
      # Keep one generation back, so even a bad write stays recoverable.
      if (Test-Path $stateFile) { Copy-Item $stateFile "$stateFile.bak" -Force }
      [IO.File]::WriteAllText($stateFile, $body, (New-Object Text.UTF8Encoding($false)))
      Write-Log ("saved user-state.json ({0} bytes)" -f $body.Length)
      Send-Json $res @{ status = 'saved'; bytes = $body.Length }
      return $true
    }

    '^api/statements/delete$' {
      if ($req.HttpMethod -ne 'POST') { Send-Json $res @{ error = 'POST required' } 405; return $true }
      $body = Read-Body $req | ConvertFrom-Json
      # Delete by filename: several banks share one month, so a period alone is
      # no longer unique.
      $file = [IO.Path]::GetFileName([string]$body.file)
      if ($file -notmatch '^[\w\-]+\.json$') { Send-Json $res @{ error = 'Bad file' } 400; return $true }
      $target = Join-Path $dataDir $file
      if (Test-Path $target) {
        Remove-Item $target -Force
        Write-Log "deleted data\$file"
        # Refresh the manifest the dashboard reads
        & $pythonExe -c "import sys; sys.path.insert(0, r'$rootPath\tools'); from statement_model import write_index; print(write_index(r'$dataDir'))" 2>&1 | Out-Null
        Send-Json $res @{ status = 'deleted'; file = $file }
      } else {
        Send-Json $res @{ error = 'Not found' } 404
      }
      return $true
    }
  }
  return $false
}

try {
  while ($listener.IsListening) {
    $ctx = $listener.GetContext()
    $req = $ctx.Request
    $res = $ctx.Response
    try {
      $rel = [Uri]::UnescapeDataString($req.Url.AbsolutePath).TrimStart('/')
      if ([string]::IsNullOrWhiteSpace($rel)) { $rel = 'index.html' }

      if ($rel -like 'api/*') {
        if (Handle-Api $req $res $rel) { continue }
        Send-Json $res @{ error = 'Unknown endpoint' } 404
        continue
      }

      # Secrets live in the project root, and the root is what gets served.
      # .gmail\ holds the Google OAuth client secret and the refresh token, and
      # user-state.json holds every decision the user has made — both were
      # fetchable at http://localhost:4173/.gmail/credentials.json, content and
      # all. Nothing under a dot-directory or dot-file is served; the state file
      # has its own API that the page uses instead.
      $firstSeg = ($rel -split '[/\\]')[0]
      if ($firstSeg.StartsWith('.') -or
          $rel -eq 'user-state.json' -or $rel -eq 'user-state.json.bak' -or
          $rel -eq 'mail-log.json') {
        Write-Log "blocked: $rel"
        Send-Json $res @{ error = 'Not available over HTTP' } 403
        continue
      }

      $full = Join-Path $rootPath ($rel -replace '/', '\')

      if ((Test-Path $full) -and (Get-Item $full).PSIsContainer) {
        $full = Join-Path $full 'index.html'
      }

      $resolved = $null
      if (Test-Path $full) { $resolved = (Resolve-Path $full).Path }

      # Path-traversal guard: never serve outside the project root.
      $inRoot = $resolved -and $resolved.StartsWith($rootPath, [StringComparison]::OrdinalIgnoreCase)

      # The check above reads the requested string. HttpListener happens to hand
      # over an already-normalised path, so "/data/../user-state.json" arrives
      # as "/user-state.json" and is caught — but that is .NET's behaviour, not
      # a promise. Check the path we are actually about to open as well, so the
      # guard does not quietly depend on it.
      if ($inRoot) {
        $rest = $resolved.Substring($rootPath.Length).TrimStart('\', '/')
        $leaf = [IO.Path]::GetFileName($resolved)
        if (($rest -split '\\') | Where-Object { $_.StartsWith('.') }) { $inRoot = $false }
        if ($leaf -in @('user-state.json', 'user-state.json.bak', 'mail-log.json')) { $inRoot = $false }
        if (-not $inRoot) { Write-Log "blocked (resolved): $rest" }
      }

      if ($inRoot -and -not (Get-Item $resolved).PSIsContainer) {
        $bytes = [IO.File]::ReadAllBytes($resolved)
        $ext = [IO.Path]::GetExtension($resolved).ToLower()
        $ct = $mime[$ext]
        if (-not $ct) { $ct = 'application/octet-stream' }
        $res.StatusCode = 200
        $res.ContentType = $ct
        # No caching, so an edit + reload always shows the new file.
        $res.Headers.Add('Cache-Control', 'no-store, no-cache, must-revalidate')
        $res.ContentLength64 = $bytes.Length
        # A HEAD request (the preview harness uses one as a readiness probe)
        # must send headers only — writing a body throws "bytes exceed
        # Content-Length" and surfaces as a spurious 500.
        if ($req.HttpMethod -ne 'HEAD') {
          $res.OutputStream.Write($bytes, 0, $bytes.Length)
        }
        Write-Log ("200 {0} {1}" -f $req.HttpMethod, $req.Url.AbsolutePath)
      }
      else {
        $body = [Text.Encoding]::UTF8.GetBytes("404 Not Found: $rel")
        $res.StatusCode = 404
        $res.ContentType = 'text/plain; charset=utf-8'
        $res.ContentLength64 = $body.Length
        if ($req.HttpMethod -ne 'HEAD') {
          $res.OutputStream.Write($body, 0, $body.Length)
        }
        Write-Log ("404 {0} {1}" -f $req.HttpMethod, $req.Url.AbsolutePath)
      }
    }
    catch {
      Write-Log "500 $($_.Exception.Message)"
      try { $res.StatusCode = 500 } catch { }
    }
    finally {
      try { $res.OutputStream.Close() } catch { }
    }
  }
}
finally {
  $listener.Stop()
  $listener.Close()
}
