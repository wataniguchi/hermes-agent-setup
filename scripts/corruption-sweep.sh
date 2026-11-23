#!/usr/bin/env bash
# corruption-sweep.sh
#
# Standalone corruption-marker sweep. Meant to be `source`d (not run
# directly) from your watchdog script and invoked at the existing
# restart-decision checkpoint (wherever `sweep_is_complete` or its
# equivalent is checked), before deciding whether/how to relaunch hermes.
#
# What it does on finding a marker-containing file:
#   1. Quarantines it — moves it aside so nothing can accidentally
#      re-trust or re-execute it on a later restart.
#   2. Logs a clear entry to the watchdog's own log.
#   3. Appends a structured JSONL line to a review-items file, meant to be
#      picked up by whatever already compiles the final report / operator
#      review list — same severity tier as a needs_operator_decision item.
#
# It does NOT attempt to contact or redirect the live hermes process.
# Hermes is run headless here, with no confirmed external injection
# channel, so this is a passive safety net, not a live corrective action.
# The in-the-moment catch is the agent's own verify-then-run guard (see
# SKILL-safe-run-guardrails.md); this sweep exists for what that doesn't
# cover — a corrupted file that was written but never actually run, or a
# corruption that slipped through for any other reason.
#
# USAGE (from the watchdog script):
#
#   source /path/to/corruption-sweep.sh
#   ...
#   if ! sweep_is_complete; then
#       run_corruption_sweep "$WORKSPACE_DIR" "$WATCHDOG_LOG" "$REVIEW_ITEMS_FILE"
#       # exit code 1 = found and quarantined something; 0 = clean sweep
#       ...
#   fi
#
# All three arguments are required. Nothing here assumes paths beyond what
# you pass in.

MARKER='HERMES-CONTEXT-COMPRESSION'

run_corruption_sweep() {
    local workspace_dir="$1"
    local watchdog_log="$2"
    local review_items_file="$3"

    if [[ -z "$workspace_dir" || -z "$watchdog_log" || -z "$review_items_file" ]]; then
        echo "run_corruption_sweep: workspace_dir, watchdog_log, and review_items_file are all required" >&2
        return 2
    fi

    # Strip a trailing slash so the rel-path stripping below is reliable.
    workspace_dir="${workspace_dir%/}"

    local quarantine_dir="${workspace_dir}/.corrupted"
    local found=0
    local ts
    ts="$(date -u +%Y%m%dT%H%M%SZ)"

    # -l: filenames only. -Z (GNU and BSD/macOS grep both support it):
    # null-separated output, so filenames with spaces/newlines survive.
    while IFS= read -r -d '' file; do
        # Skip files already sitting in the quarantine dir from a previous
        # sweep — they still contain the marker (we moved, not fixed), and
        # re-sweeping the workspace would otherwise re-quarantine them
        # forever.
        case "$file" in
            "${quarantine_dir}"/*) continue ;;
        esac

        found=1

        mkdir -p "$quarantine_dir"
        local rel dest
        rel="${file#"$workspace_dir"/}"
        # Flatten the relative path into the filename so nested
        # directories don't need to be recreated under .corrupted/, and
        # timestamp it so repeated hits on the same path never collide.
        dest="${quarantine_dir}/$(echo "$rel" | tr '/' '_')__${ts}"

        mv "$file" "$dest"

        local log_line="[corruption-sweep] ${ts} quarantined ${file} -> ${dest} (contains ${MARKER} marker)"
        echo "$log_line" | tee -a "$watchdog_log" >/dev/null

        # One JSON line per hit, readable as JSONL by whatever already
        # assembles the final report / review-item list.
        printf '{"ts":"%s","severity":"needs_operator_decision","kind":"context_compression_corruption","original_path":"%s","quarantined_path":"%s"}\n' \
            "$ts" "$file" "$dest" >> "$review_items_file"

    done < <(grep -rlZ "$MARKER" "$workspace_dir" 2>/dev/null)

    if [[ "$found" -eq 1 ]]; then
        echo "[corruption-sweep] ${ts} found and quarantined corrupted file(s) under ${workspace_dir} — see ${review_items_file}" | tee -a "$watchdog_log" >/dev/null
        return 1   # non-zero = "found something"; caller can branch on this
    fi

    return 0
}
