#!/usr/bin/env bash
# Checks Ollama's version, pulls the primary and fast models, and creates
# the gpt-oss:120b-64k tag config.yaml expects.
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
echo "== Pulling primary reasoning model (gpt-oss:120b, ~64GB) =="
ollama pull gpt-oss:120b

echo
echo "== Creating gpt-oss:120b-64k (65536-context tag) =="
echo "This is a deliberate memory trade-off, not the model's native 131072"
echo "context — see README.md's overview for why. Skipping this and using"
echo "plain gpt-oss:120b as primary has NOT been validated to coexist with"
echo "gemma4:e4b under concurrent load; re-run the full validation in"
echo "README.md §5 if you do."
if ollama list | awk '{print $1}' | grep -qx 'gpt-oss:120b-64k'; then
  echo "gpt-oss:120b-64k already exists — skipping."
else
  cat > /tmp/gptoss-64k.modelfile << 'MODELFILE_EOF'
FROM gpt-oss:120b
PARAMETER num_ctx 65536
MODELFILE_EOF
  ollama create gpt-oss:120b-64k -f /tmp/gptoss-64k.modelfile
  rm -f /tmp/gptoss-64k.modelfile
fi

echo
echo "== Pulling fast sub-agent model (gemma4:e4b) =="
ollama pull gemma4:e4b

echo
echo "== Done. Verify with: =="
echo "ollama list"
echo "ollama ps   # after first use — confirm gpt-oss:120b-64k shows CONTEXT=65536"
echo
echo "Before trusting concurrent delegation, run the full residency/concurrency"
echo "validation in README.md §5 — memory totals alone can be misleading; check"
echo "'sysctl vm.swapusage' under real concurrent load, not just 'ollama ps'."
