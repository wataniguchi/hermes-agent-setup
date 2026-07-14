#!/usr/bin/env bash
# Starts both mlx_lm.server processes as background jobs.
# For a manual/foreground test. For auto-start at login, use
# launchagent/com.mlx-servers.plist instead (see README.md §8).
set -euo pipefail

LOG_DIR="$HOME/Library/Logs"
mkdir -p "$LOG_DIR"

echo "== Starting primary reasoning model on :8081 =="
nohup mlx_lm.server \
  --model mlx-community/gemma-4-26b-a4b-it-4bit \
  --port 8081 \
  > "$LOG_DIR/mlx-primary.log" 2>&1 &
echo "PID: $!"

echo "== Starting fast sub-agent model on :8082 =="
nohup mlx_lm.server \
  --model mlx-community/gemma-4-e4b-it-4bit \
  --port 8082 \
  > "$LOG_DIR/mlx-fast.log" 2>&1 &
echo "PID: $!"

echo
echo "== Waiting for both to become healthy =="
for port in 8081 8082; do
  for i in $(seq 1 30); do
    if curl -sf "http://127.0.0.1:${port}/v1/models" >/dev/null 2>&1; then
      echo "Port $port: ready"
      break
    fi
    sleep 1
  done
done

echo
echo "Logs: $LOG_DIR/mlx-primary.log, $LOG_DIR/mlx-fast.log"
echo "Stop both with: pkill -f 'mlx_lm.server'"
