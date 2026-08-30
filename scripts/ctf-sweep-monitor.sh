#!/bin/zsh
#
# scripts/ctf-sweep-monitor.sh — periodically export and tail the
# currently active sweep session, to watch a `-z`/oneshot sweep's real
# progress in real time.
#
# Normally started automatically by scripts/ctf-sweep-watchdog.sh as a
# background companion process — see that script for how it's launched
# and cleaned up. Can also be run standalone, with --session-id, for
# manual monitoring without the watchdog.
#
# HOW SESSION IDENTIFICATION WORKS, AND WHY IT CHANGED:
# Two earlier versions of this script tried to guess the current
# session from `sessions list` alone — first by blind recency (broken:
# any unrelated interactive session someone opens becomes the new top
# row), then by filtering for a "ksnctf" keyword (also broken: equally
# defeated by anyone, or anything, typing that same word into an
# unrelated session — a keyword match is still just a guess, only with
# a narrower guess-space). Guessing isn't necessary at all: the
# watchdog already knows, with certainty, the exact prompt text it just
# fed into each attempt, because it constructed that text itself. This
# script no longer identifies its own session — it reads the ID the
# watchdog already identified using that exact knowledge, written to
# workspace/.ctf-sweep-current-session-id after each attempt launches
# (see ctf-sweep-watchdog.sh's discover_session_id, which matches on an
# exact substring of that attempt's own prompt plus a short recency
# window, via `sessions export --dry-run`'s structured output — not
# `sessions list`'s human-formatted table).
#
# See CTF_GENERALIZATION_DESIGN.md, "Operational resilience" for why
# this exists at all: `-z`'s quiet-stdout design also quiets
# agent.log/errors.log for the normal per-tool-call narration, even
# though the run itself is healthy. `sessions export` reads from the
# same live backing store `sessions list` reads from, so it reflects
# genuine in-progress history, not a stale snapshot.
#
# Usage:
#   chmod +x scripts/ctf-sweep-monitor.sh
#   ./scripts/ctf-sweep-monitor.sh [-p|--profile <name>] [-n|--interval SECONDS] [-c|--chars N] [--session-id ID]
#
# --session-id overrides the shared file entirely — use this for
# standalone monitoring of a specific session without the watchdog
# running at all.
#
# NOTE ON PRIVACY: exported content is passed through --redact, which
# catches recognized credential patterns but NOT flag values themselves
# — fine for local viewing, worth remembering before sharing or
# archiving an export.
#
# Stop with Ctrl-C (or let the parent watchdog's own cleanup handle it,
# if launched that way).

set -u

SCRIPT_DIR="${0:A:h}"
WORKSPACE_DIR="$SCRIPT_DIR/../workspace"
SESSION_ID_FILE="$WORKSPACE_DIR/.ctf-sweep-current-session-id"

PROFILE="gemma-experiment"
INTERVAL=30
TAIL_CHARS=4000
ONCE=0
SESSION_ID_OVERRIDE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        -p|--profile) PROFILE="${2:-}"; shift 2 ;;
        -n|--interval) INTERVAL="${2:-}"; shift 2 ;;
        -c|--chars) TAIL_CHARS="${2:-}"; shift 2 ;;
        --session-id) SESSION_ID_OVERRIDE="${2:-}"; shift 2 ;;
        --once) ONCE=1; shift ;;   # internal: used by the `watch` re-invocation below
        -h|--help)
            echo "Usage: $0 [-p|--profile <name>] [-n|--interval SECONDS] [-c|--chars N] [--session-id ID]"
            exit 0
            ;;
        *) echo "Unknown argument: $1"; exit 1 ;;
    esac
done

EXPORT_DIR="$HOME/.hermes/profiles/$PROFILE/session-exports"

current_session_id() {
    if [[ -n "$SESSION_ID_OVERRIDE" ]]; then
        echo "$SESSION_ID_OVERRIDE"
        return
    fi
    if [[ -f "$SESSION_ID_FILE" ]]; then
        cat "$SESSION_ID_FILE"
    fi
}

poll_once() {
    local session_id
    session_id="$(current_session_id)"

    if [[ -z "$session_id" ]]; then
        echo "No session id available yet."
        echo "Either the watchdog hasn't identified attempt #1's session yet"
        echo "(check $SESSION_ID_FILE), or pass --session-id explicitly for"
        echo "standalone use without the watchdog."
        return
    fi

    hermes -p "$PROFILE" sessions export \
        --session-id "$session_id" \
        --format md \
        --redact \
        --force \
        >/dev/null 2>&1

    local export_file
    export_file="$(ls -t "$EXPORT_DIR"/"$session_id"-*.md 2>/dev/null | head -1)"

    if [[ -z "$export_file" ]]; then
        echo "Session $session_id found but export failed or hasn't landed yet."
        return
    fi

    echo "=== $(date) — session $session_id — last ${TAIL_CHARS} chars of: $export_file ==="
    echo
    tail -c "$TAIL_CHARS" "$export_file"
}

# --once: do a single poll-and-print, then exit. Used both for direct
# single-shot checks and as the target of the `watch` re-invocation
# below (watch already handles the repeat/clear-screen behavior itself,
# so the re-invoked copy of this script should only poll once per call).
if [[ "$ONCE" -eq 1 ]]; then
    poll_once
    exit 0
fi

# `watch` reads from stdin (for its own key handling) and crashes with
# "getchar(): Undefined error: 0" when it doesn't have a real terminal
# attached — confirmed directly: this happened every time, immediately,
# when launched as a background job (`&`) by ctf-sweep-watchdog.sh,
# silently killing the whole monitor process since `exec watch ...`
# replaces this script's own process with it. Only use `watch` when
# stdin is actually a tty (i.e. a human ran this script directly in
# their own terminal); always use the plain loop otherwise, regardless
# of whether `watch` happens to be installed.
if [[ -t 0 ]] && command -v watch >/dev/null 2>&1; then
    exec watch -n "$INTERVAL" "$0" -p "$PROFILE" -n "$INTERVAL" -c "$TAIL_CHARS" --session-id "$SESSION_ID_OVERRIDE" --once
fi

# Fallback for machines without `watch` installed (not preinstalled on
# stock macOS — `brew install watch` adds it), or when stdin isn't a
# tty (e.g. started in the background by ctf-sweep-watchdog.sh).
if ! [[ -t 0 ]]; then
    echo "No tty attached (likely started in the background) — using a plain loop instead of 'watch'."
else
    echo "'watch' not found — using a plain loop instead (Ctrl-C to stop)."
    echo "Tip: 'brew install watch' gives cleaner screen-clearing output."
fi
echo
while true; do
    [[ -t 1 ]] && clear
    poll_once
    sleep "$INTERVAL"
done
