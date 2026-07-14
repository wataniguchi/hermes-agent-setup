#!/usr/bin/env bash
# Runs the automatable parts of the verification checklist.
# Manual steps (real Discord reply, Open WebUI still working) are printed
# as reminders at the end since they can't be checked from a script.
set -uo pipefail

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

echo "== Hermes Agent setup verification (MLX backend) =="
echo

check "Primary mlx_lm.server reachable (:8081)"    "curl -sf http://127.0.0.1:8081/v1/models"
check "Fast mlx_lm.server reachable (:8082)"        "curl -sf http://127.0.0.1:8082/v1/models"
check "Hermes CLI installed"                        "command -v hermes"
check "Hermes config file present"                  "test -f ~/.hermes/config.yaml"
check "Hermes env file present"                     "test -f ~/.hermes/.env"
check "Hermes CLI responds to a local model"        "hermes chat -q 'reply with ok'"
check "Hermes gateway status reachable"             "hermes gateway status"
check "Primary LaunchAgent plist installed"         "test -f ~/Library/LaunchAgents/com.mlx-primary.plist"
check "Fast LaunchAgent plist installed"            "test -f ~/Library/LaunchAgents/com.mlx-fast.plist"
check "Gateway LaunchAgent installed (Hermes-managed)" "test -f ~/Library/LaunchAgents/ai.hermes.gateway.plist"

echo
echo "== Tool-calling sanity check (primary model) =="
TOOL_TEST=$(curl -s http://127.0.0.1:8081/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"mlx-community/gemma-4-26b-a4b-it-4bit","messages":[{"role":"user","content":"Use get_weather to check Yokohama."}],"tools":[{"type":"function","function":{"name":"get_weather","description":"Get weather","parameters":{"type":"object","properties":{"location":{"type":"string"}},"required":["location"]}}}],"tool_choice":"auto"}' 2>/dev/null)
if echo "$TOOL_TEST" | grep -q '"tool_calls"'; then
  echo "Tool-calling: OK (valid tool_calls returned)"
  pass=$((pass+1))
else
  echo "Tool-calling: FAIL — check mlx_lm version (need 0.31.3+, see mlx-lm#1125)"
  fail=$((fail+1))
fi

echo
echo "== Results: $pass passed, $fail failed =="
echo
echo "Manual checks still needed:"
echo "  - Send a real message in a private Discord test channel and confirm the bot replies"
echo "  - Open http://localhost:3000 and confirm Open WebUI still works normally against its MLX backend"
echo "  - Run: launchctl kickstart -k gui/\$(id -u)/com.mlx-primary"
echo "    and: launchctl kickstart -k gui/\$(id -u)/com.mlx-fast"
echo "    then confirm both come back up (check ~/Library/Logs/mlx-primary.log, mlx-fast.log)"

if [ "$fail" -gt 0 ]; then
  exit 1
fi
