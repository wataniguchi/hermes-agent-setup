#!/usr/bin/env python3
"""
range_findings_report.py — a single, human-readable Markdown report
over every finding in range_traversal.py's state, with resolved file
references for everything an assessor/operator would otherwise have to
go hunting for by hand: the state record itself, the PoC/remediation
context files handed to the agent, the canonical result files, and any
auxiliary scripts the agent's own result JSON mentions having written
(e.g. "/workspace/loopback_docker_lpe.sh").

HONEST ABOUT WHAT THIS IS: read-only. It does not scan, enumerate,
exploit, or remediate anything, and it never writes into
.range_traversal_state.json or any *_log.json — only into the report
file itself. It is a reading aid over range_status_report.py's own
statistics, not a replacement for it.

File-reference resolution, same discipline range_status_report.py
already uses: prefer the canonical working-vmid-keyed filename; if
that's missing, fall back to a glob by finding id alone (catches a
result sitting under a stale golden-vmid name, the same gap
range_status_report.py's own integrity check flags) and say so
explicitly rather than silently presenting it as the canonical path.

Usage:
    python3 range_findings_report.py --workspace <path> [--output <path>] [--status <status>]

    --status, repeatable, limits the report to specific status values
    (e.g. --status remediation_planned --status reverify_failed) —
    useful for working through one bucket at a time, same shape as
    walking the five remediation_planned cases by hand.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

# Matches a path under /workspace ending in a common script/text
# extension, as it would appear embedded in free-text evidence or a
# commands_run/commands_or_diff entry — e.g.
# "bash script /workspace/loopback_docker_lpe.sh (run as ec2-user ...)"
SCRIPT_REF_RE = re.compile(r"/workspace/[A-Za-z0-9_./-]+\.(?:sh|py|ps1|sql|txt|md)")


def load_json(path):
    with open(path) as f:
        return json.load(f)


def finding_description(finding):
    if finding.get("scope") == "network":
        return (f"{finding.get('service') or 'unknown service'} "
                f"{finding.get('version') or ''} on port "
                f"{finding.get('port')}/{finding.get('proto')}").strip()
    if finding.get("item"):
        # Split finding from range_host_audit.py's per-category
        # splitters (one finding per sudoers line, per listening
        # socket, etc.) — the item text is the specific thing, more
        # useful here than the category/command shared by every sibling.
        return f"{finding.get('category')}: {finding['item'][:200]}"
    return finding.get("description") or f"{finding.get('category')}: {finding.get('command')}"


def resolve_file(workspace, prefix, working_vmid, fid):
    """Returns (display_path_or_None, note) for a canonical
    '<prefix>_<working_vmid>_<fid>.json'-shaped file, falling back to a
    glob by finding id alone if the canonical name isn't there."""
    canonical = os.path.join(workspace, f"{prefix}_{working_vmid}_{fid}.json")
    if working_vmid and os.path.isfile(canonical):
        return canonical, None

    import glob
    candidates = sorted(glob.glob(os.path.join(workspace, f"{prefix}_*_{fid}.json")))
    if candidates:
        return candidates[0], (
            f"NOT under the current working_vmid ({working_vmid}) — "
            f"found under a stale name instead; cross-check before trusting "
            f"its contents against the CURRENT clone."
        )
    return None, "MISSING"


def resolve_context(workspace, fid, kind):
    path = os.path.join(workspace, f".range_context_{fid}_{kind}.json")
    return path if os.path.isfile(path) else None


def resolve_sandbox_path(workspace, path):
    """A referenced path like '/workspace/loopback_docker_lpe.sh' is
    written from inside the Docker sandbox, where /workspace is the
    container-side mount of this script's --workspace directory. This
    report may be run from the host (e.g. the Mac Studio) instead,
    where that literal path doesn't exist — translate it to the real
    --workspace directory before checking existence, same as every
    other file this script resolves. Anything not under /workspace is
    left as-is (can't know its mount translation, if any)."""
    prefix = "/workspace/"
    if path.startswith(prefix):
        return os.path.join(workspace, path[len(prefix):])
    return path


def extract_script_refs(*texts):
    found = set()
    for t in texts:
        if not t:
            continue
        found.update(SCRIPT_REF_RE.findall(json.dumps(t) if not isinstance(t, str) else t))
    return sorted(found)


