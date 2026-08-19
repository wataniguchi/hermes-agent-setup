#!/usr/bin/env bash
# Starts the Proxmox bridge service, which lets Hermes's Docker sandbox
# control isolated analysis VMs on a Proxmox host via a narrow HTTP API.
# See README_Proxmox.md for the full architecture and one-time setup.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROXMOX_DIR="$REPO_ROOT/proxmox"
VENV_DIR="$PROXMOX_DIR/.venv"
ENV_FILE="$PROXMOX_DIR/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE — copy proxmox/.env.example to proxmox/.env and fill in real values first." >&2
  exit 1
fi

if [[ ! -d "$VENV_DIR" ]]; then
  echo "Creating venv at $VENV_DIR ..."
  python3 -m venv "$VENV_DIR"
  "$VENV_DIR/bin/pip" install -r "$PROXMOX_DIR/requirements.txt"
fi

# shellcheck disable=SC1090
set -a
source "$ENV_FILE"
set +a

echo "Starting Proxmox bridge on 127.0.0.1:8811 (reachable from Hermes's Docker sandbox via host.docker.internal:8811) ..."
exec "$VENV_DIR/bin/uvicorn" proxmox_bridge:app \
  --app-dir "$PROXMOX_DIR" \
  --host 127.0.0.1 \
  --port 8811
