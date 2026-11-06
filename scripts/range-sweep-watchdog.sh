#!/bin/zsh
#
# scripts/range-sweep-watchdog.sh — auto-resume the Hermes range-assess
# sweep on any exit, using the identical headless pattern
# scripts/ctf-sweep-watchdog.sh already uses for the ksnctf sweep. This
# is NOT a new mechanism — same `hermes -p <profile> -z <prompt>`
# oneshot call, same init/resume prompt-file selection by whether a
# state file already exists, same session-id discovery by the ID's own
# embedded timestamp, same unconditional-on-exit transcript archiving.
# Operator-run only, outside the Docker sandbox, same as that script.
#
# WHAT'S DELIBERATELY DIFFERENT FROM ctf-sweep-watchdog.sh, AND WHY:
#
#   - Monitor companion: started here too, by reusing the SAME
#     ctf-sweep-monitor.sh script ctf-sweep-watchdog.sh already starts
#     — not a forked copy. That script originally hardcoded its own
#     session-id file; it's now generalized with a --session-id-file
#     flag (defaulting to the unchanged ksnctf path, so
#     ctf-sweep-watchdog.sh's own unmodified invocation still behaves
#     identically). This script launches it with
#     --session-id-file pointed at its own
#     workspace/.range-sweep-current-session-id instead. See --no-monitor
#     below to disable it, same as ctf-sweep-watchdog.sh. With it
#     running, watch progress via its periodic session-export tail, or
#     directly via:
#         python3 skills/security/range-assess/scripts/range_traversal.py status
#     or, for live output from the attempt currently running, tail
#         workspace/.range-sweep-watchdog-output.log
#   - No watchdog-side note distillation. That mechanism exists for
#     ctf_traversal.py's free-text per-problem progress notes — a
#     prose file an agent might forget to update, which a crash could
#     then strand indefinitely. range_traversal.py has no analogous
#     free-text note: all per-host/per-finding state is structured JSON
#     written by range_traversal.py's own commands on every call, so
#     there's nothing comparable for a distillation step to reconstruct
#     after a crash — status is always exactly as current as the last
#     command that ran, with no separate self-reported note to verify
#     or discard.
#
# Usage (run from the repo root):
#   chmod +x scripts/range-sweep-watchdog.sh
#   ./scripts/range-sweep-watchdog.sh [-p|--profile <name>] [--init|--resume] [--config <path>]
#
# Examples:
#   ./scripts/range-sweep-watchdog.sh                          # profile: qwen-experiment, auto-detect init/resume
#   ./scripts/range-sweep-watchdog.sh -p hermes                # different profile
#   ./scripts/range-sweep-watchdog.sh --config /path/to/hosts.json --init   # force a genuine fresh start
#   ./scripts/range-sweep-watchdog.sh --resume                 # force resume even on attempt #1
#
# --config matters only for the FIRST attempt's init prompt (it's
# substituted into the `init --config <path>` command range_traversal.py
# actually runs) — ignored on every resume attempt, since init is never
# re-run once a traversal has begun. Defaults to the path your own real,
# untracked hosts.json lives at inside the sandbox — this script does
# NOT ship or reference any real range data itself (see
# skills/security/range-assess/config/hosts.json's own header: the
# committed copy is a sanitized template).
#
# Stop with Ctrl-C, or `pkill -f range-sweep-watchdog.sh` from elsewhere.
#
# A KNOWN LIMIT OF Ctrl-C/cleanup() HERE, CONFIRMED FOR REAL: stopping
# this script (or the `hermes -z` process it launched) does NOT
# necessarily stop everything that attempt was doing to the bridge.
# Hermes runs a separate, long-lived gateway/supervisor process
# (`hermes gateway run --external-supervisor`), distinct from each
# disposable `hermes -z ...` oneshot CLI invocation. A
# `terminal(background=true)` call the AGENT itself made mid-attempt
# (e.g. a backgrounded `/vm/clone` + `/vm/<id>/start` chain, or a long
# polling loop hitting `/vm/<id>/exec` every N seconds) is owned and
# kept alive by that gateway, not by the oneshot CLI process this
# script's `hermes_pid`/`kill` actually targets. Killing `hermes_pid`
# reaches the CLI; it does NOT reach a background job the agent already
# launched inside the sandbox — that keeps running, and keeps calling
# the bridge, for as long as its own loop/iteration count has left,
# regardless of what you just killed here.
#
# Confirmed directly: after killing this watchdog (and its hermes_pid),
# POST /vm/<id>/exec calls kept landing on the bridge's own access log.
# Restarting the bridge process itself does NOT stop this either — the
# bridge has no state to lose; something is still actively SENDING
# those requests. The actual fix is to find and stop whatever's still
# running inside the sandbox container itself:
#   docker ps                              # find the sandbox container
#   docker exec <container> ps -ef         # find the offending curl/bash loop
#   docker exec <container> kill <pid>     # stop just that process, or:
#   docker restart <container>             # stop everything running inside it
# `cleanup()` below prints a reminder to this effect on an interrupted
# attempt, but cannot do this itself — it has no visibility into, or
# any mechanism to reach, a process the agent backgrounded on its own.

