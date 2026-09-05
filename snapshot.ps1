<#
  Snapshot the files git deliberately does NOT track.

  Git is the shared memory between tools, but personal data is gitignored on
  purpose — statements, categorisations, the rules profile, the database. That
  keeps it off any remote, and it also means git cannot bring it back if
  something deletes it. This does.

  Snapshots land OUTSIDE the project folder, so anything that happens inside
  the folder cannot reach them.

      powershell -NoProfile -ExecutionPolicy Bypass -File snapshot.ps1
      powershell -NoProfile -ExecutionPolicy Bypass -File snapshot.ps1 -List
#>
param([switch]$List)

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$store = Join-Path (Split-Path -Parent $root) 'aifinance-snapshots'

if ($List) {
  if (Test-Path $store) { Get-ChildItem $store | Sort-Object Name -Descending | Select-Object Name, LastWriteTime }
  else { "No snapshots yet." }
  return
}

$stamp = Get-Date -Format 'yyyy-MM-dd_HHmm'
$dest = Join-Path $store $stamp
New-Item -ItemType Directory -Force -Path $dest | Out-Null

# Everything that is irreplaceable and not in git.
$items = @('data', 'statements', 'profile.json', 'user-state.json',
           'mail-log.json', 'aifinance.db', '.gmail')
$saved = 0
foreach ($i in $items) {
  $src = Join-Path $root $i
  if (Test-Path $src) {
    Copy-Item $src -Destination $dest -Recurse -Force
    $saved++
  }
}

# Which commit this data belonged to, so a restore can be matched to code.
try { (git -C $root rev-parse HEAD) | Set-Content (Join-Path $dest 'COMMIT.txt') } catch {}

"Snapshot $stamp -> $dest  ($saved item(s))"
Get-ChildItem $store | Sort-Object Name -Descending | Select-Object -Skip 10 | Remove-Item -Recurse -Force