def render_finding(workspace, vmid, rec, fid, finding, out):
    working_vmid = rec.get("working_vmid")
    status = finding["status"]
    out.append(f"\n### `{fid}` — {finding.get('scope')} — status: `{status}`\n")
    out.append(f"- **Description:** {finding_description(finding)}")
    if finding.get("poc_method"):
        out.append(f"- **PoC method:** {finding['poc_method']}")
    if finding.get("poc_evidence"):
        out.append(f"- **PoC evidence:** {finding['poc_evidence']}")
    if finding.get("remediation_summary"):
        out.append(f"- **Remediation summary:** {finding['remediation_summary']}")
    if finding.get("remediation_attempts"):
        out.append(f"- **Remediation attempts:** {finding['remediation_attempts']}")
    if finding.get("needs_operator_decision"):
        out.append("- **Needs operator decision:** yes — see `resolve-review`")
    if finding.get("risk_notes"):
        out.append(f"- **Risk notes:** {finding['risk_notes']}")
    if finding.get("operator_note"):
        out.append(f"- **Operator note (from resolve-review):** {finding['operator_note']}")

    out.append("\n**Files:**\n")

    poc_ctx = resolve_context(workspace, fid, "poc")
    out.append(f"- PoC context: `{poc_ctx}`" if poc_ctx else "- PoC context: *not found*")

    rem_ctx = resolve_context(workspace, fid, "remediate")
    out.append(f"- Remediation context: `{rem_ctx}`" if rem_ctx else "- Remediation context: *not found (not yet remediated)*")

    poc_path, poc_note = resolve_file(workspace, "result", working_vmid, fid)
    if poc_path:
        suffix = f"  ⚠️ {poc_note}" if poc_note else ""
        out.append(f"- PoC result: `{poc_path}`{suffix}")
    else:
        out.append("- PoC result: **MISSING**")

    rem_path, rem_note = resolve_file(workspace, "remediation_result", working_vmid, fid)
    if status in ("remediation_planned", "remediation_applied", "reverify_failed", "reverified_closed"):
        if rem_path:
            suffix = f"  ⚠️ {rem_note}" if rem_note else ""
            out.append(f"- Remediation result: `{rem_path}`{suffix}")
        else:
            out.append("- Remediation result: **MISSING**")

    # Auxiliary scripts the agent's own result JSON says it wrote —
    # pull referenced paths out of whichever result files we found,
    # plus the state record's own short-form text fields.
    texts = [finding.get("poc_method", ""), finding.get("poc_evidence", "")]
    for p in (poc_path, rem_path):
        if p:
            try:
                texts.append(json.dumps(load_json(p)))
            except (OSError, json.JSONDecodeError):
                pass
    refs = extract_script_refs(*texts)
    if refs:
        out.append("- Referenced scripts/files mentioned in evidence:")
        for r in refs:
            resolved = resolve_sandbox_path(workspace, r)
            exists = "✓" if os.path.isfile(resolved) else "MISSING on disk"
            note = f" (resolved as `{resolved}`)" if resolved != r else ""
            out.append(f"  - `{r}`{note} — {exists}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--output", default=None)
    ap.add_argument("--status", action="append", default=None,
                     help="Limit to one or more status values; repeatable. Default: all.")
    args = ap.parse_args()

    workspace = os.path.abspath(args.workspace)
    state_path = os.path.join(workspace, ".range_traversal_state.json")
    if not os.path.isfile(state_path):
        print(f"ERROR: no state file at {state_path}", file=sys.stderr)
        sys.exit(1)
    state = load_json(state_path)

    out = []
    out.append("# Range Assessment Findings Report")
    out.append(f"\nGenerated: {datetime.now(timezone.utc).astimezone().isoformat()}")
    out.append(f"\nWorkspace: `{workspace}`")
    if args.status:
        out.append(f"\nFiltered to status: {', '.join(args.status)}")

    total = 0
    for vmid, rec in state["hosts"].items():
        host_findings = [
            (fid, f) for fid, f in rec["findings"].items()
            if not args.status or f["status"] in args.status
        ]
        if not host_findings:
            continue
        out.append(f"\n\n## Host: {rec.get('host')} "
                   f"(golden vmid {vmid}, working vmid {rec.get('working_vmid')})\n")
        out.append(f"- Network scan: `{rec.get('network_scan_status')}` · "
                    f"Local audit: `{rec.get('local_audit_status')}`")
        for fid, finding in host_findings:
            render_finding(workspace, vmid, rec, fid, finding, out)
            total += 1

    out.append(f"\n\n---\n{total} finding(s) in this report.")

    report = "\n".join(out) + "\n"
    output_path = args.output or os.path.join(workspace, "findings_report.md")
    with open(output_path, "w") as f:
        f.write(report)
    print(f"Wrote {total} finding(s) to {output_path}")


if __name__ == "__main__":
    main()