set -u

SCRIPT_DIR="${0:A:h}"
RESTART_DELAY_SECONDS=5

WORKSPACE_DIR="$SCRIPT_DIR/../workspace"
WORKSPACE_STATE_FILE="$WORKSPACE_DIR/.range_traversal_state.json"
OUTPUT_LOG="$WORKSPACE_DIR/.range-sweep-watchdog-output.log"
USAGE_FILE="$WORKSPACE_DIR/.range-sweep-last-usage.json"
SESSION_ID_FILE="$WORKSPACE_DIR/.range-sweep-current-session-id"

SESSION_EXPORTS_DIR="$WORKSPACE_DIR/range-session-exports"
SESSION_EXPORTS_INDEX="$SESSION_EXPORTS_DIR/index.txt"

PROFILE="qwen-experiment"
MODE=""   # "" = auto-detect on attempt #1; "init" or "resume" = forced every attempt
HOSTS_CONFIG="/root/.hermes/skills/security/range-assess/config/hosts.json"
START_MONITOR=1

usage() {
    cat <<EOF
Usage: $0 [-p|--profile <name>] [--init|--resume] [--config <path>] [--no-monitor]

  -p, --profile <name>   The Hermes profile to run under. Passed as
                          "hermes -p <name> -z <prompt>". Default:
                          qwen-experiment

  --config <path>         Path (inside the sandbox) to your real,
                          filled-in hosts.json. Only used to build
                          attempt #1's init prompt — ignored on resume.
                          Default: $HOSTS_CONFIG

  --init                 Force the fresh-start prompt (runs
                          range_traversal.py init) on every attempt,
                          including relaunches. Rarely what you want
                          past attempt #1 — mainly useful for testing.

  --resume               Force the resume prompt on every attempt,
                          including the very first one.

  --no-monitor            Don't start scripts/ctf-sweep-monitor.sh (the
                          same script ctf-sweep-watchdog.sh uses) as a
                          background companion process. Use this if
                          you'd rather run it yourself in its own
                          terminal, or don't want the periodic
                          session-export activity at all.

  With neither --init nor --resume given: attempt #1 auto-detects by
  checking whether
  $WORKSPACE_STATE_FILE
  exists yet. If not, the init prompt is used (genuine fresh start).
  If it does, the resume prompt is used. Every attempt after the first
  always uses the resume prompt regardless, since by then a traversal
  has begun no matter which prompt started it.
EOF
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -p|--profile)
            PROFILE="${2:-}"
            [[ -z "$PROFILE" ]] && { echo "Missing value for $1"; usage; }
            shift 2
            ;;
        --config)
            HOSTS_CONFIG="${2:-}"
            [[ -z "$HOSTS_CONFIG" ]] && { echo "Missing value for $1"; usage; }
            shift 2
            ;;
        --init) MODE="init"; shift ;;
        --resume) MODE="resume"; shift ;;
        --no-monitor) START_MONITOR=0; shift ;;
        -h|--help) usage ;;
        *) echo "Unknown argument: $1"; usage ;;
    esac
done

LOG_DIR="$HOME/.hermes/profiles/$PROFILE/logs"

echo "=== range-sweep-watchdog starting: $(date) ==="
echo "Profile: $PROFILE"
echo "Output log: $OUTPUT_LOG"
if [[ -n "$MODE" ]]; then
    echo "Mode: forced '$MODE' on every attempt"
else
    echo "Mode: auto-detect on attempt #1 (checking for $WORKSPACE_STATE_FILE)"
fi

MONITOR_PID=""
if [[ "$START_MONITOR" -eq 1 ]]; then
    MONITOR_SCRIPT="$SCRIPT_DIR/ctf-sweep-monitor.sh"
    if [[ -x "$MONITOR_SCRIPT" ]]; then
        # Same script ctf-sweep-watchdog.sh starts for the ksnctf sweep
        # — reused as-is, just pointed at this sweep's own session-id
        # file via --session-id-file (see that script's own header
        # comment for the generalization). Started once, not per-attempt
        # — it self-detects whichever session is currently active on
        # every poll, so it keeps following the sweep transparently
        # across every relaunch below.
        "$MONITOR_SCRIPT" -p "$PROFILE" --session-id-file "$SESSION_ID_FILE" &
        MONITOR_PID=$!
        echo "Monitor: started in background (pid $MONITOR_PID) — scripts/ctf-sweep-monitor.sh -p $PROFILE --session-id-file $SESSION_ID_FILE"
    else
        echo "Monitor: $MONITOR_SCRIPT not found or not executable — skipping (run with --no-monitor to silence this)"
    fi
