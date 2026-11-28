#!/usr/bin/env python3
"""
range-status-report.py — a human-facing statistics report over the
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
    python3 range-status-report.py
    python3 range-status-report.py --workspace /path/to/workspace
    python3 range-status-report.py --json

See README_range-status-report.md for how to read each section — in
particular the "Vulnerability funnel", which reconstructs how many
findings were PROVEN exploitable and how far each got through
remediation, from the per-finding statuses.
"""
import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

# This script lives alongside the other operator-facing tools
# (scripts/), not inside the skill the agent itself uses
# (skills/security/range-assess/scripts/) - but it still needs
# range_poc.py's shared constant from there, so point sys.path at the
# skill's own scripts dir explicitly rather than at this file's own
# directory.
_RANGE_ASSESS_SCRIPTS = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "skills", "security", "range-assess", "scripts",
)
sys.path.insert(0, _RANGE_ASSESS_SCRIPTS)
from range_poc import MAX_ATTEMPTS_PER_FINDING  # noqa: E402  (shared constant, not duplicated)

JST = timezone(timedelta(hours=9))


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


# range-sweep-watchdog.sh stamps each attempt with the shell's own locale
# `date` output, e.g. (observed on this Mac, ja_JP locale):
#   --- Attempt #1 (init): launching 'hermes -p qwen-experiment -z ...': 2026年 11月23日 月曜日 11時17分53秒 JST ---
# Parsed as plain digit groups rather than strptime, since matching the
# kanji weekday/era text with %A would require the parsing side to also
# run under a ja_JP locale - digits alone are locale-independent. The
# weekday text itself (月曜日 etc.) is skipped with .*? and never used.
ATTEMPT_HEADER_RE = re.compile(
    r"--- Attempt #(\d+) \((init|resume)\): launching .*?: "
    r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日.*?"
    r"(\d{1,2})時\s*(\d{1,2})分\s*(\d{1,2})秒"
)


def find_sweep_start(workspace: str):
    """Return (started_at: datetime, mode: str, warning: str|None) for the
    CURRENT sweep, anchored to the LAST '(init)' attempt in the watchdog's
    own output log - a workspace that's been archived/cleared and
    re-initialized has its clock reset there, same as the real sweep.
    Falls back to the earliest attempt of any kind if no '(init)' line is
    found (e.g. the log only covers a resumed run). Returns (None, None,
    warning) if the log is missing or has no parseable attempt lines.
    """
    log_path = os.path.join(workspace, ".range-sweep-watchdog-output.log")
    if not os.path.exists(log_path):
        return None, None, f"{log_path} not found — cannot determine sweep start time"
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as e:
        return None, None, f"{log_path} exists but couldn't be read: {e}"

    last_init = None
    earliest_any = None
    for m in ATTEMPT_HEADER_RE.finditer(text):
        mode = m.group(2)
        y, mo, d, h, mi, s = (int(g) for g in m.groups()[2:])
        ts = datetime(y, mo, d, h, mi, s, tzinfo=JST)
        if earliest_any is None or ts < earliest_any:
            earliest_any = ts
        if mode == "init":
            if last_init is None or ts > last_init:
                last_init = ts

    if last_init is not None:
        return last_init, "init", None
    if earliest_any is not None:
        return earliest_any, "resume (no init line found)", None
    return None, None, f"no '--- Attempt #N (init|resume): ...' lines found in {log_path}"


# The watchdog stamps each attempt's end the same locale-`date` way it stamps
# the launch, e.g. "--- Attempt #7 exited (code 0): 2026年 11月26日 ... 07時...秒 JST ---".
EXIT_HEADER_RE = re.compile(
    r"--- Attempt #(\d+) exited \(code (-?\d+)\): "
    r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日.*?"
    r"(\d{1,2})時\s*(\d{1,2})分\s*(\d{1,2})秒"
)
# What the watchdog prints ONLY on a confirmed graceful completion (is-done
# JSON teed to the log, plus its own sentence). Any of these after the sweep's
# (init) anchor means the sweep ended on its own rather than still running.
COMPLETE_MARKERS = ('"done": true', '"done_pending_review": true',
                    "Sweep is genuinely complete")


