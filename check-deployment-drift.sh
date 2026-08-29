#!/usr/bin/env bash
# Compares the repo (source of truth) against everywhere its files get
# deployed to at runtime. Run from anywhere — paths are absolute.
#
# Catches exactly the kind of gap found tonight: a fix committed to the
# repo that never actually reached the directory Hermes reads from.

set -uo pipefail

REPO="$HOME/hermes-agent-setup"
DEFAULT_SKILL_DIR="$HOME/.hermes/skills/security/windows-binary-analysis"
QWEN_SKILL_DIR="$HOME/.hermes/profiles/qwen-experiment/skills/security/windows-binary-analysis"
DEFAULT_BINARY_SKILL_DIR="$HOME/.hermes/skills/security/binary-static-analysis"
QWEN_BINARY_SKILL_DIR="$HOME/.hermes/profiles/qwen-experiment/skills/security/binary-static-analysis"
DEFAULT_KSNCTF_FETCH_DIR="$HOME/.hermes/skills/security/ksnctf-fetch"
QWEN_KSNCTF_FETCH_DIR="$HOME/.hermes/profiles/qwen-experiment/skills/security/ksnctf-fetch"
DEFAULT_KSNCTF_SUBMIT_DIR="$HOME/.hermes/skills/security/ksnctf-submit"
QWEN_KSNCTF_SUBMIT_DIR="$HOME/.hermes/profiles/qwen-experiment/skills/security/ksnctf-submit"
DEFAULT_KSNCTF_DISCOVER_DIR="$HOME/.hermes/skills/security/ksnctf-discover"
QWEN_KSNCTF_DISCOVER_DIR="$HOME/.hermes/profiles/qwen-experiment/skills/security/ksnctf-discover"
DEFAULT_CTF_SOLVER_DIR="$HOME/.hermes/skills/security/ctf-solver"
QWEN_CTF_SOLVER_DIR="$HOME/.hermes/profiles/qwen-experiment/skills/security/ctf-solver"
GEMMA_SKILL_DIR="$HOME/.hermes/profiles/gemma-experiment/skills/security/windows-binary-analysis"
GEMMA_BINARY_SKILL_DIR="$HOME/.hermes/profiles/gemma-experiment/skills/security/binary-static-analysis"
GEMMA_KSNCTF_FETCH_DIR="$HOME/.hermes/profiles/gemma-experiment/skills/security/ksnctf-fetch"
GEMMA_KSNCTF_SUBMIT_DIR="$HOME/.hermes/profiles/gemma-experiment/skills/security/ksnctf-submit"
GEMMA_KSNCTF_DISCOVER_DIR="$HOME/.hermes/profiles/gemma-experiment/skills/security/ksnctf-discover"
GEMMA_CTF_SOLVER_DIR="$HOME/.hermes/profiles/gemma-experiment/skills/security/ctf-solver"
DEVSTRAL_SKILL_DIR="$HOME/.hermes/profiles/devstral-experiment/skills/security/windows-binary-analysis"
DEVSTRAL_BINARY_SKILL_DIR="$HOME/.hermes/profiles/devstral-experiment/skills/security/binary-static-analysis"
DEVSTRAL_KSNCTF_FETCH_DIR="$HOME/.hermes/profiles/devstral-experiment/skills/security/ksnctf-fetch"
DEVSTRAL_KSNCTF_SUBMIT_DIR="$HOME/.hermes/profiles/devstral-experiment/skills/security/ksnctf-submit"
DEVSTRAL_KSNCTF_DISCOVER_DIR="$HOME/.hermes/profiles/devstral-experiment/skills/security/ksnctf-discover"
DEVSTRAL_CTF_SOLVER_DIR="$HOME/.hermes/profiles/devstral-experiment/skills/security/ctf-solver"

check() {
  local repo_file="$1"
  local deployed_file="$2"
  local label="$3"

  if [[ ! -f "$repo_file" ]]; then
    echo "MISSING FROM REPO:      $label"
    return
  fi
  if [[ ! -f "$deployed_file" ]]; then
    echo "NOT YET DEPLOYED:       $label"
    return
  fi
  if diff -q "$repo_file" "$deployed_file" > /dev/null 2>&1; then
    echo "OK (identical):         $label"
  else
    echo "DRIFT — differs:        $label"
    echo "    repo:     $repo_file"
    echo "    deployed: $deployed_file"
  fi
}

