#!/usr/bin/env bash
# Builds the custom Hermes sandbox image (docker/hermes-sandbox.Dockerfile)
# and tags it as hermes-sandbox:latest, matching config.yaml's
# terminal.docker_image. Re-run this any time docker/hermes-sandbox.Dockerfile
# changes (e.g. adding a new package the sandbox needs).
set -euo pipefail

cd "$(dirname "$0")/.."

# ---------------------------------------------------------------------------
# Fixed SSH client identity — generate ONCE, then reuse forever
# ---------------------------------------------------------------------------
# The Dockerfile COPYs ssh/{id_ed25519,id_ed25519.pub,config} out of the
# build context (docker/) into both the hermes and root ~/.ssh so every
# container has a stable, pre-known public key. The public key only ever
# has to be registered into the target hosts' authorized_keys once.
#
# CRITICAL: generate-if-missing, never unconditionally. Regenerating the
# key would mint a NEW public key and silently invalidate every
# authorized_keys entry already registered on the target hosts. So we only
# create it when docker/ssh/id_ed25519 does not already exist; every
# subsequent build reuses the exact same committed key. To intentionally
# rotate the identity, delete docker/ssh/ and re-run this script, then
# re-register the new public key everywhere.
#
# The private key lives in docker/ssh/id_ed25519 and is sensitive: anyone
# who can read it can authenticate as this sandbox to those hosts. Keep it
# out of any widely-shared location; if the repo is shared, decide
# deliberately whether to commit it (needed for the key to survive fresh
# clones) or to distribute it out-of-band.
SSH_DIR="docker/ssh"
SSH_KEY="${SSH_DIR}/id_ed25519"

if [[ ! -f "${SSH_KEY}" ]]; then
  echo "== No sandbox SSH key found — generating one (happens only once) =="
  mkdir -p "${SSH_DIR}"
  ssh-keygen -t ed25519 -N '' -C 'hermes-sandbox' -f "${SSH_KEY}"
  chmod 0700 "${SSH_DIR}"
  chmod 0600 "${SSH_KEY}"
  chmod 0644 "${SSH_KEY}.pub"
  echo "   Generated ${SSH_KEY} (private) and ${SSH_KEY}.pub (public)."
else
  echo "== Reusing existing sandbox SSH key at ${SSH_KEY} (not regenerating) =="
fi

# The Dockerfile also COPYs an ssh/config that pins this identity and
# accepts unknown host keys on first contact (reasonable only because the
# target hosts are ones you control). Create it if missing; leave any
# existing customized one untouched.
SSH_CONFIG="${SSH_DIR}/config"
if [[ ! -f "${SSH_CONFIG}" ]]; then
  echo "== Writing default ${SSH_CONFIG} =="
  cat > "${SSH_CONFIG}" <<'EOF'
Host *
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
    StrictHostKeyChecking accept-new
EOF
  chmod 0600 "${SSH_CONFIG}"
fi

echo
echo "== Building hermes-sandbox:latest =="
docker build -t hermes-sandbox:latest -f docker/hermes-sandbox.Dockerfile docker/

echo
echo "== Verifying pypdf/pdfplumber are importable in the new image =="
docker run --rm hermes-sandbox:latest python3 -c "import pypdf, pdfplumber; print('ok')"

echo
echo "== Verifying the SSH client identity is in place for both hermes and root =="
# Runs as the image's default USER (hermes). Checks the hermes key's perms
# and that both baked public keys are identical to the one on disk here
# (so a single registration covers both users).
docker run --rm hermes-sandbox:latest bash -c '
  set -e
  ssh -V
  test -f ~/.ssh/id_ed25519
  test "$(stat -c %a ~/.ssh/id_ed25519)" = "600"
  echo "hermes identity ok (user: $(whoami), key mode: $(stat -c %a ~/.ssh/id_ed25519))"
  sudo test -f /root/.ssh/id_ed25519
  echo "root identity present: /root/.ssh/id_ed25519"
  diff <(cat ~/.ssh/id_ed25519.pub) <(sudo cat /root/.ssh/id_ed25519.pub) \
    && echo "hermes and root share the same public key (one registration covers both)"
'

echo
echo "== Sandbox public key — register THIS in each target host'\''s ~/.ssh/authorized_keys =="
echo "   (only needs doing once; it stays valid across image rebuilds)"
echo
cat "${SSH_KEY}.pub"
echo
echo "   Quick registration per host:"
echo "     ssh-copy-id -i ${SSH_KEY}.pub user@target-host"
echo "   or append ${SSH_KEY}.pub to that host'\''s ~/.ssh/authorized_keys by hand."

echo
echo "== Done. config.yaml's terminal.docker_image should already point at hermes-sandbox:latest =="
echo "If any Hermes sandbox containers are currently running from the OLD image, remove them so"
echo "the next request creates a fresh one from this new image:"
echo
echo "  docker ps -a | grep hermes-"
echo "  docker rm -f <container_id> [<container_id> ...]"
echo "  hermes gateway restart"
