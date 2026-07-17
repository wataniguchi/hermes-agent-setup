#!/usr/bin/env bash
# Checks Ollama's version and pulls the two models config.yaml expects.
set -euo pipefail

echo "== Checking Ollama =="
if ! command -v ollama >/dev/null 2>&1; then
  echo "Ollama not found. Install with: brew install ollama"
  exit 1
fi

OLLAMA_VERSION=$(ollama --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
echo "Installed: $OLLAMA_VERSION"
echo "NOTE: 0.22.0 or later required — earlier builds predate the llama.cpp"
echo "Gemma 4 fixes, especially around tool-calling reliability."
echo "If older: brew upgrade ollama"

echo
echo "== Pulling primary reasoning model =="
ollama pull gemma4:26b

echo
echo "== Pulling fast sub-agent model =="
ollama pull gemma4:e4b

echo
echo "== Done. Verify with: =="
echo "ollama list"
echo "ollama ps   # after first use, confirms 262144 context and GPU residency"
