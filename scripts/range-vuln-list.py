#!/usr/bin/env python3
"""
range-vuln-list.py — list every PROVEN vulnerability in a range-assess
sweep, grouped by its current status (the eight proven statuses).

A "proven vulnerability" is a finding that was demonstrated exploitable by
a successful PoC — i.e. it reached `poc_confirmed` and is now at one of the
eight statuses downstream of it. `informational` inventory, `discovered`
(not yet PoC'd) and `poc_not_reachable`/`poc_inconclusive` (tested, not
exploitable) are NOT proven and are not listed here — see
range-status-report.py's vulnerability funnel for those.

Read-only. Standard library only.

Usage:
    python3 range-vuln-list.py --workspace ~/hermes-agent-setup/workspace
    python3 range-vuln-list.py --workspace <ws> --status poc_confirmed
    python3 range-vuln-list.py --workspace <ws> --json

Each row ends with the exact command to see that finding in full:
    range-vuln-show.py --vmid <golden_vmid> <finding_id>
"""
import argparse
import json
import os
import sys

# The eight statuses a finding can hold once it has been PROVEN exploitable,
# in lifecycle order. Keep in sync with range_traversal.py's state machine
# and range-status-report.py's PROVEN_STATUSES.
PROVEN_STATUSES = [
    "poc_confirmed",          # proven, no remediation yet
    "remediation_planned",    # remediation attempted, came back applied:false
    "remediation_applied",    # fix applied, reverify not yet run
    "reverified_closed",      # fixed and re-verified as closed
    "reverify_failed",        # fix applied but STILL exploitable
    "reverify_inconclusive",  # fixed, reverify couldn't confirm
    "risk_accepted",          # operator accepted the risk (resolve-review)
    "closed_manually",        # operator fixed by hand (resolve-review)
]

STATUS_BLURB = {
    "poc_confirmed": "proven exploitable, no remediation proposed yet",
    "remediation_planned": "remediation proposed, not proven (untested / couldn't apply)",
    "remediation_applied": "fix applied on clone, re-verify not yet run",
    "reverified_closed": "fix proven effective on clone (re-verified)",
    "reverify_failed": "fix tried on clone but did NOT close it",
    "reverify_inconclusive": "fix applied on clone, re-verify inconclusive",
    "risk_accepted": "operator accepted the risk",
    "closed_manually": "operator closed by hand",
}


def finding_short_desc(finding: dict, width: int = 70) -> str:
    if finding.get("scope") == "network":
        s = f"{finding.get('service') or 'unknown service'} {finding.get('version') or ''}".strip()
        return f"{s} on port {finding.get('port')}/{finding.get('proto')}"
    if finding.get("item"):
        return f"{finding.get('category')}: {finding['item'][:width]}"
    return finding.get("description") or f"{finding.get('category')}: {finding.get('command')}"


def collect(state: dict):
    rows = []
    for gvmid, rec in state.get("hosts", {}).items():
        for fid, finding in rec.get("findings", {}).items():
            st = finding.get("status")
            if st in PROVEN_STATUSES:
                rows.append({
                    "status": st,
                    "golden_vmid": gvmid,
                    "host": rec.get("host", "?"),
                    "working_vmid": rec.get("working_vmid"),
                    "finding_id": fid,
                    "category": finding.get("category") or finding.get("scope"),
                    "scope": finding.get("scope"),
                    "desc": finding_short_desc(finding),
                    "needs_operator_decision": bool(finding.get("needs_operator_decision")),
                })
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--workspace", default="/workspace")
    p.add_argument("--status", choices=PROVEN_STATUSES, help="show only this one status")
    p.add_argument("--needs-decision", action="store_true",
                   help="show only findings flagged needs_operator_decision "
                        "(deploying the proven fix is a conscious operator call)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    state_path = os.path.join(args.workspace, ".range_traversal_state.json")
    try:
        with open(state_path) as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"FATAL: cannot read {state_path}: {e}", file=sys.stderr)
        sys.exit(1)

    rows = collect(state)
    if args.status:
        rows = [r for r in rows if r["status"] == args.status]
    if args.needs_decision:
        rows = [r for r in rows if r["needs_operator_decision"]]

    if args.json:
        print(json.dumps(rows, indent=2))
        return

    wanted = [args.status] if args.status else PROVEN_STATUSES
    print("=== Proven vulnerabilities ===")
    print(f"Workspace: {args.workspace}")
    print(f"Total proven: {len(rows)}")
    print("Note: none of these have been applied beyond the disposable clone they were")
    print("proven on — each is a proven, ready-to-deploy fix; deploying it is the")
    print("operator's decision.  ⚑ = that deployment is a conscious operator call")
    print("(needs_operator_decision).")
    print("Summary by status:")
    for st in wanted:
        print(f"  {st:<24} {sum(1 for r in rows if r['status'] == st)}")

    for st in wanted:
        group = [r for r in rows if r["status"] == st]
        if not group:
            continue
        print(f"\n-- {st}  ({STATUS_BLURB[st]})  [{len(group)}] --")
        # golden vmid | host | category | finding id | short desc
        for r in sorted(group, key=lambda r: (r["golden_vmid"], r["finding_id"])):
            flag = "⚑ " if r["needs_operator_decision"] else "  "
            print(f"  {flag}{r['golden_vmid']}  {r['host']:<16} {str(r['category']):<20} "
                  f"{r['finding_id']}  {r['desc']}")
        print("  → inspect any of the above:")
        print(f"      python3 range-vuln-show.py --workspace <ws> "
              f"--vmid {group[0]['golden_vmid']} {group[0]['finding_id']}")


if __name__ == "__main__":
    main()
