#!/usr/bin/env bash
# Backup and restore the sweep's workspace state — traversal state,
# submission log, progress notes, write-ups, and the session-export
# archive. workspace/ lives entirely outside git (see .gitignore), so
# this is the only safety net for anything the sweep has produced.
#
# Usage:
#   ./ctf-sweep-backup.sh backup                  # create a timestamped backup
#   ./ctf-sweep-backup.sh list                    # list existing backups
#   ./ctf-sweep-backup.sh restore <backup-file>   # restore from a backup (destructive)
#
# Originally a few ad-hoc commands run by hand before a risky MLX
# backend trial, with the explicit intent "if this doesn't fly, I'm
# happy to throw away whatever it produced and restore the checkpoint."
# Promoted to a real script once a second real use arrived: backing up
# before a deliberate clean-slate restart of the sweep itself.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
WORKSPACE_DIR="$REPO_DIR/workspace"
BACKUP_DIR="$REPO_DIR/workspace-archive"

# Files/directories worth confirming landed in a backup — not an
# exhaustive list of everything workspace/ might contain, just the
# pieces tonight's whole design has been built around: without these,
# a "successful" backup would still be a silent gap.
KEY_PATTERNS=(
  "ctf_traversal_state"
  "ksnctf_submission_log"
  "progress-notes/"
  "session-exports/"
  "writeups/"
  "skill-improvement-notes"
)

cmd_backup() {
  if [[ ! -d "$WORKSPACE_DIR" ]]; then
    echo "ERROR: no workspace directory found at $WORKSPACE_DIR — nothing to back up."
    exit 1
  fi

  mkdir -p "$BACKUP_DIR"
  local ts backup_file
  ts="$(date +%Y%m%d_%H%M%S)"
  backup_file="$BACKUP_DIR/workspace-backup-${ts}.tar.gz"

  echo "Backing up $WORKSPACE_DIR"
  echo "       ->   $backup_file"
  tar -czf "$backup_file" -C "$REPO_DIR" workspace

  # Verify the archive is real, not just that tar exited 0 — the same
  # "confirm the actual bytes, don't trust the command succeeded"
  # discipline used everywhere else tonight.
  local file_count
  file_count="$(tar -tzf "$backup_file" | wc -l | tr -d ' ')"
  echo
  echo "Backup contains $file_count entries."
  echo
  echo "Key files:"
  local any_found=0
  local pattern matches
  for pattern in "${KEY_PATTERNS[@]}"; do
    matches="$(tar -tzf "$backup_file" | grep -c "$pattern" || true)"
    if [[ "$matches" -gt 0 ]]; then
      echo "  OK:      $pattern ($matches entries)"
      any_found=1
    else
      echo "  ABSENT:  $pattern (not in backup — may genuinely not exist yet)"
    fi
  done

  if [[ "$any_found" -eq 0 ]]; then
    echo
    echo "WARNING: none of the expected key files were found. Either the"
    echo "workspace is already empty/fresh, or something is wrong with"
    echo "this backup — don't trust it as a real checkpoint without"
    echo "checking why."
  fi

  echo
  echo "Backup complete: $backup_file"
}

cmd_list() {
  if [[ ! -d "$BACKUP_DIR" ]] || [[ -z "$(ls -A "$BACKUP_DIR" 2>/dev/null)" ]]; then
    echo "No backups found in $BACKUP_DIR"
    return 0
  fi
  echo "Backups in $BACKUP_DIR:"
  ls -la "$BACKUP_DIR"/workspace-backup-*.tar.gz 2>/dev/null
}

cmd_restore() {
  local backup_file="${1:-}"

  if [[ -z "$backup_file" ]]; then
    echo "Usage: $0 restore <backup-file>"
    echo
    cmd_list
    exit 1
  fi

  # Allow either a full path or just the filename inside BACKUP_DIR.
  if [[ ! -f "$backup_file" ]] && [[ -f "$BACKUP_DIR/$backup_file" ]]; then
    backup_file="$BACKUP_DIR/$backup_file"
  fi
  if [[ ! -f "$backup_file" ]]; then
    echo "ERROR: backup file not found: $backup_file"
    echo
    cmd_list
    exit 1
  fi

  echo "This will DELETE the current workspace at:"
  echo "    $WORKSPACE_DIR"
  echo "and replace it with the contents of:"
  echo "    $backup_file"
  echo
  read -r -p "Type 'yes' to proceed: " confirm
  if [[ "$confirm" != "yes" ]]; then
    echo "Aborted — nothing was changed."
    exit 1
  fi

  # Restoring out from under a live session risks exactly the kind of
  # corruption state_lock() was built to prevent between two processes
  # — this is a third, blunter way to race the same file. Warn, don't
  # silently proceed.
  if pgrep -f "hermes -p .* -z" > /dev/null 2>&1; then
    echo
    echo "WARNING: a 'hermes -z' process is currently running. If the"
    echo "watchdog is also still running, it will relaunch another one"
    echo "immediately after this — stop the watchdog itself first, not"
    echo "just the current attempt, or this restore may be racing a new"
    echo "attempt already writing to the workspace you're about to wipe."
    read -r -p "Proceed anyway? Type 'yes' to continue: " confirm2
    if [[ "$confirm2" != "yes" ]]; then
      echo "Aborted — nothing was changed."
      exit 1
    fi
  fi

  rm -rf "$WORKSPACE_DIR"
  tar -xzf "$backup_file" -C "$REPO_DIR"

  echo
  echo "Restored. Verifying..."
  if [[ -f "$WORKSPACE_DIR/.ctf_traversal_state.json" ]]; then
    python3 -c "
import json
with open('$WORKSPACE_DIR/.ctf_traversal_state.json') as f:
    state = json.load(f)
summary = {}
for info in state.get('problems', {}).values():
    s = info.get('status', 'unknown')
    summary[s] = summary.get(s, 0) + 1
print('Restored traversal state summary:', summary)
"
  else
    echo "No .ctf_traversal_state.json in the restored workspace — this"
    echo "backup was likely taken from an empty or not-yet-initialized sweep."
  fi
}

case "${1:-}" in
  backup)
    cmd_backup
    ;;
  list)
    cmd_list
    ;;
  restore)
    cmd_restore "${2:-}"
    ;;
  *)
    echo "Usage: $0 {backup|list|restore <backup-file>}"
    exit 1
    ;;
esac
