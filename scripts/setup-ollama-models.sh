#!/usr/bin/env bash
# Checks Ollama's version, pulls the primary and delegation models, and
# creates the qwen3.8:27b-96k / qwen3.8:27b-delegate tags config.yaml
# expects.
set -euo pipefail

echo "== Checking Ollama =="
if ! command -v ollama >/dev/null 2>&1; then
  echo "Ollama not found. Install with: brew install ollama"
  exit 1
fi

OLLAMA_VERSION=$(ollama --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
echo "Installed: $OLLAMA_VERSION"
echo "NOTE: 0.22.0 or later required. See CHANGELOG.md if you need to know"
echo "why this floor predates the current delegation model."
echo "If older: brew upgrade ollama"

echo
echo "== Pulling primary reasoning model (qwen3.8:27b, ~21GB resident) =="
ollama pull qwen3.8:27b

echo
echo "== Creating qwen3.8:27b-96k (98304-context tag) =="
echo "Skipping this tag and using plain qwen3.8:27b as primary will pick up"
echo "whatever Ollama's default context is for that model instead — see"
echo "README.md §5."
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
echo "== Pulling delegation model (qwen3.8:27b-delegate base) =="
echo "A smaller quant of the primary model — real reasoning on delegated"
echo "PoC/remediate work, while fitting comfortably alongside the primary."
ollama pull hf.co/Chungulus/Qwen3.8-27B-Q3_K_M-GGUF:latest

echo
echo "== Creating qwen3.8:27b-delegate (65536-context tag) =="
echo "65536 is Hermes's own hard minimum for any delegate_task model, not"
echo "a tuning choice — see CHANGELOG.md. Resident at this context: 16GB,"
echo "flat through at least 10-way concurrent use, no swap growth under"
echo "load."
if ollama list | awk '{print $1}' | grep -qx 'qwen3.8:27b-delegate'; then
  echo "qwen3.8:27b-delegate already exists — skipping."
else
  cat > /tmp/qwen-delegate.modelfile << 'MODELFILE_EOF'
FROM hf.co/Chungulus/Qwen3.8-27B-Q3_K_M-GGUF:latest
PARAMETER num_ctx 65536
MODELFILE_EOF
  ollama create qwen3.8:27b-delegate -f /tmp/qwen-delegate.modelfile
  rm -f /tmp/qwen-delegate.modelfile
fi

echo
echo "== Done. Verify with: =="
echo "ollama list"
echo "ollama ps   # after first use — confirm qwen3.8:27b-96k shows CONTEXT=98304"
echo "            # and qwen3.8:27b-delegate shows CONTEXT=65536"
echo
echo "config.yaml's delegation.max_concurrent_children: 10 is a conservative"
echo "setting, not a measured ceiling — see CHANGELOG.md. Re-run"
echo "scripts/measure-ollama-concurrency.sh with the same measure-first"
echo "discipline if real sweep concurrency needs grow past this."
