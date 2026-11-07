#!/usr/bin/env python3
"""
range_status_report.py — a human-facing statistics report over the
range-assess sweep's actual on-disk state in /workspace. Unlike every
other script in this skill, it defaults to a readable text report, not
JSON — its audience is the operator reading it directly, not the agent
loop. Pass --json for a machine-readable version of the same numbers.

Reads, read-only, never writes anything:
    <workspace>/.range_traversal_state.json   — authoritative: hosts,
        findings, their status, scope, category, reset history. This
        IS what `range_traversal.py status` already summarizes
        (hosts_by_*_status, findings_by_status) — this script starts
        from the same state but goes further: attempt-log stats,
        timing, and a cross-check against the result files actually
        on disk, none of which `status` looks at.
    <workspace>/.range_poc_log.json           — every PoC attempt,
        keyed "working_vmid:finding_id" (range_poc.py's own log).
    <workspace>/.range_remediate_log.json     — every remediation
        attempt, same key shape (range_remediate.py's own log).
    <workspace>/result_*.json,
    <workspace>/remediation_result_*.json      — the canonical
        per-finding result files range_poc.py/range_remediate.py's
        `report` subcommand writes (see this skill's README for why
        that naming exists — it didn't, before a real gap found in
        production: result files were coming out named after the
        GOLDEN vmid instead of the working one, which doesn't match
        this key scheme at all). Missing or golden-vmid-named files are
        reported as a data-integrity finding in their own right, not
        silently ignored.

Any of the three JSON inputs being absent or unparsable is reported as
a warning (empty data assumed), not a hard failure, EXCEPT the state
file: with no state file there's nothing to report on, so that's fatal.

Usage:
    python3 range_status_report.py
    python3 range_status_report.py --workspace /path/to/workspace
    python3 range_status_report.py --json
"""
import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from range_poc import MAX_ATTEMPTS_PER_FINDING  # noqa: E402  (shared constant, not duplicated)


def load_json(path: str, default):
    if not os.path.exists(path):
        return default, f"{path} not found — treating as empty"
    try:
        with open(path) as f:
            return json.load(f), None
    except (json.JSONDecodeError, OSError) as e:
        return default, f"{path} exists but couldn't be read/parsed: {e} — treating as empty"


