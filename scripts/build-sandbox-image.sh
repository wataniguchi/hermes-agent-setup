#!/usr/bin/env bash
# Builds the custom Hermes sandbox image (docker/hermes-sandbox.Dockerfile)
# and tags it as hermes-sandbox:latest, matching config.yaml's
# terminal.docker_image. Re-run this any time docker/hermes-sandbox.Dockerfile
# changes (e.g. adding a new package the sandbox needs).
set -euo pipefail

cd "$(dirname "$0")/.."

echo "== Building hermes-sandbox:latest =="
docker build -t hermes-sandbox:latest -f docker/hermes-sandbox.Dockerfile docker/

echo
echo "== Verifying pypdf/pdfplumber are importable in the new image =="
docker run --rm hermes-sandbox:latest python3 -c "import pypdf, pdfplumber; print('ok')"

echo
echo "== Done. config.yaml's terminal.docker_image should already point at hermes-sandbox:latest =="
echo "If any Hermes sandbox containers are currently running from the OLD image, remove them so"
echo "the next request creates a fresh one from this new image:"
echo
echo "  docker ps -a | grep hermes-"
echo "  docker rm -f <container_id> [<container_id> ...]"
echo "  hermes gateway restart"
