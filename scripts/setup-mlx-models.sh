#!/usr/bin/env bash
# Downloads the MLX models config/config.yaml expects. No tagging step
# needed (unlike the old Ollama setup) — context length is a server
# startup flag, not a per-model tag, and Hermes's model.context_length is
# auto-detected from the provider (per the installer's own default
# config.yaml comments) — no manual tagging or override needed here.
#
# CLI-only — does not assume `mlx_lm` is importable from python3. If
# mlx-lm was installed via `brew install mlx-lm`, the Python module lives
# inside Homebrew's private venv and is not importable from your own
# python3, even though the CLI binaries (mlx_lm.server, mlx_lm.generate,
# etc.) are on PATH and work fine. This script only relies on those CLI
# binaries, so it works regardless of install method (brew, pip, uvx).
set -euo pipefail

echo "== Checking mlx_lm installation =="
if command -v mlx_lm.server >/dev/null 2>&1; then
  echo "mlx_lm.server found on PATH: $(command -v mlx_lm.server)"
else
  echo "mlx_lm.server not found. Install with either:"
  echo "  brew install mlx-lm"
  echo "  pip install -U mlx-lm"
  exit 1
fi

if command -v brew >/dev/null 2>&1 && brew list --versions mlx-lm >/dev/null 2>&1; then
  INSTALLED_VERSION=$(brew list --versions mlx-lm | awk '{print $2}')
  echo "Installed via Homebrew: mlx-lm $INSTALLED_VERSION"
else
  INSTALLED_VERSION=$(python3 -c "import mlx_lm; print(mlx_lm.__version__)" 2>/dev/null || echo "unknown")
  echo "Installed version (via pip/python3 import): $INSTALLED_VERSION"
fi

echo "NOTE: confirm this is 0.31.3 or later — mlx-lm#1125 (broken tool-call"
echo "parsing on gemma-4-26b-a4b-it-4bit) was open on 0.31.2 and earlier."
echo "If older: brew upgrade mlx-lm   (or: pip install -U mlx-lm)"

download_model() {
  local repo="$1"
  echo
  echo "== Downloading $repo (if not already cached) =="
  # mlx_lm.generate triggers the same HF Hub download mlx_lm.server would
  # on first use — this pre-warms the cache without needing a Python
  # import of huggingface_hub. --max-tokens 1 keeps this fast; we only
  # care about the download, not the output.
  mlx_lm.generate --model "$repo" --prompt "hi" --max-tokens 1 > /dev/null
}

download_model "mlx-community/gemma-4-26b-a4b-it-4bit"
download_model "mlx-community/gemma-4-e4b-it-4bit"

echo
echo "== Done. Models cached under ~/.cache/huggingface/hub/ =="
if command -v mlx_lm.manage >/dev/null 2>&1; then
  echo "Verify with: mlx_lm.manage --scan"
fi
echo "Start both servers with: ./scripts/start-mlx-servers.sh"
