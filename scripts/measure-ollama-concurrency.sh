#!/usr/bin/env bash
# measure-ollama-concurrency.sh — fires N concurrent no-op generations
# against one Ollama model tag, then polls `ollama ps` while they're
# in flight so you can read real per-copy resident memory under actual
# concurrent load, not just at idle. Exactly the "measure, don't guess"
# step referenced for sizing a quantized qwen3.8:27b subagent tag.
#
# Usage:
#   ./measure-ollama-concurrency.sh <model-tag> [count] [num_predict] [host]
#
#   model-tag    required — e.g. qwen3.8:27b-delegate
#   count        how many concurrent requests to fire (default 3)
#   num_predict  tokens to generate per request — bigger = longer
#                window to observe `ollama ps` mid-flight (default 200)
#   host         Ollama endpoint (default http://localhost:11434)
#
# What it does NOT do: change OLLAMA_NUM_PARALLEL/OLLAMA_MAX_LOADED_MODELS
# for you, or decide how many copies actually fit — that's your call,
# made from the real numbers this prints.

set -euo pipefail

MODEL="${1:?usage: $0 <model-tag> [count] [num_predict] [host]}"
COUNT="${2:-3}"
NUM_PREDICT="${3:-200}"
HOST="${4:-http://localhost:11434}"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

echo "== Firing $COUNT concurrent requests against '$MODEL' ($HOST) =="
echo "   num_predict=$NUM_PREDICT (raise this if requests finish before you"
echo "   get a few 'ollama ps' reads in)"
echo

BATCH_START="$(date +%s.%N)"
echo "$BATCH_START" > "$TMPDIR/batch_start.txt"

PIDS=()
for i in $(seq 1 "$COUNT"); do
  (
    START="$(date +%s.%N)"
    curl -s "$HOST/api/generate" \
      -d "{\"model\": \"$MODEL\", \"prompt\": \"Reply with just the word done.\", \
           \"stream\": false, \"keep_alive\": \"5m\", \
           \"options\": {\"num_predict\": $NUM_PREDICT}}" \
      > "$TMPDIR/resp_$i.json" 2>"$TMPDIR/err_$i.log"
    END="$(date +%s.%N)"
    echo "$START $END" > "$TMPDIR/times_$i.txt"
  ) &
  PIDS+=("$!")
done

echo "== Launched. Polling 'ollama ps' every 2s while requests are in flight =="
echo

ANY_ALIVE=1
POLLS=0
while [[ "$ANY_ALIVE" -eq 1 && "$POLLS" -lt 30 ]]; do
  ANY_ALIVE=0
  for pid in "${PIDS[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      ANY_ALIVE=1
    fi
  done
  echo "--- t=${POLLS}x2s ---"
  ollama ps || echo "(ollama ps failed — is ollama on PATH and the daemon running?)"
  echo
  sleep 2
  POLLS=$((POLLS + 1))
done

for pid in "${PIDS[@]}"; do
  wait "$pid" || true
done

echo "== Done. Per-request results =="
echo "(eval_count/eval_tps come from Ollama's own response fields — the"
echo " REAL tokens generated and generation-only speed, not an assumed"
echo " num_predict count. This model reasons before answering, so actual"
echo " tokens generated varies a lot request to request regardless of"
echo " concurrency — that variance is real model behavior, not noise.)"
for i in $(seq 1 "$COUNT"); do
  if [[ -s "$TMPDIR/resp_$i.json" ]]; then
    python3 -c "
import json
d = json.load(open('$TMPDIR/resp_$i.json'))
eval_count = d.get('eval_count', 0)
eval_s = d.get('eval_duration', 0) / 1e9
tps = eval_count / eval_s if eval_s > 0 else 0
print(f\"request $i: '{d.get('response','').strip()[:40]}' \"
      f\"| total_ms={d.get('total_duration',0)//1000000} \"
      f\"| eval_count={eval_count} | eval_only_tok_s={tps:.1f}\")
" 2>/dev/null || echo "request $i: (could not parse response)"
  else
    echo "request $i: no response — $(cat "$TMPDIR/err_$i.log" 2>/dev/null)"
  fi
done

echo
echo "== Overlap analysis (from real start/end wall-clock timestamps) =="
python3 - "$TMPDIR" "$COUNT" <<'PYEOF'
import sys, os, json

tmpdir, count = sys.argv[1], int(sys.argv[2])
with open(os.path.join(tmpdir, "batch_start.txt")) as f:
    batch_start = float(f.read().strip())

intervals = []
total_eval_tokens = 0
for i in range(1, count + 1):
    path = os.path.join(tmpdir, f"times_{i}.txt")
    resp_path = os.path.join(tmpdir, f"resp_{i}.json")
    if not os.path.isfile(path):
        print(f"request {i}: never finished — excluded from overlap analysis")
        continue
    with open(path) as f:
        start_s, end_s = f.read().split()
    start, end = float(start_s) - batch_start, float(end_s) - batch_start
    intervals.append((i, start, end))
    if os.path.isfile(resp_path):
        try:
            with open(resp_path) as f:
                total_eval_tokens += json.load(f).get("eval_count", 0)
        except (json.JSONDecodeError, OSError):
            pass

if not intervals:
    print("No completed requests to analyze.")
    sys.exit(0)

# Sweep line: +1 at a start, -1 at an end. Ties sort starts before ends,
# so two requests that start/end at the literal same instant still read
# as overlapping — the more generous (not the more conservative) reading
# of "how many were in flight at once."
events = []
for i, start, end in intervals:
    events.append((start, 0, i))   # 0 sorts before 1 at equal timestamps
    events.append((end, 1, i))
events.sort()

running, max_overlap, max_at = 0, 0, 0.0
for t, kind, i in events:
    if kind == 0:
        running += 1
        if running > max_overlap:
            max_overlap, max_at = running, t
    else:
        running -= 1

print(f"Max requests genuinely in flight at the same instant: {max_overlap} "
      f"(at t={max_at:.2f}s into the batch)")

batch_span = max(end for _, _, end in intervals) - min(start for _, start, _ in intervals)
if batch_span > 0 and total_eval_tokens > 0:
    print(f"Aggregate throughput across all {len(intervals)} completed requests: "
          f"{total_eval_tokens} real generated tokens (from Ollama's own eval_count, "
          f"not an assumed num_predict) over {batch_span:.2f}s wall-clock "
          f"= {total_eval_tokens / batch_span:.1f} tok/s combined.")
    print("Compare this number across different --count values directly — if it stays")
    print("roughly flat as count rises, the GPU's decode throughput is already")
    print("saturated at the lower count, and more concurrent slots mainly divide that")
    print("same total throughput into more, slower individual requests rather than")
    print("finishing more real work per second.")
print()
print("Per-request timeline (relative to batch launch, in seconds):")
for i, start, end in sorted(intervals, key=lambda x: x[1]):
    bar_start = int(start * 2)
    bar_len = max(1, int((end - start) * 2))
    print(f"  request {i:>2}: [{start:6.2f}s -> {end:6.2f}s]  "
          + (" " * bar_start) + ("#" * bar_len))
PYEOF

echo
echo "Read the LAST 'ollama ps' block above for real resident GPU memory"
echo "under this load, and the 'max requests genuinely in flight' line"
echo "above for whether $COUNT requests actually ran concurrently or your"
echo "current OLLAMA_NUM_PARALLEL setting queued some of them instead."
