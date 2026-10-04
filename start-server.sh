#!/usr/bin/env bash
# Starts AIFinance Pro (the multi-user server) on http://localhost:4173.
# macOS/Linux counterpart of start-server.ps1.
#
# Secrets come from .env.local (gitignored), one KEY=VALUE per line:
#   AIFP_ADMIN_EMAILS=you@example.com
#   AIFP_DEMO_PASSWORD=a-real-password-of-12-plus-chars
#
#   ./start-server.sh          # start in the background, log to .server.log.err
#   ./start-server.sh --fg     # run in the foreground (Ctrl-C stops it)
set -euo pipefail
root="$(cd "$(dirname "$0")" && pwd)"
cd "$root"
port=4173

if [ -f .env.local ]; then
  set -a; . ./.env.local; set +a
else
  echo "warning: .env.local not found - the creator dashboard and demo account will be off." >&2
fi

py="$root/.venv/bin/python"
if [ ! -x "$py" ]; then
  echo "No .venv yet. Run: uv venv --python 3.12 .venv && uv pip install -r requirements.txt" >&2
  exit 1
fi

# Free the port if a previous server still holds it.
pids="$(lsof -ti tcp:$port -sTCP:LISTEN || true)"
if [ -n "$pids" ]; then
  echo "Stopping PID(s) on :$port: $pids"
  kill $pids 2>/dev/null || true
  sleep 2
fi

args=(-u -m uvicorn server.app:app --host 127.0.0.1 --port "$port")
if [ "${1:-}" = "--fg" ]; then
  exec "$py" "${args[@]}"
fi

log="$root/.server.log"
nohup "$py" "${args[@]}" >"$log" 2>"$log.err" &
pid=$!
sleep 4
if ! kill -0 "$pid" 2>/dev/null; then
  echo "Server exited. Last lines of $log.err:" >&2
  tail -15 "$log.err" >&2
  exit 1
fi
echo "AIFinance Pro is running: http://localhost:$port  (PID $pid)"
echo "Log: $log.err"
