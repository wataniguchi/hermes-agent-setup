#!/bin/zsh
#
# scripts/ctf-sweep-watchdog.sh — auto-resume the Hermes ksnctf sweep on
# any exit, using hermes's genuine headless mode. Also starts and stops
# scripts/ctf-sweep-monitor.sh as a background companion, so a human
# never has to separately remember to launch it.
#
# Operator-run only. This is NOT a skill — it isn't invoked by the agent
# from inside the Docker sandbox, it supervises the agent from outside,
# the same way scripts/start-proxmox-bridge.sh supervises the Proxmox
# bridge. See CTF_GENERALIZATION_DESIGN.md, "Operational resilience" for
# the full rationale and how this fits alongside the AGENTS.md
# file-validation rule and the error_classifier.py recovery patch — this
# script is the backstop for whatever those two don't catch.
#
# HISTORY: earlier versions of this script drove the interactive
# `<profile> chat` TUI via `expect`, which turned out to be fighting a
# whole class of problems that don't need to exist at all: the TUI
# probes the terminal for capabilities (background color, device
# attributes) that a real pty automation tool doesn't answer, which
# apparently caused it to fall back to opening an external editor
# instead of accepting input. `hermes --help` revealed a genuinely
# headless entrypoint built for exactly this use case:
#
#   -z PROMPT, --oneshot PROMPT
#       One-shot mode: send a single prompt and print ONLY the final
#       response text to stdout. No banner, no spinner, no tool
#       previews. Tools, memory, rules, and AGENTS.md are loaded as
#       normal; approvals are auto-bypassed. Intended for scripts/pipes.
#
# This runs through the identical agent engine as `chat` — same
# conversation_loop.py, same tool-calling, same error_classifier.py
# recovery patch — it just never renders a TUI, so none of the pty
# problems above exist: no terminal probes to answer, no editor
# fallback, no bracketed-paste concerns, no expect dependency at all.
# "Single prompt" means one input message, not one tool call — the
# agent still runs its normal multi-tool-call turn internally and only
# returns once it stops calling tools on its own (or hits a turn/budget
# limit), exactly matching the "Turn ended: reason=text_response"
# pattern seen throughout this project's logs under `chat` too.
#
# Usage (run from the repo root):
#   chmod +x scripts/ctf-sweep-watchdog.sh
#   ./scripts/ctf-sweep-watchdog.sh [-p|--profile <name>] [--init|--resume]
#
# Examples:
#   ./scripts/ctf-sweep-watchdog.sh                  # profile: gemma-experiment, auto-detect init/resume
#   ./scripts/ctf-sweep-watchdog.sh -p hermes         # different profile
#   ./scripts/ctf-sweep-watchdog.sh -p hermes --init  # force a genuine fresh start
#   ./scripts/ctf-sweep-watchdog.sh --resume          # force resume even on attempt #1
#
# No dependency on `expect` anymore — this is a plain blocking
# subprocess call, same as any other CLI tool.
#
# Stop it with Ctrl-C, or `pkill -f ctf-sweep-watchdog.sh` from elsewhere.

set -u

SCRIPT_DIR="${0:A:h}"
RESTART_DELAY_SECONDS=5

# Traversal state lives in the host-mounted workspace dir, shared across
# profiles/sessions (see AGENTS.md's description of the /workspace
# mount). Its existence is what distinguishes "a sweep has already
# begun" from "this is a genuine first run" for the auto-detect logic
# below.
WORKSPACE_DIR="$SCRIPT_DIR/../workspace"
WORKSPACE_STATE_FILE="$WORKSPACE_DIR/.ctf_traversal_state.json"
OUTPUT_LOG="$WORKSPACE_DIR/.ctf-sweep-watchdog-output.log"
USAGE_FILE="$WORKSPACE_DIR/.ctf-sweep-last-usage.json"
SESSION_ID_FILE="$WORKSPACE_DIR/.ctf-sweep-current-session-id"

PROFILE="gemma-experiment"
MODE=""   # "" = auto-detect on attempt #1; "init" or "resume" = forced every attempt
START_MONITOR=1