def find_sweep_end(workspace: str, start_ts):
    """If the watchdog log shows this sweep GRACEFULLY completed, return
    (ended_at, basis, None) — the final attempt-exit timestamp — so runtime
    is measured to the real end instead of to now(). Returns (None, None,
    None) when no completion marker is found after the sweep's start (still
    running, or interrupted): the caller then falls back to now().

    Only the slice of the log from the current sweep's (init) anchor onward is
    considered, so a stale completion marker from an earlier, since-cleared run
    in the same log can't falsely end this one.
    """
    log_path = os.path.join(workspace, ".range-sweep-watchdog-output.log")
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None, None, None

    init_matches = [m for m in ATTEMPT_HEADER_RE.finditer(text) if m.group(2) == "init"]
    tail = text[init_matches[-1].start():] if init_matches else text
    if not any(mark in tail for mark in COMPLETE_MARKERS):
        return None, None, None

    last_exit = None
    for m in EXIT_HEADER_RE.finditer(tail):
        y, mo, d, h, mi, s = (int(g) for g in m.groups()[2:])
        ts = datetime(y, mo, d, h, mi, s, tzinfo=JST)
        if start_ts is not None and ts < start_ts:
            continue
        if last_exit is None or ts > last_exit:
            last_exit = ts
    if last_exit is None:
        return None, None, None
    return last_exit, "watchdog graceful completion", None


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

    sweep_started_at, sweep_start_mode, warn = find_sweep_start(workspace)
    if warn:
        warnings.append(warn)
    sweep_runtime = None
    if sweep_started_at is not None:
        ended_at, ended_basis, _ = find_sweep_end(workspace, sweep_started_at)
        # Measure to the real end once the sweep has gracefully exited;
        # otherwise (still running / interrupted) measure to now, as before.
        endpoint = ended_at if ended_at is not None else datetime.now(tz=JST)
        elapsed = (endpoint - sweep_started_at).total_seconds()
        sweep_runtime = {
            "started_at": sweep_started_at.isoformat(),
            "started_mode": sweep_start_mode,
            "ended_at": ended_at.isoformat() if ended_at is not None else None,
            "ended_basis": ended_basis,
            "running": ended_at is None,
            "elapsed_seconds": elapsed,
            "elapsed_human": fmt_duration(elapsed),
        }

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

    # ---- vulnerability funnel (derived from the per-finding statuses) ----
    # A finding's CURRENT status implies the path it took: a finding at
    # `reverified_closed` necessarily passed through poc_confirmed ->
    # remediation_applied -> reverified_closed, so the current-status counts
    # alone let us reconstruct "how many were PROVEN exploitable, and how
    # far each got through remediation" without a separate history.
    #
    #   PROVEN exploitable  = ever reached poc_confirmed. Every status below
    #       is downstream of poc_confirmed (risk_accepted/closed_manually
    #       only come via resolve-review, which only acts on an
    #       already-proven, stuck finding).
    #   A fix is ON THE BOX for remediation_applied and anything past it
    #       (reverified_closed / reverify_failed / reverify_inconclusive).
    #       remediation_planned means a remediation was attempted but came
    #       back applied:false — NOT yet fixed. poc_confirmed = no fix yet.
    #   NEEDS ATTENTION = still genuinely open: no fix (poc_confirmed),
    #       fix wouldn't apply (remediation_planned), or fix applied but
    #       the finding is STILL exploitable on reverify (reverify_failed).
    PROVEN_STATUSES = [
        "poc_confirmed", "remediation_planned", "remediation_applied",
        "reverified_closed", "reverify_failed", "reverify_inconclusive",
        "risk_accepted", "closed_manually",
    ]
    FIX_APPLIED_STATUSES = {
        "remediation_applied", "reverified_closed",
        "reverify_failed", "reverify_inconclusive",
    }
    NEEDS_ATTENTION_STATUSES = {"poc_confirmed", "remediation_planned", "reverify_failed"}
    sc = status_counts
    proven_total = sum(sc.get(s, 0) for s in PROVEN_STATUSES)
    # Second axis — needs_operator_decision over PROVEN findings, crossed with
    # "is the fix proven effective?" (reverified_closed). This is the operator's
    # three-tier deployment-decision view (axis 1 = is there a proven fix,
    # axis 2 = does deploying it need a conscious human call).
    decision = {"proven_flagged": 0, "proven_unflagged": 0,
                "unproven_flagged": 0, "unproven_unflagged": 0}
    for _, _, _, finding in all_findings:
        st = finding.get("status")
        if st not in PROVEN_STATUSES:
            continue
        flagged = bool(finding.get("needs_operator_decision"))
        proven_fix = st == "reverified_closed"
        decision[("proven" if proven_fix else "unproven")
                 + ("_flagged" if flagged else "_unflagged")] += 1
    vulnerabilities = {
        "proven_total": proven_total,
        "proven_by_status": {s: sc.get(s, 0) for s in PROVEN_STATUSES},
        "by_decision": decision,
        "fix_applied": sum(sc.get(s, 0) for s in FIX_APPLIED_STATUSES),
        "verified_closed": sc.get("reverified_closed", 0),
        "needs_attention": sum(sc.get(s, 0) for s in NEEDS_ATTENTION_STATUSES),
        # Context — NOT vulnerabilities, shown so the funnel reconciles
        # against the total finding count.
        "context": {
            "poc_not_reachable": sc.get("poc_not_reachable", 0),
            "poc_inconclusive": sc.get("poc_inconclusive", 0),
            "discovered": sc.get("discovered", 0),
            "informational": sc.get("informational", 0),
        },
    }

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
        # Displayed in JST, not UTC — these timestamps are read by an
        # operator, not fed back into any script, so there's no
        # machine-readability reason to keep them in UTC.
        timing = {
            "earliest": datetime.fromtimestamp(earliest, tz=JST).isoformat(),
            "latest": datetime.fromtimestamp(latest, tz=JST).isoformat(),
            "span_seconds": latest - earliest,
            "span_human": fmt_duration(latest - earliest),
            "note": (
                "covers PoC/remediation attempts only - NOT the sweep's "
                "total wall-clock runtime. No sweep-start timestamp is "
                "recorded anywhere this script reads."
            ),
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
        "sweep_runtime": sweep_runtime,
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
        "vulnerabilities": vulnerabilities,
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

    sr = report["sweep_runtime"]
    print("\n-- Sweep runtime (JST) --")
    if sr:
        line("Started", f"{sr['started_at']}  ({sr['started_mode']})")
        if sr.get("running"):
            line("Elapsed", f"{sr['elapsed_human']}  (running — measured to report time)")
        else:
            line("Ended", f"{sr['ended_at']}  ({sr['ended_basis']})")
            line("Elapsed", f"{sr['elapsed_human']}  (final)")
    else:
        line("Elapsed", "(unknown - see warnings above)")

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

    v = report["vulnerabilities"]
    pb = v["proven_by_status"]
    print("\n-- Vulnerability funnel --")
    print("   (PROVEN exploitable findings only — informational inventory and")
    print("    not-reachable findings are NOT vulnerabilities; see README)")
    print("   Note: 'closed'/'applied' below means on the disposable clone, NOT on any")
    print("   live host — each is a proven, ready-to-deploy fix; deploying it is the")
    print("   operator's decision.")
    line("Proven vulnerabilities", v["proven_total"])
    print(f"      {'reverified_closed':<30} {pb['reverified_closed']:>4}   fix proven effective on clone (re-verified)")
    print(f"      {'remediation_applied':<30} {pb['remediation_applied']:>4}   fix applied on clone, re-verify not yet run")
    print(f"      {'reverify_inconclusive':<30} {pb['reverify_inconclusive']:>4}   fix applied on clone, re-verify inconclusive")
    print(f"      {'reverify_failed':<30} {pb['reverify_failed']:>4}   fix tried on clone but did NOT close it")
    print(f"      {'remediation_planned':<30} {pb['remediation_planned']:>4}   remediation proposed, not proven")
    print(f"      {'poc_confirmed':<30} {pb['poc_confirmed']:>4}   proven, no remediation proposed yet")
    print(f"      {'closed_manually':<30} {pb['closed_manually']:>4}   operator closed by hand")
    print(f"      {'risk_accepted':<30} {pb['risk_accepted']:>4}   operator accepted the risk")
    total = v["proven_total"]
    pct = lambda n: f"{n}/{total}" + (f" ({round(100*n/total)}%)" if total else "")
    print("   Remediation coverage of proven vulns:")
    line("  Fix tested on clone", pct(v["fix_applied"]))
    line("  Fix proven effective", pct(v["verified_closed"]))
    line("  Needs attention", f"{v['needs_attention']}   (poc_confirmed / remediation_planned / reverify_failed)")
    bd = v["by_decision"]
    print("   Deployment decision — 2nd axis (⚑ = applying the fix is the operator's call):")
    print(f"      ⚑ proven fix, operator-decision     {bd['proven_flagged']:>4}   reverified_closed + flagged")
    print(f"      ⚑ no proven fix yet, operator-dec.  {bd['unproven_flagged']:>4}   proposed/other + flagged")
    print(f"        proven fix, AI-judged routine     {bd['proven_unflagged']:>4}   reverified_closed, not flagged")
    if bd["unproven_unflagged"]:
        print(f"        no proven fix, not flagged        {bd['unproven_unflagged']:>4}   fix attempt failed")
    c = v["context"]
    print("   For context (NOT vulnerabilities):")
    print(f"      {'poc_not_reachable':<30} {c['poc_not_reachable']:>4}   tested, not exploitable")
    print(f"      {'poc_inconclusive':<30} {c['poc_inconclusive']:>4}   tested, couldn't determine")
    print(f"      {'discovered':<30} {c['discovered']:>4}   risk finding, PoC still pending")
    print(f"      {'informational':<30} {c['informational']:>4}   inventory (never a vuln claim)")

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

    # NOTE: this section only covers PoC/remediation ATTEMPTS, which start
    # well after a finding is discovered (triage/clone/scan time isn't
    # logged anywhere this script reads). It is NOT the sweep's total
    # wall-clock runtime - a sweep that started hours before the first
    # logged attempt will show a much shorter span here than it's
    # actually been running. There is no "sweep start" timestamp recorded
    # in .range_traversal_state.json to report a true total against.
    print("\n-- PoC / Remediation Attempt Timing (JST) --")
    print("   (attempts only - NOT total sweep runtime; see note above)")
    t = report["timing"]
    if t:
        line("Earliest logged attempt", t["earliest"])
        line("Latest logged attempt", t["latest"])
        line("Attempt-logging span", t["span_human"])
    else:
        line("Attempt-logging span", "(no attempts logged yet)")

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