echo "=== Skill files: repo -> default profile ==="
check "$REPO/skills/security/windows-binary-analysis/SKILL.md" \
      "$DEFAULT_SKILL_DIR/SKILL.md" \
      "SKILL.md (default)"
check "$REPO/skills/security/windows-binary-analysis/scripts/analyze_windows_binary.py" \
      "$DEFAULT_SKILL_DIR/scripts/analyze_windows_binary.py" \
      "analyze_windows_binary.py (default)"
check "$REPO/skills/security/windows-binary-analysis/scripts/gui_probe.ps1" \
      "$DEFAULT_SKILL_DIR/scripts/gui_probe.ps1" \
      "gui_probe.ps1 (default)"
check "$REPO/skills/security/windows-binary-analysis/scripts/pyghidra_tool.py" \
      "$DEFAULT_SKILL_DIR/scripts/pyghidra_tool.py" \
      "pyghidra_tool.py (default)"

echo ""
echo "=== Skill files: repo -> qwen-experiment profile ==="
check "$REPO/skills/security/windows-binary-analysis/SKILL.md" \
      "$QWEN_SKILL_DIR/SKILL.md" \
      "SKILL.md (qwen-experiment)"
check "$REPO/skills/security/windows-binary-analysis/scripts/analyze_windows_binary.py" \
      "$QWEN_SKILL_DIR/scripts/analyze_windows_binary.py" \
      "analyze_windows_binary.py (qwen-experiment)"
check "$REPO/skills/security/windows-binary-analysis/scripts/gui_probe.ps1" \
      "$QWEN_SKILL_DIR/scripts/gui_probe.ps1" \
      "gui_probe.ps1 (qwen-experiment)"
check "$REPO/skills/security/windows-binary-analysis/scripts/pyghidra_tool.py" \
      "$QWEN_SKILL_DIR/scripts/pyghidra_tool.py" \
      "pyghidra_tool.py (qwen-experiment)"

echo ""
echo "=== Skill files: repo -> gemma-experiment profile ==="
check "$REPO/skills/security/windows-binary-analysis/SKILL.md" \
      "$GEMMA_SKILL_DIR/SKILL.md" \
      "SKILL.md (gemma-experiment)"
check "$REPO/skills/security/windows-binary-analysis/scripts/analyze_windows_binary.py" \
      "$GEMMA_SKILL_DIR/scripts/analyze_windows_binary.py" \
      "analyze_windows_binary.py (gemma-experiment)"
check "$REPO/skills/security/windows-binary-analysis/scripts/gui_probe.ps1" \
      "$GEMMA_SKILL_DIR/scripts/gui_probe.ps1" \
      "gui_probe.ps1 (gemma-experiment)"
check "$REPO/skills/security/windows-binary-analysis/scripts/pyghidra_tool.py" \
      "$GEMMA_SKILL_DIR/scripts/pyghidra_tool.py" \
      "pyghidra_tool.py (gemma-experiment)"

echo ""
echo "=== Skill files: repo -> devstral-experiment profile ==="
check "$REPO/skills/security/windows-binary-analysis/SKILL.md" \
      "$DEVSTRAL_SKILL_DIR/SKILL.md" \
      "SKILL.md (devstral-experiment)"
check "$REPO/skills/security/windows-binary-analysis/scripts/analyze_windows_binary.py" \
      "$DEVSTRAL_SKILL_DIR/scripts/analyze_windows_binary.py" \
      "analyze_windows_binary.py (devstral-experiment)"
check "$REPO/skills/security/windows-binary-analysis/scripts/gui_probe.ps1" \
      "$DEVSTRAL_SKILL_DIR/scripts/gui_probe.ps1" \
      "gui_probe.ps1 (devstral-experiment)"
check "$REPO/skills/security/windows-binary-analysis/scripts/pyghidra_tool.py" \
      "$DEVSTRAL_SKILL_DIR/scripts/pyghidra_tool.py" \
      "pyghidra_tool.py (devstral-experiment)"

echo ""
echo "=== binary-static-analysis skill: repo -> default profile ==="
check "$REPO/skills/security/binary-static-analysis/SKILL.md" \
      "$DEFAULT_BINARY_SKILL_DIR/SKILL.md" \
      "SKILL.md (default)"