usage() {
    cat <<EOF
Usage: $0 [-p|--profile <name>] [--init|--resume] [--no-monitor]

  -p, --profile <name>   The Hermes profile to run under, e.g.
                          'gemma-experiment' or 'hermes'. Passed as
                          "hermes -p <name> -z <prompt>". Default:
                          gemma-experiment

  --init                 Force the fresh-start prompt (runs
                          ctf_traversal.py init) on every attempt,
                          including relaunches. Rarely what you want
                          past attempt #1 — mainly useful for testing.

  --resume               Force the resume prompt on every attempt,
                          including the very first one.

  --no-monitor            Don't start scripts/ctf-sweep-monitor.sh as a
                          background companion process. Use this if
                          you'd rather run the monitor yourself in its
                          own terminal, or don't want the periodic
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
        --init) MODE="init"; shift ;;
        --resume) MODE="resume"; shift ;;
        --no-monitor) START_MONITOR=0; shift ;;
        -h|--help) usage ;;
        *) echo "Unknown argument: $1"; usage ;;
    esac
done

LOG_DIR="$HOME/.hermes/profiles/$PROFILE/logs"

echo "=== ctf-sweep-watchdog starting: $(date) ==="
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
        # Started once, not per-attempt: ctf-sweep-monitor.sh
        # self-detects whichever session is currently active on every
        # poll (see its own header comment), so it keeps following the
        # sweep transparently across every relaunch below without this
        # script needing to tell it anything about which attempt is
        # running. Output interleaves with this script's own — fine for
        # a background companion; run with --no-monitor and start it
        # manually in a separate terminal if you'd rather keep the two
        # separate.
        "$MONITOR_SCRIPT" -p "$PROFILE" &
        MONITOR_PID=$!
        echo "Monitor: started in background (pid $MONITOR_PID) — scripts/ctf-sweep-monitor.sh -p $PROFILE"
    else
        echo "Monitor: $MONITOR_SCRIPT not found or not executable — skipping (run with --no-monitor to silence this)"
    fi
else
    echo "Monitor: disabled (--no-monitor)"
fi