def fmt_duration(seconds: float) -> str:
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def build_report(workspace: str) -> dict:
    warnings = []

    state_path = os.path.join(workspace, ".range_traversal_state.json")
    state, warn = load_json(state_path, None)
    if state is None:
        print(f"FATAL: {warn or state_path + ' not found'} — nothing to report on.", file=sys.stderr)
        sys.exit(1)

    poc_log, warn = load_json(os.path.join(workspace, ".range_poc_log.json"), {})
    if warn:
        warnings.append(warn)
    remediate_log, warn = load_json(os.path.join(workspace, ".range_remediate_log.json"), {})
    if warn:
        warnings.append(warn)

    # ---- hosts ----
    hosts = state.get("hosts", {})
    os_counts = {}
    network_scan_counts = {}
    local_audit_counts = {}
    hosts_with_resets = 0
    total_resets = 0
    for rec in hosts.values():
        os_counts[rec.get("os", "unknown")] = os_counts.get(rec.get("os", "unknown"), 0) + 1
        network_scan_counts[rec["network_scan_status"]] = network_scan_counts.get(rec["network_scan_status"], 0) + 1
        local_audit_counts[rec["local_audit_status"]] = local_audit_counts.get(rec["local_audit_status"], 0) + 1
        resets = len(rec.get("reset_history", []))
        if resets:
            hosts_with_resets += 1
            total_resets += resets

    # ---- findings ----
    all_findings = []  # (vmid, working_vmid, golden_vmid, fid, finding dict)
    for vmid, rec in hosts.items():
        for fid, finding in rec.get("findings", {}).items():
            all_findings.append((vmid, rec.get("working_vmid"), fid, finding))

    scope_counts = {}
    status_counts = {}
    category_counts = {}
    for _, _, _, finding in all_findings:
        scope_counts[finding.get("scope", "unknown")] = scope_counts.get(finding.get("scope", "unknown"), 0) + 1
        status_counts[finding.get("status", "unknown")] = status_counts.get(finding.get("status", "unknown"), 0) + 1
        category_counts[finding.get("category", "unknown")] = category_counts.get(finding.get("category", "unknown"), 0) + 1

    # ---- PoC attempt log ----
    poc_attempt_counts = {key: len(entries) for key, entries in poc_log.items()}
    poc_total_attempts = sum(poc_attempt_counts.values())
    poc_at_cap = [key for key, n in poc_attempt_counts.items() if n >= MAX_ATTEMPTS_PER_FINDING]
    poc_avg_attempts = (poc_total_attempts / len(poc_attempt_counts)) if poc_attempt_counts else 0.0

    # ---- remediation attempt log ----
    remediate_total_attempts = sum(len(entries) for entries in remediate_log.values())
    remediate_applied = 0
    remediate_dry_run_only = 0
    remediate_needs_decision = 0
    for entries in remediate_log.values():
        last = entries[-1]["result"] if entries else {}
        if last.get("applied"):
            remediate_applied += 1
        elif entries and entries[-1].get("dry_run"):
            remediate_dry_run_only += 1
        if last.get("needs_operator_decision"):
            remediate_needs_decision += 1

    # ---- timing, across both logs ----
    all_timestamps = []
    for entries in poc_log.values():
        all_timestamps += [e["timestamp"] for e in entries if "timestamp" in e]
    for entries in remediate_log.values():
        all_timestamps += [e["timestamp"] for e in entries if "timestamp" in e]
    timing = None
    if all_timestamps:
        earliest, latest = min(all_timestamps), max(all_timestamps)
        timing = {
            "earliest": datetime.fromtimestamp(earliest, tz=timezone.utc).isoformat(),
            "latest": datetime.fromtimestamp(latest, tz=timezone.utc).isoformat(),
            "span_seconds": latest - earliest,
            "span_human": fmt_duration(latest - earliest),
        }

    # ---- data integrity: canonical result files actually on disk ----
    POC_DONE_STATUSES = {
        "poc_confirmed", "poc_not_reachable", "poc_inconclusive",
        "remediation_planned", "remediation_applied",
        "reverified_closed", "reverify_failed", "reverify_inconclusive",
    }
    REMEDIATE_DONE_STATUSES = {
        "remediation_planned", "remediation_applied",
        "reverified_closed", "reverify_failed", "reverify_inconclusive",
    }
    missing_result_files = []
    missing_remediation_files = []
    for vmid, working_vmid, fid, finding in all_findings:
        status = finding.get("status")
        if working_vmid and status in POC_DONE_STATUSES:
            if not os.path.exists(os.path.join(workspace, f"result_{working_vmid}_{fid}.json")):
                missing_result_files.append(f"{working_vmid}:{fid}")
        if working_vmid and status in REMEDIATE_DONE_STATUSES:
            if not os.path.exists(os.path.join(workspace, f"remediation_result_{working_vmid}_{fid}.json")):
                missing_remediation_files.append(f"{working_vmid}:{fid}")

    # Stale, pre-naming-fix files: result_<N>_<fid>.json / remediation_result_<N>_<fid>.json
    # on disk where <N> is NOT any host's current working_vmid — i.e. almost
    # certainly a golden vmid, from before this skill's own naming-convention
    # fix (see README's "fifth real gap"). Not a correctness problem by
    # itself (nothing reads these), but worth flagging so they don't get
    # mistaken for a second, contradictory result on the same finding.
    working_vmids = {str(rec.get("working_vmid")) for rec in hosts.values() if rec.get("working_vmid")}
    stale_pattern = re.compile(r"^(remediation_result|result)_([^_]+)_(f-[0-9a-f]+)\.json$")
    stale_files = []
    for path in glob.glob(os.path.join(workspace, "result_*.json")) + \
                glob.glob(os.path.join(workspace, "remediation_result_*.json")):
        m = stale_pattern.match(os.path.basename(path))
        if m and m.group(2) not in working_vmids:
            stale_files.append(os.path.basename(path))

    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "workspace": workspace,
        "warnings": warnings,
        "hosts": {
            "total": len(hosts),
            "by_os": os_counts,
            "by_network_scan_status": network_scan_counts,
            "by_local_audit_status": local_audit_counts,
            "hosts_with_resets": hosts_with_resets,
            "total_resets": total_resets,
        },
        "findings": {
            "total": len(all_findings),
            "by_scope": scope_counts,
            "by_status": status_counts,
            "by_category": dict(sorted(category_counts.items(), key=lambda kv: -kv[1])),
        },
        "poc_attempts": {
            "findings_with_attempts": len(poc_attempt_counts),
            "total_attempts": poc_total_attempts,
            "average_attempts_per_finding": round(poc_avg_attempts, 2),
            "at_or_above_cap": sorted(poc_at_cap),
            "cap": MAX_ATTEMPTS_PER_FINDING,
        },
        "remediation_attempts": {
            "findings_with_attempts": len(remediate_log),
            "total_attempts": remediate_total_attempts,
            "applied": remediate_applied,
            "dry_run_only": remediate_dry_run_only,
            "needs_operator_decision": remediate_needs_decision,
        },
        "timing": timing,
        "data_integrity": {
            "findings_missing_result_file": sorted(missing_result_files),
            "findings_missing_remediation_result_file": sorted(missing_remediation_files),
            "stale_golden_vmid_named_files": sorted(stale_files),
        },
    }


