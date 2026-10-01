#!/usr/bin/env bash
# Checks Ollama's version, pulls the primary and fast models, and creates
# the qwen3.8:27b-96k tag config.yaml expects.
set -euo pipefail

echo "== Checking Ollama =="
if ! command -v ollama >/dev/null 2>&1; then
  echo "Ollama not found. Install with: brew install ollama"
  exit 1
fi

OLLAMA_VERSION=$(ollama --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
echo "Installed: $OLLAMA_VERSION"
echo "NOTE: 0.22.0 or later required — earlier builds predate the llama.cpp"
echo "Gemma 4 fixes, especially around tool-calling reliability (gemma4:e4b"
echo "still depends on this)."
echo "If older: brew upgrade ollama"

echo
echo "== Pulling primary reasoning model (qwen3.8:27b, ~21GB resident) =="
ollama pull qwen3.8:27b

echo
echo "== Creating qwen3.8:27b-96k (98304-context tag) =="
echo "A mid-ground context choice, not a rigorously validated one — see"
echo "README.md's overview for the reasoning (more headroom than 64k allowed,"
echo "128k's extra memory cost didn't seem worth it given Hermes's own context"
echo "compression kicks in occasionally anyway). Skipping this tag and using"
echo "plain qwen3.8:27b as primary will pick up whatever Ollama's default"
echo "context is for that model instead — re-check README.md §5 if you do."
if ollama list | awk '{print $1}' | grep -qx 'qwen3.8:27b-96k'; then
  echo "qwen3.8:27b-96k already exists — skipping."
else
  cat > /tmp/qwen-96k.modelfile << 'MODELFILE_EOF'
FROM qwen3.8:27b
PARAMETER num_ctx 98304
MODELFILE_EOF
  ollama create qwen3.8:27b-96k -f /tmp/qwen-96k.modelfile
  rm -f /tmp/qwen-96k.modelfile
fi

echo
echo "== Pulling fast sub-agent model (gemma4:e4b) =="
ollama pull gemma4:e4b

echo
echo "== Done. Verify with: =="
echo "ollama list"
echo "ollama ps   # after first use — confirm qwen3.8:27b-96k shows CONTEXT=98304"
echo
echo "Before trusting concurrent delegation, run the full residency/concurrency"
echo "validation in README.md §5 — memory totals alone can be misleading; check"
echo "'sysctl vm.swapusage' under real concurrent load, not just 'ollama ps'."
echo "Note: real 3-way concurrent delegation has barely been exercised in"
echo "practice so far (README.md §5) — residency/idle-swap numbers are"
echo "confirmed, genuine concurrent throughput is still mostly untested."