# Clean up on any exit path — Ctrl-C, an error exit further down, or
# normal completion (this loop never completes normally today, but the
# trap covers it regardless).
#
# Two bugs fixed here after a real, serious incident: (1) a trap that
# only runs a handler, without the handler calling `exit`, does NOT
# terminate the script — execution just continues from wherever it
# was. `trap cleanup INT` alone meant Ctrl-C ran cleanup and then fell
# straight back into the while loop, forever, with no way to actually
# stop the script short of `kill -9`. (2) `hermes -p ... -z ... &` is a
# background job — background jobs never receive the terminal's Ctrl-C
# at all, only the foreground script process does, so the actual
# hermes attempt kept running completely untouched through every
# Ctrl-C press; only the monitor (also backgrounded) was ever being
# killed. `cleanup` now also kills `$hermes_pid` explicitly, and the
# INT/TERM trap explicitly calls `exit` after cleanup. Guarded with
# `_cleaned_up` so it's safe to run twice (an explicit `exit` from the
# INT/TERM handler also triggers the EXIT trap) without double-killing
# or double-printing.
_cleaned_up=0
cleanup() {
    [[ "$_cleaned_up" -eq 1 ]] && return
    _cleaned_up=1
    if [[ -n "${hermes_pid:-}" ]]; then
        echo
        echo "Stopping in-progress hermes attempt (pid $hermes_pid)..."
        kill "$hermes_pid" 2>/dev/null
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

# Discover the session ID for the attempt this script itself just
# launched, using two things the watchdog already knows with total
# certainty: the exact moment it launched, and the fact that Hermes's
# own session IDs are literally "YYYYMMDD_HHMMSS_hex" — the same
# format `date +%Y%m%d_%H%M%S` produces. No display column needed at
# all: a session belongs to this attempt only if its ID's own embedded
# timestamp is not older than launch_ts (captured right before this
# attempt's `hermes ... &` call below); among any that qualify, take
# the newest one.
#
# Three earlier approaches all failed by relying on some *displayed*
# signal instead — a --title field that's empty until an async
# background call fills it in later; `sessions list`'s row order,
# which an old still-active session can legitimately sort above a
# brand-new one; and an exact "just now" match, defeated the moment two
# sessions were active in the same rounding window (confirmed directly
# — real output showed two different session IDs both reading "just
# now" simultaneously). None of those were ever necessary: the ID
# itself already carries an exact, second-precision timestamp in every
# single row, with no coarse rounding or async delay at all.
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
            # First 15 chars of a session ID are "YYYYMMDD_HHMMSS" —
            # fixed-width and zero-padded, so plain string comparison
            # is equivalent to chronological comparison.
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
        # Any attempt past the first is always a resume: a traversal
        # exists by now regardless of how attempt #1 started it.
        this_mode="resume"
    fi

    if [[ "$this_mode" == "init" ]]; then
        prompt_file="$SCRIPT_DIR/ctf-sweep-init-prompt.txt"
    else
        prompt_file="$SCRIPT_DIR/ctf-sweep-resume-prompt.txt"
    fi

    if [[ ! -f "$prompt_file" ]]; then
        echo "Prompt file not found: $prompt_file" | tee -a "$OUTPUT_LOG"
        exit 1
    fi

    prompt_text="$(cat "$prompt_file")"

    {
        echo "--- Attempt #$attempt ($this_mode): launching 'hermes -p $PROFILE -z ...': $(date) ---"
    } | tee -a "$OUTPUT_LOG"

    # Guard against two hermes -z processes for this profile ever
    # running concurrently — e.g. a manually-started resume invocation
    # left running in another terminal, or (defensively) a second copy
    # of this watchdog script started by accident. Both would otherwise
    # race on the same shared ctf_traversal.py state file. Checked via
    # a direct OS-level process match (pgrep -f), not sessions list's
    # Preview/Last-Active columns — a live process is unambiguous,
    # whereas matching truncated preview text against a "just now"
    # timestamp that may not refresh during a long silent tool call
    # could produce a false negative and launch a duplicate anyway.
    while pgrep -f "hermes -p $PROFILE -z" >/dev/null 2>&1; do
        existing_pid="$(pgrep -f "hermes -p $PROFILE -z" | head -1)"
        {
            echo "A hermes -z process for profile '$PROFILE' is already running (pid $existing_pid) — waiting for it to finish before starting attempt #$attempt, to avoid two invocations racing on shared traversal state."
        } | tee -a "$OUTPUT_LOG"
        sleep 30
    done

    # --accept-hooks: without a TTY at all (unlike the earlier
    # expect/pty approach), any shell-hook approval prompt would block
    # forever with nobody to answer it — this is the headless-safe
    # equivalent of what a human would click through interactively.
    # -z's own docs say dangerous-command approvals are already
    # auto-bypassed in oneshot mode, so --yolo shouldn't be needed on
    # top of that; add it explicitly only if a run still stalls on an
    # approval prompt in practice.
    #
    # launch_ts captured immediately before backgrounding: this is the
    # known-good lower bound discover_session_id compares session IDs'
    # own embedded timestamps against.
    #
    # Launched in the background, not blocking directly, so this script
    # can identify the resulting session (see discover_session_id above)
    # while the attempt is running — then immediately `wait`s for it,
    # preserving the same sequential, one-attempt-at-a-time semantics
    # as a plain foreground call.
    launch_ts="$(date +%Y%m%d_%H%M%S)"
    hermes -p "$PROFILE" -z "$prompt_text" \
        --accept-hooks \
        --usage-file "$USAGE_FILE" \
        >> "$OUTPUT_LOG" 2>&1 &
    hermes_pid=$!

    content_key="${prompt_text[1,20]}"
    if session_id="$(discover_session_id "$content_key" "$launch_ts")"; then
        echo "$session_id" > "$SESSION_ID_FILE"
        echo "Attempt #$attempt session id: $session_id (recorded to $SESSION_ID_FILE for the monitor)" | tee -a "$OUTPUT_LOG"
    else
        echo "WARNING: could not identify attempt #$attempt's session id within the discovery timeout — the monitor may show stale data until the next attempt." | tee -a "$OUTPUT_LOG"
    fi

    wait "$hermes_pid"
    exit_code=$?

    {
        echo
        echo "--- Attempt #$attempt exited (code $exit_code): $(date) ---"
    } | tee -a "$OUTPUT_LOG"

    if [ -d "$LOG_DIR" ]; then
        echo "--- Last 5 agent.log lines: ---" | tee -a "$OUTPUT_LOG"
        tail -5 "$LOG_DIR"/agent.log 2>/dev/null | tee -a "$OUTPUT_LOG"
        echo "--- end log tail ---" | tee -a "$OUTPUT_LOG"
    fi

    echo "Resuming in ${RESTART_DELAY_SECONDS}s... (Ctrl-C to stop)"
    sleep "$RESTART_DELAY_SECONDS"
    echo
done
