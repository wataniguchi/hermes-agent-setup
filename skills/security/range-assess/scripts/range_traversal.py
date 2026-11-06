"""
range_traversal.py — orchestrator for the range assessment loop, built
on the same design as ctf_traversal.py: explicit params, a lock
spanning the whole load-modify-save span of every subcommand that
touches state, and bookkeeping-only logic that hands the real work out
to other scripts via subprocess — range_discover.py (network scan),
range_host_audit.py (local guest-exec enumeration), range_poc.py and
range_remediate.py (PoC/remediation guardrails) — the same run_script()
pattern ctf_traversal.py uses for ksnctf-discover/ctf-solver/
ksnctf-submit.

HONEST ABOUT WHAT THIS IS: this script does not scan, enumerate,
exploit, or remediate anything itself — it tracks state and tells you
(the agent driving this loop) what to do next. Critically, it also does
NOT call any model anywhere — `assign-poc`/`assign-remediate` hand you a
filled task prompt to act on with YOUR OWN tools (real network access
for a network-scope finding, real calls to proxmox_bridge.py's
guest-exec endpoint for a local-scope one); `report-poc`/
`report-remediate` validate and log the result you report back after
actually doing that work. See range_poc.py's own docstring for why this
two-phase shape replaced an earlier, mistaken design where a script
tried to call a tool-less model itself.

NEW, UNTESTED CODE — this whole skill is new. None of the state
transitions below have been exercised against a real run. Validate in
stages against ONE host and ONE low-risk finding before trusting this
across the range — see the skill's SKILL.md for a suggested validation
order.

Per-host lifecycle this engine tracks (processed one host at a time, in
config order — a host's findings are all walked to a terminal status,
or to review_needed, before the NEXT host's clone_and_scan is issued):

    network_scan_status: not_started -> scanned (or scan_failed)
    local_audit_status:  not_started -> audited (or audit_failed)
                                         (only attempted once network-scanned)

    per finding:
        discovered -> poc_confirmed -> remediation_planned -> remediation_applied -> reverified_closed
                   |-> poc_not_reachable                                          |-> reverify_failed (-> review_needed)
                   |-> poc_inconclusive

Usage:
    python3 range_traversal.py init --config <hosts.json>
    python3 range_traversal.py next
    python3 range_traversal.py record-scan --vmid <golden_vmid> --working-vmid <id> \
        --target <ip> --discover <path to range_discover.py>
    python3 range_traversal.py record-audit --vmid <golden_vmid> --working-vmid <id> \
        --audit <path to range_host_audit.py>
    python3 range_traversal.py assign-poc --vmid <golden_vmid> --finding <finding_id> \
        --poc-script <path to range_poc.py> [--reverify]
    python3 range_traversal.py report-poc --vmid <golden_vmid> --finding <finding_id> \
        --poc-script <path to range_poc.py> --result-file <path> [--reverify]
    python3 range_traversal.py assign-remediate --vmid <golden_vmid> --finding <finding_id> \
        --remediate-script <path to range_remediate.py> [--dry-run]
    python3 range_traversal.py report-remediate --vmid <golden_vmid> --finding <finding_id> \
        --remediate-script <path to range_remediate.py> --result-file <path>
    python3 range_traversal.py status
    python3 range_traversal.py reset-host --vmid <golden_vmid> [--reason <text>]
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import subprocess
import sys

STATE_PATH = "/workspace/.range_traversal_state.json"
STATE_LOCK_PATH = STATE_PATH + ".lock"
CONTEXT_DIR = "/workspace"


@contextlib.contextmanager
def state_lock():
    with open(STATE_LOCK_PATH, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def load_state() -> dict:
    if not os.path.isfile(STATE_PATH):
        print(json.dumps({"error": "No traversal state found — run `init` first."}, indent=2))
        sys.exit(1)
    with open(STATE_PATH) as f:
        return json.load(f)


def save_state(state: dict):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def run_script(script_path: str, args: list) -> dict:
    result = subprocess.run(["python3", script_path] + args, capture_output=True, text=True)
    if result.returncode != 0 and not result.stdout.strip():
        return {"_error": True, "stderr": result.stderr}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"_error": True, "reason": "non-JSON output", "stdout": result.stdout, "stderr": result.stderr}


def finding_id(scope: str, *key_parts) -> str:
    raw = (scope + ":" + ":".join(str(p) for p in key_parts)).encode()
    return "f-" + hashlib.sha256(raw).hexdigest()[:10]


def finding_description(finding: dict) -> str:
    if finding["scope"] == "network":
        return f"{finding.get('service') or 'unknown service'} {finding.get('version') or ''} " \
               f"on port {finding.get('port')}/{finding.get('proto')}".strip()
    return finding.get("description") or f"{finding.get('category')}: {finding.get('command')}"


def context_path(finding_id_: str, kind: str) -> str:
    return os.path.join(CONTEXT_DIR, f".range_context_{finding_id_}_{kind}.json")


def write_context(path: str, data: dict):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def cmd_init(config_path: str):
    if not os.path.isfile(config_path):
        print(json.dumps({"initialized": False, "reason": f"--config path does not exist: {config_path!r}"}, indent=2))
        sys.exit(1)
    with open(config_path) as f:
        cfg = json.load(f)

    with state_lock():
        state = load_state() if os.path.isfile(STATE_PATH) else {"config_path": config_path, "hosts": {}}
        state["config_path"] = config_path
        for vmid, host_cfg in cfg.get("hosts", {}).items():
            rec = state["hosts"].setdefault(vmid, {})
            rec.setdefault("host", host_cfg["host"])
            rec.setdefault("os", host_cfg["os"])
            rec.setdefault("ipv4", host_cfg["ipv4"])
            rec.setdefault("ipv6", host_cfg.get("ipv6"))
            rec.setdefault("working_vmid", None)
            rec.setdefault("network_scan_status", "not_started")
            rec.setdefault("local_audit_status", "not_started")
            rec.setdefault("last_scan_target", None)
            rec.setdefault("last_scan_method", None)
            rec.setdefault("findings", {})
        save_state(state)

    print(json.dumps({
        "initialized": True,
        "total_hosts": len(state["hosts"]),
        "note": "Each host still needs its working clone (<host>-ctf) created and started "
                "via proxmox_bridge.py's /vm/clone and /vm/{vmid}/start BEFORE record-scan "
                "can run against it — that lifecycle step is intentionally outside this "
                "script, same division of labor as ctf_traversal.py leaving flag derivation "
                "to the agent.",
    }, indent=2))


def host_action(vmid: str, rec: dict):
    """Returns the next action for ONE host, or None if this host is
    fully done (every finding terminal, or audit/scan failed with
    nothing further to do)."""
    if rec["network_scan_status"] == "not_started":
        return {
            "vmid": vmid, "host": rec["host"], "action": "clone_and_scan",
            "note": f"Clone+start {rec['host']}-golden (vmid {vmid}) as {rec['host']}-ctf via "
                    "proxmox_bridge.py, then call range_discover.py against its address and "
                    "feed the result to `record-scan`.",
        }
    if rec["network_scan_status"] == "scanned" and rec["local_audit_status"] == "not_started":
        return {
            "vmid": vmid, "host": rec["host"], "working_vmid": rec["working_vmid"], "action": "local_audit",
            "note": "Call range_host_audit.py against the working clone via proxmox_bridge.py's "
                    "guest-exec channel and feed the result to `record-audit`.",
        }

    for fid, finding in rec["findings"].items():
        status = finding["status"]
        if status == "discovered":
            return {
                "vmid": vmid, "host": rec["host"], "working_vmid": rec["working_vmid"],
                "finding_id": fid, "scope": finding["scope"], "action": "assign_poc",
                "description": finding_description(finding),
            }
        if status == "poc_confirmed":
            return {
                "vmid": vmid, "host": rec["host"], "working_vmid": rec["working_vmid"],
                "finding_id": fid, "action": "assign_remediate",
                "description": finding_description(finding),
            }
        if status == "remediation_applied":
            return {
                "vmid": vmid, "host": rec["host"], "working_vmid": rec["working_vmid"],
                "finding_id": fid, "scope": finding["scope"], "action": "assign_reverify",
                "description": finding_description(finding),
            }
        if status == "reverify_failed":
            return {
                "vmid": vmid, "host": rec["host"], "working_vmid": rec["working_vmid"],
                "finding_id": fid, "action": "review_needed",
                "note": "Remediation did not close this finding — read `status` for its "
                        "remediation_summary/evidence before trying again; the operator "
                        "should also be told about this one directly.",
            }
    return None


def cmd_next():
    state = load_state()
    for vmid, rec in state["hosts"].items():
        action = host_action(vmid, rec)
        if action is not None:
            print(json.dumps(action, indent=2))
            return
    print(json.dumps({"done": True, "summary": summarize(state)}, indent=2))


def cmd_record_scan(vmid: str, working_vmid: str, target: str, discover_script: str, nmap_path: str, ports: str):
    with state_lock():
        state = load_state()
        if vmid not in state["hosts"]:
            print(json.dumps({"recorded": False, "reason": f"vmid {vmid} not in state — run init first"}, indent=2))
            sys.exit(1)

        scan_args = ["scan", "--target", target]
        if ports:
            scan_args += ["--ports", ports]
        if nmap_path:
            scan_args += ["--nmap-path", nmap_path]
        scan_result = run_script(discover_script, scan_args)

        rec = state["hosts"][vmid]
        rec["working_vmid"] = working_vmid
        rec["last_scan_target"] = target

        if scan_result.get("_error") or not scan_result.get("reachable", False):
            rec["network_scan_status"] = "scan_failed"
            save_state(state)
            print(json.dumps({"recorded": True, "reachable": False, "scan_result": scan_result}, indent=2))
            return

        rec["network_scan_status"] = "scanned"
        rec["last_scan_method"] = scan_result.get("scan_method")
        for f in scan_result.get("findings", []):
            fid = finding_id("network", f["port"], f["proto"], f.get("service", ""))
            if fid not in rec["findings"]:
                rec["findings"][fid] = {
                    "scope": "network",
                    "port": f["port"], "proto": f["proto"],
                    "service": f.get("service", ""), "version": f.get("version", ""),
                    "status": "discovered", "poc_evidence": "", "poc_method": "", "remediation_summary": "",
                }
        save_state(state)

    print(json.dumps({"recorded": True, "reachable": True, "findings_total": len(rec["findings"])}, indent=2))


def cmd_record_audit(vmid: str, working_vmid: str, audit_script: str, timeout: int):
    with state_lock():
        state = load_state()
        if vmid not in state["hosts"]:
            print(json.dumps({"recorded": False, "reason": f"vmid {vmid} not in state — run init first"}, indent=2))
            sys.exit(1)
        rec = state["hosts"][vmid]
        rec["working_vmid"] = working_vmid

        audit_args = [
            "audit", "--golden-vmid", vmid, "--working-vmid", working_vmid, "--os", rec["os"],
            "--timeout", str(timeout),
        ]
        audit_result = run_script(audit_script, audit_args)

        if audit_result.get("_error") or not audit_result.get("reachable", False):
            rec["local_audit_status"] = "audit_failed"
            save_state(state)
            print(json.dumps({"recorded": True, "reachable": False, "audit_result": audit_result}, indent=2))
            return

        rec["local_audit_status"] = "audited"
        new_findings = 0
        for f in audit_result.get("findings", []):
            fid = finding_id("local", f["category"], f["command"])
            if fid not in rec["findings"]:
                rec["findings"][fid] = {
                    "scope": "local",
                    "category": f["category"], "command": f["command"],
                    "output": f.get("output", ""), "description": f.get("description", ""),
                    "status": "discovered", "poc_evidence": "", "poc_method": "", "remediation_summary": "",
                }
                new_findings += 1
        save_state(state)

    print(json.dumps({
        "recorded": True, "reachable": True,
        "new_findings": new_findings, "findings_total": len(rec["findings"]),
        "audit_errors": audit_result.get("errors", []),
    }, indent=2))


def cmd_assign_poc(vmid: str, fid: str, poc_script: str, max_attempts: int, time_budget: int, reverify: bool):
    state = load_state()
    if vmid not in state["hosts"] or fid not in state["hosts"][vmid]["findings"]:
        print(json.dumps({"assigned": False, "reason": f"vmid {vmid} / finding {fid} not found in state"}, indent=2))
        sys.exit(1)
    rec = state["hosts"][vmid]
    finding = rec["findings"][fid]

    required_status = "remediation_applied" if reverify else "discovered"
    if finding["status"] != required_status:
        print(json.dumps({
            "assigned": False,
            "reason": f"finding {fid} has status '{finding['status']}', expected '{required_status}'.",
        }, indent=2))
        sys.exit(1)

    if finding["scope"] == "network":
        context = {
            "target": rec["last_scan_target"] or rec["ipv4"], "port": finding["port"], "proto": finding["proto"],
            "service": finding["service"], "version": finding["version"], "scan_method": rec["last_scan_method"],
        }
    else:
        context = {"category": finding["category"], "command": finding["command"],
                    "output": finding["output"], "description": finding["description"]}
    if reverify:
        # Reverify must target the SAME method that originally confirmed this
        # finding, not a fresh exploration — see range_poc.py's
        # build_reverify_banner for why these three fields matter. Without
        # them, "repeat the attempt" degrades into "try something, anything,"
        # which doesn't actually confirm the remediation closed the specific
        # path that was proven exploitable.
        context["prior_poc_method"] = finding.get("poc_method", "")
        context["prior_poc_evidence"] = finding.get("poc_evidence", "")
        context["remediation_summary"] = finding.get("remediation_summary", "")
    ctx_path = context_path(fid, "poc")
    write_context(ctx_path, context)

    args = [
        "assign", "--golden-vmid", vmid, "--working-vmid", rec["working_vmid"] or "",
        "--host", rec["host"], "--finding-id", fid, "--scope", finding["scope"],
        "--context-file", ctx_path, "--max-attempts", str(max_attempts),
        "--time-budget-minutes", str(time_budget),
    ]
    if reverify:
        args.append("--reverify")
    result = run_script(poc_script, args)
    print(json.dumps(result, indent=2))
    if not result.get("assigned"):
        sys.exit(1)


def cmd_report_poc(vmid: str, fid: str, poc_script: str, result_file: str, reverify: bool):
    with state_lock():
        state = load_state()
        if vmid not in state["hosts"] or fid not in state["hosts"][vmid]["findings"]:
            print(json.dumps({"reported": False, "reason": f"vmid {vmid} / finding {fid} not found in state"}, indent=2))
            sys.exit(1)
        rec = state["hosts"][vmid]
        finding = rec["findings"][fid]

        args = [
            "report", "--golden-vmid", vmid, "--working-vmid", rec["working_vmid"] or "",
            "--finding-id", fid, "--result-file", result_file,
        ]
        result = run_script(poc_script, args)

        if result.get("_error") or not result.get("reported"):
            print(json.dumps(result, indent=2))
            sys.exit(1)

        reachable = result.get("reachable")
        if reverify:
            status = "reverified_closed" if reachable is False else (
                "reverify_failed" if reachable is True else "reverify_inconclusive"
            )
        else:
            status = {True: "poc_confirmed", False: "poc_not_reachable"}.get(reachable, "poc_inconclusive")

        finding["status"] = status
        finding["poc_evidence"] = result.get("evidence", finding["poc_evidence"])
        finding["poc_method"] = result.get("method", finding["poc_method"])
        save_state(state)

    print(json.dumps({"vmid": vmid, "finding_id": fid, "status": status}, indent=2))
    if status == "reverify_failed":
        sys.exit(1)


def cmd_assign_remediate(vmid: str, fid: str, remediate_script: str, dry_run: bool):
    state = load_state()
    if vmid not in state["hosts"] or fid not in state["hosts"][vmid]["findings"]:
        print(json.dumps({"assigned": False, "reason": f"vmid {vmid} / finding {fid} not found in state"}, indent=2))
        sys.exit(1)
    rec = state["hosts"][vmid]
    finding = rec["findings"][fid]

    if finding["status"] != "poc_confirmed":
        print(json.dumps({
            "assigned": False,
            "reason": f"finding {fid} has status '{finding['status']}', expected 'poc_confirmed'.",
        }, indent=2))
        sys.exit(1)

    context = {
        "scope": finding["scope"], "finding_description": finding_description(finding),
        "target": (rec["last_scan_target"] or rec["ipv4"]) if finding["scope"] == "network" else "",
        "poc_evidence": finding["poc_evidence"], "poc_method": finding["poc_method"],
    }
    ctx_path = context_path(fid, "remediate")
    write_context(ctx_path, context)

    args = [
        "assign", "--golden-vmid", vmid, "--working-vmid", rec["working_vmid"] or "",
        "--host", rec["host"], "--finding-id", fid, "--context-file", ctx_path,
    ]
    if dry_run:
        args.append("--dry-run")
    result = run_script(remediate_script, args)
    print(json.dumps(result, indent=2))
    if not result.get("assigned"):
        sys.exit(1)


def cmd_report_remediate(vmid: str, fid: str, remediate_script: str, result_file: str):
    with state_lock():
        state = load_state()
        if vmid not in state["hosts"] or fid not in state["hosts"][vmid]["findings"]:
            print(json.dumps({"reported": False, "reason": f"vmid {vmid} / finding {fid} not found in state"}, indent=2))
            sys.exit(1)
        rec = state["hosts"][vmid]
        finding = rec["findings"][fid]

        args = [
            "report", "--golden-vmid", vmid, "--working-vmid", rec["working_vmid"] or "",
            "--finding-id", fid, "--result-file", result_file,
        ]
        result = run_script(remediate_script, args)

        if result.get("_error") or not result.get("reported"):
            print(json.dumps(result, indent=2))
            sys.exit(1)

        status = "remediation_applied" if result.get("applied") else "remediation_planned"
        finding["status"] = status
        finding["remediation_summary"] = result.get("remediation_summary", finding["remediation_summary"])
        save_state(state)

    print(json.dumps({"vmid": vmid, "finding_id": fid, "status": status}, indent=2))


def cmd_reset_host(vmid: str, reason: str):
    # For a working clone destroyed OUT OF BAND — manually in Proxmox,
    # not via this skill's own teardown step (POST stop + DELETE
    # through proxmox_bridge.py) or via any script here. Without this,
    # `working_vmid` just sits in state pointing at a vmid that's gone,
    # and `next` keeps handing out actions against it with no way to
    # tell it otherwise. See proxmox_bridge.py's new GET
    # /vm/{vmid}/status for how to actually confirm a vmid is gone
    # before calling this — don't reset on a guess.
    #
    # Deliberately does NOT touch `findings` — a finding already at
    # poc_confirmed/remediation_applied/etc. keeps its recorded
    # status/evidence; this only clears the per-host clone-tracking
    # fields so `next` naturally re-issues `clone_and_scan` for a FRESH
    # working clone. Be aware this means a finding's prior evidence was
    # collected against the clone that no longer exists — if you're
    # resetting mid-assessment (not after a clean host-done teardown),
    # treat any already-advanced finding's evidence as worth a second
    # look once the new clone is up, the same caution a reverify
    # banner already applies to pre-remediation audit output elsewhere
    # in this skill.
    with state_lock():
        state = load_state()
        if vmid not in state["hosts"]:
            print(json.dumps({"reset": False, "reason": f"vmid {vmid} not in state"}, indent=2))
            sys.exit(1)
        rec = state["hosts"][vmid]
        old_working_vmid = rec["working_vmid"]
        rec["working_vmid"] = None
        rec["network_scan_status"] = "not_started"
        rec["local_audit_status"] = "not_started"
        rec.setdefault("reset_history", []).append({
            "old_working_vmid": old_working_vmid, "reason": reason or "(no reason given)",
        })
        save_state(state)

    print(json.dumps({
        "reset": True, "vmid": vmid, "host": rec["host"],
        "old_working_vmid": old_working_vmid,
        "findings_untouched": len(rec["findings"]),
        "note": "network_scan_status/local_audit_status reset to not_started — `next` will "
                "issue clone_and_scan again for a fresh working clone. Findings already "
                "recorded (status and evidence) were left untouched; if any were already "
                "poc_confirmed or further along, their evidence predates this reset — worth "
                "a second look once the new clone exists, not just resumed blindly.",
    }, indent=2))


def summarize(state: dict) -> dict:
    host_counts = {}
    audit_counts = {}
    finding_counts = {}
    for rec in state["hosts"].values():
        host_counts[rec["network_scan_status"]] = host_counts.get(rec["network_scan_status"], 0) + 1
        audit_counts[rec["local_audit_status"]] = audit_counts.get(rec["local_audit_status"], 0) + 1
        for finding in rec["findings"].values():
            finding_counts[finding["status"]] = finding_counts.get(finding["status"], 0) + 1
    return {
        "hosts_by_network_scan_status": host_counts,
        "hosts_by_local_audit_status": audit_counts,
        "findings_by_status": finding_counts,
    }


def cmd_status():
    state = load_state()
    print(json.dumps({"summary": summarize(state), "hosts": state["hosts"]}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init")
    p_init.add_argument("--config", required=True)

    sub.add_parser("next")

    p_rs = sub.add_parser("record-scan")
    p_rs.add_argument("--vmid", required=True, help="the golden VMID (hosts.json key)")
    p_rs.add_argument("--working-vmid", required=True)
    p_rs.add_argument("--target", required=True)
    p_rs.add_argument("--discover", required=True, help="path to range_discover.py")
    p_rs.add_argument("--nmap-path", default=None)
    p_rs.add_argument("--ports", default=None)

    p_ra = sub.add_parser("record-audit")
    p_ra.add_argument("--vmid", required=True)
    p_ra.add_argument("--working-vmid", required=True)
    p_ra.add_argument("--audit", required=True, help="path to range_host_audit.py")
    p_ra.add_argument("--timeout", type=int, default=330)

    p_ap = sub.add_parser("assign-poc")
    p_ap.add_argument("--vmid", required=True)
    p_ap.add_argument("--finding", required=True)
    p_ap.add_argument("--poc-script", required=True)
    p_ap.add_argument("--max-attempts", type=int, default=5)
    p_ap.add_argument("--time-budget-minutes", type=int, default=20)
    p_ap.add_argument("--reverify", action="store_true")

    p_rp = sub.add_parser("report-poc")
    p_rp.add_argument("--vmid", required=True)
    p_rp.add_argument("--finding", required=True)
    p_rp.add_argument("--poc-script", required=True)
    p_rp.add_argument("--result-file", required=True)
    p_rp.add_argument("--reverify", action="store_true")

    p_ar = sub.add_parser("assign-remediate")
    p_ar.add_argument("--vmid", required=True)
    p_ar.add_argument("--finding", required=True)
    p_ar.add_argument("--remediate-script", required=True)
    p_ar.add_argument("--dry-run", action="store_true")

    p_rr = sub.add_parser("report-remediate")
    p_rr.add_argument("--vmid", required=True)
    p_rr.add_argument("--finding", required=True)
    p_rr.add_argument("--remediate-script", required=True)
    p_rr.add_argument("--result-file", required=True)

    sub.add_parser("status")

    p_rh = sub.add_parser("reset-host")
    p_rh.add_argument("--vmid", required=True, help="the golden VMID (hosts.json key)")
    p_rh.add_argument("--reason", default="",
                       help="why this host is being reset, e.g. 'working clone manually "
                            "destroyed in Proxmox outside the sanctioned teardown' — recorded "
                            "in state's reset_history for an audit trail")

    args = parser.parse_args()
    if args.command == "init":
        cmd_init(args.config)
    elif args.command == "next":
        cmd_next()
    elif args.command == "record-scan":
        cmd_record_scan(args.vmid, args.working_vmid, args.target, args.discover, args.nmap_path, args.ports)
    elif args.command == "record-audit":
        cmd_record_audit(args.vmid, args.working_vmid, args.audit, args.timeout)
    elif args.command == "assign-poc":
        cmd_assign_poc(args.vmid, args.finding, args.poc_script, args.max_attempts, args.time_budget_minutes, args.reverify)
    elif args.command == "report-poc":
        cmd_report_poc(args.vmid, args.finding, args.poc_script, args.result_file, args.reverify)
    elif args.command == "assign-remediate":
        cmd_assign_remediate(args.vmid, args.finding, args.remediate_script, args.dry_run)
    elif args.command == "report-remediate":
        cmd_report_remediate(args.vmid, args.finding, args.remediate_script, args.result_file)
    elif args.command == "status":
        cmd_status()
    elif args.command == "reset-host":
        cmd_reset_host(args.vmid, args.reason)


if __name__ == "__main__":
    main()