else
    echo "Monitor: disabled (--no-monitor)"
fi

# Unconditional on exit path, same rationale as ctf-sweep-watchdog.sh's
# own archive_session_transcript: a crash or interrupt is exactly the
# case this must not depend on a clean ending to still capture.
archive_session_transcript() {
    local sid="${1:-}"
    local label="${2:-current attempt}"
    if [[ -z "$sid" ]]; then
        echo "WARNING: no session id known for $label — cannot archive its transcript." | tee -a "$OUTPUT_LOG"
        return
    fi
    mkdir -p "$SESSION_EXPORTS_DIR"
    hermes -p "$PROFILE" sessions export \
        --session-id "$sid" \
        --format md \
        --redact \
        --force \
        >> "$OUTPUT_LOG" 2>&1
    local export_src
    export_src="$(ls -t "$HOME/.hermes/profiles/$PROFILE/session-exports/$sid"-*.md 2>/dev/null | head -1)"
    if [[ -n "$export_src" ]]; then
        cp "$export_src" "$SESSION_EXPORTS_DIR/$sid.md"
        echo "$sid" >> "$SESSION_EXPORTS_INDEX"
        echo "Archived $label's transcript: $SESSION_EXPORTS_DIR/$sid.md" | tee -a "$OUTPUT_LOG"
    else
        echo "WARNING: could not export/archive $label's transcript (session $sid) — export produced no file." | tee -a "$OUTPUT_LOG"
    fi
}

_cleaned_up=0
cleanup() {
    [[ "$_cleaned_up" -eq 1 ]] && return
    _cleaned_up=1
    if [[ -n "${hermes_pid:-}" ]]; then
        echo
        echo "Stopping in-progress hermes attempt (pid $hermes_pid)..."
        kill "$hermes_pid" 2>/dev/null
        wait "$hermes_pid" 2>/dev/null
        archive_session_transcript "${session_id:-}" "interrupted attempt #${attempt:-?}"
        echo "NOTE: if this attempt had cloned/started a working VM (<host>-ctf) that never" | tee -a "$OUTPUT_LOG"
        echo "finished its assessment, it is still running in Proxmox — range_traversal.py" | tee -a "$OUTPUT_LOG"
        echo "does not destroy it automatically on an interrupted attempt. Check" | tee -a "$OUTPUT_LOG"
        echo "'range_traversal.py status' for any working_vmid left set before assuming" | tee -a "$OUTPUT_LOG"
        echo "it needs re-cloning on resume." | tee -a "$OUTPUT_LOG"
        echo "NOTE: killing hermes_pid stops the oneshot CLI process ONLY — it does NOT" | tee -a "$OUTPUT_LOG"
        echo "reach any terminal(background=true) job the agent itself launched mid-attempt" | tee -a "$OUTPUT_LOG"
        echo "(a backgrounded clone/start chain, a polling loop hitting /vm/<id>/exec, etc.)." | tee -a "$OUTPUT_LOG"
        echo "Those are owned by Hermes's own long-lived gateway process, not this CLI, and" | tee -a "$OUTPUT_LOG"
        echo "keep running and keep calling the bridge regardless of this kill. Confirmed for" | tee -a "$OUTPUT_LOG"
        echo "real: restarting the bridge process doesn't stop it either (nothing server-side" | tee -a "$OUTPUT_LOG"
        echo "to lose) -- something is still actively sending requests. If the bridge's own" | tee -a "$OUTPUT_LOG"
        echo "access log keeps showing new calls after this, find and stop it inside the" | tee -a "$OUTPUT_LOG"
        echo "sandbox container directly: docker ps; docker exec <container> ps -ef; then" | tee -a "$OUTPUT_LOG"
        echo "kill the offending pid (or docker restart <container> to stop everything in it)." | tee -a "$OUTPUT_LOG"
    fi
    if [[ -n "${MONITOR_PID:-}" ]]; then
        echo "Stopping monitor (pid $MONITOR_PID)..."
        kill "$MONITOR_PID" 2>/dev/null
    fi
}
trap cleanup EXIT
trap 'cleanup; exit 130' INT TERM

echo "Press Ctrl-C to stop."
echo

