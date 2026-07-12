#!/bin/zsh
# Local-only helper to start the live provider_worker in the background.
# Not part of the app; safe to keep out of git.
cd /Users/eminkaragoz/Desktop/projects/Croon-RFQ || exit 1
mkdir -p .run
export CROON_PROVIDER_DEBUG_EVENTS=1
export PYTHONPATH="$PWD"
nohup .venv/bin/python -m croon.provider_worker > .run/provider_worker.log 2>&1 &
echo $! > .run/worker.pid
sleep 8
echo "=== WORKER_PID ==="
cat .run/worker.pid
echo "=== LOG (startup) ==="
tail -n 25 .run/provider_worker.log