check "$REPO/skills/security/binary-static-analysis/scripts/binary_analysis.py" \
      "$DEFAULT_BINARY_SKILL_DIR/scripts/binary_analysis.py" \
      "binary_analysis.py (default)"

echo ""
echo "=== binary-static-analysis skill: repo -> qwen-experiment profile ==="
check "$REPO/skills/security/binary-static-analysis/SKILL.md" \
      "$QWEN_BINARY_SKILL_DIR/SKILL.md" \
      "SKILL.md (qwen-experiment)"
check "$REPO/skills/security/binary-static-analysis/scripts/binary_analysis.py" \
      "$QWEN_BINARY_SKILL_DIR/scripts/binary_analysis.py" \
      "binary_analysis.py (qwen-experiment)"

echo ""
echo "=== binary-static-analysis skill: repo -> gemma-experiment profile ==="
check "$REPO/skills/security/binary-static-analysis/SKILL.md" \
      "$GEMMA_BINARY_SKILL_DIR/SKILL.md" \
      "SKILL.md (gemma-experiment)"
check "$REPO/skills/security/binary-static-analysis/scripts/binary_analysis.py" \
      "$GEMMA_BINARY_SKILL_DIR/scripts/binary_analysis.py" \
      "binary_analysis.py (gemma-experiment)"

echo ""
echo "=== binary-static-analysis skill: repo -> devstral-experiment profile ==="
check "$REPO/skills/security/binary-static-analysis/SKILL.md" \
      "$DEVSTRAL_BINARY_SKILL_DIR/SKILL.md" \
      "SKILL.md (devstral-experiment)"
check "$REPO/skills/security/binary-static-analysis/scripts/binary_analysis.py" \
      "$DEVSTRAL_BINARY_SKILL_DIR/scripts/binary_analysis.py" \
      "binary_analysis.py (devstral-experiment)"

echo ""
echo "=== ksnctf-fetch skill: repo -> default profile ==="
check "$REPO/skills/security/ksnctf-fetch/SKILL.md" \
      "$DEFAULT_KSNCTF_FETCH_DIR/SKILL.md" \
      "SKILL.md (default)"
check "$REPO/skills/security/ksnctf-fetch/scripts/ksnctf_fetch.py" \
      "$DEFAULT_KSNCTF_FETCH_DIR/scripts/ksnctf_fetch.py" \
      "ksnctf_fetch.py (default)"

echo ""
echo "=== ksnctf-fetch skill: repo -> qwen-experiment profile ==="
check "$REPO/skills/security/ksnctf-fetch/SKILL.md" \
      "$QWEN_KSNCTF_FETCH_DIR/SKILL.md" \
      "SKILL.md (qwen-experiment)"
check "$REPO/skills/security/ksnctf-fetch/scripts/ksnctf_fetch.py" \
      "$QWEN_KSNCTF_FETCH_DIR/scripts/ksnctf_fetch.py" \
      "ksnctf_fetch.py (qwen-experiment)"

echo ""
echo "=== ksnctf-fetch skill: repo -> gemma-experiment profile ==="
check "$REPO/skills/security/ksnctf-fetch/SKILL.md" \
      "$GEMMA_KSNCTF_FETCH_DIR/SKILL.md" \
      "SKILL.md (gemma-experiment)"
check "$REPO/skills/security/ksnctf-fetch/scripts/ksnctf_fetch.py" \
      "$GEMMA_KSNCTF_FETCH_DIR/scripts/ksnctf_fetch.py" \
      "ksnctf_fetch.py (gemma-experiment)"

echo ""
echo "=== ksnctf-fetch skill: repo -> devstral-experiment profile ==="
check "$REPO/skills/security/ksnctf-fetch/SKILL.md" \
      "$DEVSTRAL_KSNCTF_FETCH_DIR/SKILL.md" \
      "SKILL.md (devstral-experiment)"
check "$REPO/skills/security/ksnctf-fetch/scripts/ksnctf_fetch.py" \
      "$DEVSTRAL_KSNCTF_FETCH_DIR/scripts/ksnctf_fetch.py" \
      "ksnctf_fetch.py (devstral-experiment)"

echo ""
echo "=== ksnctf-submit skill: repo -> default profile ==="
check "$REPO/skills/security/ksnctf-submit/SKILL.md" \
      "$DEFAULT_KSNCTF_SUBMIT_DIR/SKILL.md" \
      "SKILL.md (default)"