# Identical mechanism to ctf-sweep-watchdog.sh's own discover_session_id
# — see that script's header comment for why this, specifically, is
# reliable (the session ID's own embedded "YYYYMMDD_HHMMSS" prefix)
# where three earlier approaches based on displayed/async signals
# weren't.
discover_session_id() {
    local content_key="$1"
    local launch_ts="$2"
    local max_wait=60
    local waited=0
    local sid best_id
    while (( waited < max_wait )); do
        best_id=""
        while IFS= read -r sid; do
            [[ -z "$sid" ]] && continue
            if [[ "${sid[1,15]}" > "$launch_ts" || "${sid[1,15]}" == "$launch_ts" ]]; then
                if [[ -z "$best_id" || "${sid[1,15]}" > "${best_id[1,15]}" ]]; then
                    best_id="$sid"
                fi
            fi
        done < <(hermes -p "$PROFILE" sessions list 2>/dev/null | grep -F -- "$content_key" | awk '{print $NF}')

        if [[ -n "$best_id" ]]; then
            echo "$best_id"
            return 0
        fi
        sleep 2
        waited=$((waited + 2))
    done
    return 1
}

build_init_prompt() {
    # The committed init-prompt file's STEP 1 line hardcodes a --config
    # path as a readable example, not your real one. Substitute it here
    # rather than editing that file per-invocation, so the file on disk
    # stays the documentation copy.
    local template="$1"
    local config_path="$2"
    sed "s|--config /root/.hermes/skills/security/range-assess/config/hosts.json|--config ${config_path}|" "$template"
}

attempt=0
while true; do
    attempt=$((attempt + 1))

    if [[ -n "$MODE" ]]; then
        this_mode="$MODE"
    elif [[ $attempt -eq 1 ]]; then
        if [[ -f "$WORKSPACE_STATE_FILE" ]]; then
            this_mode="resume"
        else
            this_mode="init"
        fi
    else
        this_mode="resume"
    fi

    if [[ "$this_mode" == "init" ]]; then
        prompt_file="$SCRIPT_DIR/range-sweep-init-prompt.txt"
        if [[ ! -f "$prompt_file" ]]; then
            echo "Prompt file not found: $prompt_file" | tee -a "$OUTPUT_LOG"
            exit 1
        fi
        prompt_text="$(build_init_prompt "$prompt_file" "$HOSTS_CONFIG")"
    else
        prompt_file="$SCRIPT_DIR/range-sweep-resume-prompt.txt"
        if [[ ! -f "$prompt_file" ]]; then
            echo "Prompt file not found: $prompt_file" | tee -a "$OUTPUT_LOG"
            exit 1
        fi
        prompt_text="$(cat "$prompt_file")"
    fi

    {
        echo "--- Attempt #$attempt ($this_mode): launching 'hermes -p $PROFILE -z ...': $(date) ---"
    } | tee -a "$OUTPUT_LOG"

    # Same race guard as ctf-sweep-watchdog.sh, and deliberately matched
    # on profile alone, not on "range" vs "ctf" content — if you run
    # both sweeps under the SAME profile, they would otherwise race on
    # that profile's own resources (the resident Ollama model, rate
    # limits, etc.) even though they write to different state files.
    # Use separate profiles if you want both sweeps able to run
    # concurrently.
    while pgrep -f "hermes -p $PROFILE -z" >/dev/null 2>&1; do
        existing_pid="$(pgrep -f "hermes -p $PROFILE -z" | head -1)"
        {
            echo "A hermes -z process for profile '$PROFILE' is already running (pid $existing_pid) — waiting for it to finish before starting attempt #$attempt."
        } | tee -a "$OUTPUT_LOG"
        sleep 30
    done

    launch_ts="$(date +%Y%m%d_%H%M%S)"
    hermes -p "$PROFILE" -z "$prompt_text" \
        --accept-hooks \
        --usage-file "$USAGE_FILE" \
        >> "$OUTPUT_LOG" 2>&1 &
    hermes_pid=$!

    content_key="${prompt_text[1,20]}"
    if session_id="$(discover_session_id "$content_key" "$launch_ts")"; then
        echo "$session_id" > "$SESSION_ID_FILE"
        echo "Attempt #$attempt session id: $session_id" | tee -a "$OUTPUT_LOG"
    else
        echo "WARNING: could not identify attempt #$attempt's session id within the discovery timeout." | tee -a "$OUTPUT_LOG"
    fi

    wait "$hermes_pid"
    exit_code=$?

    {
        echo
        echo "--- Attempt #$attempt exited (code $exit_code): $(date) ---"
    } | tee -a "$OUTPUT_LOG"

    archive_session_transcript "${session_id:-}" "attempt #$attempt"

    if [ -d "$LOG_DIR" ]; then
        echo "--- Last 5 agent.log lines: ---" | tee -a "$OUTPUT_LOG"
        tail -5 "$LOG_DIR"/agent.log 2>/dev/null | tee -a "$OUTPUT_LOG"
        echo "--- end log tail ---" | tee -a "$OUTPUT_LOG"
    fi

    echo "Resuming in ${RESTART_DELAY_SECONDS}s... (Ctrl-C to stop)"
    sleep "$RESTART_DELAY_SECONDS"
    echo
done
