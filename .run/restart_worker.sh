#!/bin/zsh
# Local-only: cleanly restart the live provider_worker with a fresh WS.
cd /Users/eminkaragoz/Desktop/projects/Croon-RFQ || exit 1
mkdir -p .run

# Kill any previous worker (by saved pid and by module match).
if [[ -f .run/worker.pid ]]; then
  OLD=$(cat .run/worker.pid)
  kill "$OLD" 2>/dev/null && echo "killed old PID=$OLD" || echo "old PID=$OLD not running"
fi
pkill -f "croon.provider_worker" 2>/dev/null && echo "pkill matched stragglers" || true
sleep 2

export CROON_PROVIDER_DEBUG_EVENTS=1
export PYTHONPATH="$PWD"
nohup .venv/bin/python -m croon.provider_worker > .run/provider_worker.log 2>&1 &
echo $! > .run/worker.pid
sleep 9
echo "=== NEW WORKER_PID ==="
cat .run/worker.pid
echo "=== LOG (startup) ==="
tail -n 20 .run/provider_worker.log
