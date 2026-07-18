#!/usr/bin/env bash
# Runs the automatable parts of the verification checklist.
# Manual steps (real Discord reply, Open WebUI still working) are printed
# as reminders at the end since they can't be checked from a script.
set -uo pipefail

# Homebrew's bin directories aren't reliably on PATH for a non-interactive
# script shell (brew shellenv is typically sourced only for interactive
# shells via ~/.zshrc) — add them explicitly so `ollama` resolves here the
# same way it does in your terminal.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

pass=0
fail=0

check() {
  local desc="$1"
  local cmd="$2"
  printf "%-55s" "$desc"
  if eval "$cmd" >/dev/null 2>&1; then
    echo "OK"
    pass=$((pass+1))
  else
    echo "FAIL"
    fail=$((fail+1))
  fi
}

echo "== Hermes Agent setup verification (Ollama backend) =="
echo

check "Ollama reachable"                            "curl -sf http://localhost:11434/api/tags"
check "gpt-oss:120b-64k created"                     "ollama list | awk '{print \$1}' | grep -qx 'gpt-oss:120b-64k'"
check "gemma4:e4b pulled"                            "ollama list | awk '{print \$1}' | grep -qx 'gemma4:e4b'"
check "Hermes CLI installed"                         "command -v hermes"
check "Hermes config file present"                   "test -f ~/.hermes/config.yaml"
check "Hermes env file present"                      "test -f ~/.hermes/.env"
check "Hermes CLI responds to a local model"         "hermes chat -q 'reply with ok'"
check "Hermes gateway status reachable"              "hermes gateway status"
check "Gateway LaunchAgent installed (Hermes-managed)" "test -f ~/Library/LaunchAgents/ai.hermes.gateway.plist"

echo
echo "== Sandbox image check =="
check "hermes-sandbox:latest image built"            "docker image inspect hermes-sandbox:latest"
if docker image inspect hermes-sandbox:latest >/dev/null 2>&1; then
  if docker run --rm hermes-sandbox:latest python3 -c "import pypdf, pdfplumber" >/dev/null 2>&1; then
    echo "pypdf/pdfplumber importable in image           OK"
    pass=$((pass+1))
  else
    echo "pypdf/pdfplumber importable in image           FAIL — rebuild with ./scripts/build-sandbox-image.sh"
    fail=$((fail+1))
  fi
fi

echo "== Tool-calling sanity check (primary model) =="
TOOL_TEST=$(curl -s http://localhost:11434/api/chat \
  -d '{"model":"gpt-oss:120b-64k","messages":[{"role":"user","content":"Use get_weather to check Yokohama."}],"tools":[{"type":"function","function":{"name":"get_weather","description":"Get weather","parameters":{"type":"object","properties":{"location":{"type":"string"}},"required":["location"]}}}],"stream":false}' 2>/dev/null)
if echo "$TOOL_TEST" | grep -q '"tool_calls"'; then
  echo "Tool-calling: OK (valid tool_calls returned)"
  pass=$((pass+1))
else
  echo "Tool-calling: FAIL — confirm Ollama is 0.22.0+ (ollama --version)"
  fail=$((fail+1))
fi

echo
echo "== Currently resident models =="
ollama ps || true

echo
echo "== Results: $pass passed, $fail failed =="
echo
echo "Manual checks still needed:"
echo "  - Send a real message in a private Discord test channel and confirm the bot replies"
echo "  - Open http://localhost:3000 and confirm Open WebUI still works normally against its Ollama backend"
echo "  - Run a real delegate_task through hermes chat and confirm it completes cleanly (§6)"

if [ "$fail" -gt 0 ]; then
  exit 1
fi