check "$REPO/skills/security/ksnctf-submit/scripts/submit_flag.py" \
      "$DEFAULT_KSNCTF_SUBMIT_DIR/scripts/submit_flag.py" \
      "submit_flag.py (default)"

echo ""
echo "=== ksnctf-submit skill: repo -> qwen-experiment profile ==="
check "$REPO/skills/security/ksnctf-submit/SKILL.md" \
      "$QWEN_KSNCTF_SUBMIT_DIR/SKILL.md" \
      "SKILL.md (qwen-experiment)"
check "$REPO/skills/security/ksnctf-submit/scripts/submit_flag.py" \
      "$QWEN_KSNCTF_SUBMIT_DIR/scripts/submit_flag.py" \
      "submit_flag.py (qwen-experiment)"

echo ""
echo "=== ksnctf-submit skill: repo -> gemma-experiment profile ==="
check "$REPO/skills/security/ksnctf-submit/SKILL.md" \
      "$GEMMA_KSNCTF_SUBMIT_DIR/SKILL.md" \
      "SKILL.md (gemma-experiment)"
check "$REPO/skills/security/ksnctf-submit/scripts/submit_flag.py" \
      "$GEMMA_KSNCTF_SUBMIT_DIR/scripts/submit_flag.py" \
      "submit_flag.py (gemma-experiment)"

echo ""
echo "=== ksnctf-submit skill: repo -> devstral-experiment profile ==="
check "$REPO/skills/security/ksnctf-submit/SKILL.md" \
      "$DEVSTRAL_KSNCTF_SUBMIT_DIR/SKILL.md" \
      "SKILL.md (devstral-experiment)"
check "$REPO/skills/security/ksnctf-submit/scripts/submit_flag.py" \
      "$DEVSTRAL_KSNCTF_SUBMIT_DIR/scripts/submit_flag.py" \
      "submit_flag.py (devstral-experiment)"

echo ""
echo "=== ksnctf-discover skill: repo -> default profile ==="
check "$REPO/skills/security/ksnctf-discover/SKILL.md" \
      "$DEFAULT_KSNCTF_DISCOVER_DIR/SKILL.md" \
      "SKILL.md (default)"
check "$REPO/skills/security/ksnctf-discover/scripts/ksnctf_discover.py" \
      "$DEFAULT_KSNCTF_DISCOVER_DIR/scripts/ksnctf_discover.py" \
      "ksnctf_discover.py (default)"

echo ""
echo "=== ksnctf-discover skill: repo -> qwen-experiment profile ==="
check "$REPO/skills/security/ksnctf-discover/SKILL.md" \
      "$QWEN_KSNCTF_DISCOVER_DIR/SKILL.md" \
      "SKILL.md (qwen-experiment)"
check "$REPO/skills/security/ksnctf-discover/scripts/ksnctf_discover.py" \
      "$QWEN_KSNCTF_DISCOVER_DIR/scripts/ksnctf_discover.py" \
      "ksnctf_discover.py (qwen-experiment)"

echo ""
echo "=== ksnctf-discover skill: repo -> gemma-experiment profile ==="
check "$REPO/skills/security/ksnctf-discover/SKILL.md" \
      "$GEMMA_KSNCTF_DISCOVER_DIR/SKILL.md" \
      "SKILL.md (gemma-experiment)"
check "$REPO/skills/security/ksnctf-discover/scripts/ksnctf_discover.py" \
      "$GEMMA_KSNCTF_DISCOVER_DIR/scripts/ksnctf_discover.py" \
      "ksnctf_discover.py (gemma-experiment)"

echo ""
echo "=== ksnctf-discover skill: repo -> devstral-experiment profile ==="
check "$REPO/skills/security/ksnctf-discover/SKILL.md" \
      "$DEVSTRAL_KSNCTF_DISCOVER_DIR/SKILL.md" \
      "SKILL.md (devstral-experiment)"
check "$REPO/skills/security/ksnctf-discover/scripts/ksnctf_discover.py" \
      "$DEVSTRAL_KSNCTF_DISCOVER_DIR/scripts/ksnctf_discover.py" \
      "ksnctf_discover.py (devstral-experiment)"