def print_human_report(report: dict):
    def line(label, value):
        print(f"  {label:<40} {value}")

    print("=== Range Assessment Status Report ===")
    print(f"Generated: {report['generated_at']}")
    print(f"Workspace: {report['workspace']}")
    if report["warnings"]:
        print("\nWarnings:")
        for w in report["warnings"]:
            print(f"  - {w}")

    h = report["hosts"]
    print("\n-- Host coverage --")
    line("Total hosts", h["total"])
    line("By OS", ", ".join(f"{k}={v}" for k, v in sorted(h["by_os"].items())) or "(none)")
    line("Network scan status", ", ".join(f"{k}={v}" for k, v in sorted(h["by_network_scan_status"].items())) or "(none)")
    line("Local audit status", ", ".join(f"{k}={v}" for k, v in sorted(h["by_local_audit_status"].items())) or "(none)")
    line("Hosts reset at least once", f"{h['hosts_with_resets']} ({h['total_resets']} resets total)")

    f = report["findings"]
    print("\n-- Findings --")
    line("Total findings", f["total"])
    line("By scope", ", ".join(f"{k}={v}" for k, v in sorted(f["by_scope"].items())) or "(none)")
    print("  By status:")
    for k, v in sorted(f["by_status"].items(), key=lambda kv: -kv[1]):
        print(f"      {k:<24} {v}")
    print("  By category (top 10):")
    for k, v in list(f["by_category"].items())[:10]:
        print(f"      {k:<24} {v}")

    p = report["poc_attempts"]
    print("\n-- PoC attempts (.range_poc_log.json) --")
    line("Findings with >=1 attempt", p["findings_with_attempts"])
    line("Total attempts logged", p["total_attempts"])
    line("Average attempts/finding", p["average_attempts_per_finding"])
    line(f"At/above cap ({p['cap']})", ", ".join(p["at_or_above_cap"]) or "(none)")

    r = report["remediation_attempts"]
    print("\n-- Remediation attempts (.range_remediate_log.json) --")
    line("Findings with >=1 attempt", r["findings_with_attempts"])
    line("Total attempts logged", r["total_attempts"])
    line("Applied", r["applied"])
    line("Dry-run only (last attempt)", r["dry_run_only"])
    line("needs_operator_decision", r["needs_operator_decision"])

    print("\n-- Timing --")
    t = report["timing"]
    if t:
        line("Earliest logged attempt", t["earliest"])
        line("Latest logged attempt", t["latest"])
        line("Span", t["span_human"])
    else:
        line("Span", "(no attempts logged yet)")

    d = report["data_integrity"]
    print("\n-- Data integrity --")
    line("Findings missing result_*.json", ", ".join(d["findings_missing_result_file"]) or "(none)")
    line("Findings missing remediation_result_*.json", ", ".join(d["findings_missing_remediation_result_file"]) or "(none)")
    line("Stale golden-vmid-named files on disk", ", ".join(d["stale_golden_vmid_named_files"]) or "(none)")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", default="/workspace", help="directory holding the state/log/result files")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON instead of the text report")
    args = parser.parse_args()

    report = build_report(args.workspace)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_human_report(report)


if __name__ == "__main__":
    main()
