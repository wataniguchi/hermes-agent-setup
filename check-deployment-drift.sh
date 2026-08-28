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
# Can't diff a repo file against a running image directly — this is a
# reminder, not an automated check. The Dockerfile has been through
# several real, hard-won fixes; a stale image after a Dockerfile edit
# reproduces the exact "fix committed but never actually deployed"
# problem this whole script exists to catch, just one layer removed
# (image vs. container, not repo vs. deployed-file).
if [[ -f "$REPO/docker/hermes-sandbox.Dockerfile" ]]; then
  DOCKERFILE_MTIME=$(stat -f "%m" "$REPO/docker/hermes-sandbox.Dockerfile" 2>/dev/null || stat -c "%Y" "$REPO/docker/hermes-sandbox.Dockerfile" 2>/dev/null)
  IMAGE_CREATED=$(docker inspect -f '{{.Created}}' hermes-sandbox:latest 2>/dev/null)
  if [[ -z "$IMAGE_CREATED" ]]; then
    echo "NOT YET BUILT:          hermes-sandbox:latest (no such image found)"
  else
    echo "Dockerfile last modified (epoch): $DOCKERFILE_MTIME"
    echo "Image hermes-sandbox:latest created: $IMAGE_CREATED"
    echo "  -> compare these by eye; if the Dockerfile is newer than the"
    echo "     image build, rebuild and retag before trusting the sandbox."
  fi
else
  echo "MISSING FROM REPO:      docker/hermes-sandbox.Dockerfile"
fi