echo ""
echo "=== ctf-solver skill: repo -> default profile ==="
check "$REPO/skills/security/ctf-solver/SKILL.md" \
      "$DEFAULT_CTF_SOLVER_DIR/SKILL.md" \
      "SKILL.md (default)"
check "$REPO/skills/security/ctf-solver/scripts/ctf_solver.py" \
      "$DEFAULT_CTF_SOLVER_DIR/scripts/ctf_solver.py" \
      "ctf_solver.py (default)"
check "$REPO/skills/security/ctf-solver/scripts/ctf_traversal.py" \
      "$DEFAULT_CTF_SOLVER_DIR/scripts/ctf_traversal.py" \
      "ctf_traversal.py (default)"

echo ""
echo "=== ctf-solver skill: repo -> qwen-experiment profile ==="
check "$REPO/skills/security/ctf-solver/SKILL.md" \
      "$QWEN_CTF_SOLVER_DIR/SKILL.md" \
      "SKILL.md (qwen-experiment)"
check "$REPO/skills/security/ctf-solver/scripts/ctf_solver.py" \
      "$QWEN_CTF_SOLVER_DIR/scripts/ctf_solver.py" \
      "ctf_solver.py (qwen-experiment)"
check "$REPO/skills/security/ctf-solver/scripts/ctf_traversal.py" \
      "$QWEN_CTF_SOLVER_DIR/scripts/ctf_traversal.py" \
      "ctf_traversal.py (qwen-experiment)"

echo ""
echo "=== ctf-solver skill: repo -> gemma-experiment profile ==="
check "$REPO/skills/security/ctf-solver/SKILL.md" \
      "$GEMMA_CTF_SOLVER_DIR/SKILL.md" \
      "SKILL.md (gemma-experiment)"
check "$REPO/skills/security/ctf-solver/scripts/ctf_solver.py" \
      "$GEMMA_CTF_SOLVER_DIR/scripts/ctf_solver.py" \
      "ctf_solver.py (gemma-experiment)"
check "$REPO/skills/security/ctf-solver/scripts/ctf_traversal.py" \
      "$GEMMA_CTF_SOLVER_DIR/scripts/ctf_traversal.py" \
      "ctf_traversal.py (gemma-experiment)"

echo ""
echo "=== ctf-solver skill: repo -> devstral-experiment profile ==="
check "$REPO/skills/security/ctf-solver/SKILL.md" \
      "$DEVSTRAL_CTF_SOLVER_DIR/SKILL.md" \
      "SKILL.md (devstral-experiment)"
check "$REPO/skills/security/ctf-solver/scripts/ctf_solver.py" \
      "$DEVSTRAL_CTF_SOLVER_DIR/scripts/ctf_solver.py" \
      "ctf_solver.py (devstral-experiment)"
check "$REPO/skills/security/ctf-solver/scripts/ctf_traversal.py" \
      "$DEVSTRAL_CTF_SOLVER_DIR/scripts/ctf_traversal.py" \
      "ctf_traversal.py (devstral-experiment)"

echo ""
echo "=== AGENTS.md: repo root (loaded by working directory, not per-profile — no separate deploy step needed as long as both profiles launch from this repo root) ==="
if [[ -f "$REPO/AGENTS.md" ]]; then
  echo "OK (present):           AGENTS.md"
else
  echo "MISSING FROM REPO:      AGENTS.md"
fi

# A copy also exists at the qwen-experiment profile's own directory
# (from an earlier deploy command). Genuine uncertainty, not confidently
# resolved either way: AGENTS.md discovery is understood to walk from
# cwd up to the git root, with nothing pointing to this profile-specific
# path — but that understanding hasn't been independently confirmed
# against Hermes's actual source, only inferred from documentation and
# observed behavior. Checking it costs nothing; being wrong about it
# being unused and letting it go stale would be a real, silent gap.
check "$REPO/AGENTS.md" \
      "$HOME/.hermes/profiles/qwen-experiment/AGENTS.md" \
      "AGENTS.md (qwen-experiment profile copy — relevance to Hermes's actual behavior unconfirmed, kept in sync as cheap insurance)"
check "$REPO/AGENTS.md" \
      "$HOME/.hermes/profiles/gemma-experiment/AGENTS.md" \
      "AGENTS.md (gemma-experiment profile copy — same unconfirmed-relevance caveat as above)"
