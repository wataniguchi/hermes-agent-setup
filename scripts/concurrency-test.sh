#!/usr/bin/env bash
# concurrency-test.sh
PORT=8081
URL="http://127.0.0.1:${PORT}/v1/chat/completions"
PROMPT='{"model":"mlx-community/gemma-4-26b-a4b-it-4bit","messages":[{"role":"user","content":"Count slowly from 1 to 50, one number per line."}],"max_tokens":300}'

echo "== Sequential baseline (3 requests, one after another) =="
time (
  for i in 1 2 3; do
    curl -s -o /dev/null -w "req $i: %{time_total}s\n" $URL -H "Content-Type: application/json" -d "$PROMPT"
  done
)

echo
echo "== Concurrent (3 requests fired simultaneously) =="
time (
  for i in 1 2 3; do
    curl -s -o /dev/null -w "req $i: %{time_total}s\n" $URL -H "Content-Type: application/json" -d "$PROMPT" &
  done
  wait
)