check "$REPO/AGENTS.md" \
      "$HOME/.hermes/profiles/devstral-experiment/AGENTS.md" \
      "AGENTS.md (devstral-experiment profile copy — same unconfirmed-relevance caveat as above)"

echo ""
echo "=== ksnctf-submit guardrail state: current attempt counts per problem ==="
# /workspace only exists inside the Docker container — this script runs
# on the host, so the real path is the bind-mount SOURCE directory, not
# the container-internal path the Python script itself uses.
KSNCTF_LOG="$REPO/workspace/.ksnctf_submission_log.json"
if [[ -f "$KSNCTF_LOG" ]]; then
  python3 -c "
import json
with open('$KSNCTF_LOG') as f:
    log = json.load(f)
if not log:
    print('  (log exists but is empty — no submissions recorded yet)')
for problem_id, attempts in log.items():
    print(f'  problem {problem_id}: {len(attempts)}/5 attempts used')
" 2>/dev/null || echo "  (log file exists but could not be parsed as JSON)"
else
  echo "  (no log yet — $KSNCTF_LOG does not exist)"
fi

echo ""
echo "=== config.yaml: repo -> live Hermes config (checked in full, not diffed — this file has local-only settings like tokens that will legitimately differ) ==="
echo "Known-important keys to check by hand:"
grep -A2 "^terminal:" "$HOME/.hermes/config.yaml" 2>/dev/null || echo "  ~/.hermes/config.yaml not found"
grep -A2 "^compression:" "$HOME/.hermes/config.yaml" 2>/dev/null

echo ""
echo "=== Dockerfile: has the built image actually been rebuilt since the last repo change? ==="
# CONFIRMED worth automating: this used to just print both raw
# timestamps (an epoch number and an ISO-8601 string) for manual
# comparison — genuinely error-prone in practice, confirmed directly
# when this exact comparison needed to be worked out by hand with a
# separate Python one-liner rather than trusted by eye. Now computes
# and states the answer directly.
if [[ -f "$REPO/docker/hermes-sandbox.Dockerfile" ]]; then
  DOCKERFILE_MTIME=$(stat -f "%m" "$REPO/docker/hermes-sandbox.Dockerfile" 2>/dev/null || stat -c "%Y" "$REPO/docker/hermes-sandbox.Dockerfile" 2>/dev/null)
  IMAGE_CREATED=$(docker inspect -f '{{.Created}}' hermes-sandbox:latest 2>/dev/null)
  if [[ -z "$IMAGE_CREATED" ]]; then
    echo "NOT YET BUILT:          hermes-sandbox:latest (no such image found)"
  else
    IMAGE_CREATED_EPOCH=$(python3 -c "
import datetime
try:
    dt = datetime.datetime.fromisoformat('$IMAGE_CREATED'.replace('Z', '+00:00'))
    print(int(dt.timestamp()))
except Exception:
    pass
" 2>/dev/null)
    if [[ -z "$IMAGE_CREATED_EPOCH" ]]; then
      echo "COULD NOT PARSE image creation timestamp — falling back to raw values:"
      echo "  Dockerfile modified (epoch): $DOCKERFILE_MTIME"
      echo "  Image created: $IMAGE_CREATED"
    elif [[ "$DOCKERFILE_MTIME" -gt "$IMAGE_CREATED_EPOCH" ]]; then
      echo "STALE — Dockerfile is newer than the built image."
      echo "  Dockerfile modified: $(TZ=UTC date -r "$DOCKERFILE_MTIME" "+%Y-%m-%d %H:%M:%S UTC" 2>/dev/null || TZ=UTC date -d "@$DOCKERFILE_MTIME" "+%Y-%m-%d %H:%M:%S UTC" 2>/dev/null)"
      IMAGE_CREATED_DISPLAY=$(python3 -c "
import datetime
dt = datetime.datetime.fromisoformat('$IMAGE_CREATED'.replace('Z', '+00:00'))
print(dt.strftime('%Y-%m-%d %H:%M:%S UTC'))
" 2>/dev/null)
      echo "  Image built:         ${IMAGE_CREATED_DISPLAY:-$IMAGE_CREATED}"
      echo "  -> rebuild and retag before trusting the sandbox."
    else
      echo "OK — image is at least as new as the current Dockerfile."
    fi
  fi
else
  echo "MISSING FROM REPO:      docker/hermes-sandbox.Dockerfile"
fi
