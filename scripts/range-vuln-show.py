#!/usr/bin/env python3
"""
range-vuln-show.py — one finding's full dossier in a single report:
identity, what the audit/scan found, the PoC that proved it, the
remediation applied, the reverification, any operator review, and the
on-disk result files — read from the sweep's state and logs.

Read-only. Standard library only.

Usage:
    python3 range-vuln-show.py --workspace <ws> <finding_id>
    python3 range-vuln-show.py --workspace <ws> --vmid <golden_vmid> <finding_id>

A finding ID (f-xxxxxxxxxx) is NOT unique across hosts — the same ID can
appear on two unrelated hosts (it's a hash of category+command, not of the
host). If the ID you pass matches more than one host, this lists the
matches and asks you to add --vmid <golden_vmid> to pick one.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
MAXFIELD = 4000  # cap very long text fields; full content is in the named file


def ts_str(ts) -> str:
    try:
        return datetime.fromtimestamp(ts, tz=JST).isoformat()
    except (TypeError, ValueError, OSError):
        return str(ts)


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def clip(text) -> str:
    if text is None:
        return "(none)"
    s = text if isinstance(text, str) else json.dumps(text, indent=2, ensure_ascii=False)
    if len(s) > MAXFIELD:
        return s[:MAXFIELD] + f"\n…[truncated, {len(s) - MAXFIELD} more chars]"
    return s


def hr(title):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def kv(label, value):
    print(f"  {label:<22} {value}")


def dump_json_block(obj):
    print(clip(obj))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--workspace", default="/workspace")
    p.add_argument("--vmid", default=None, help="golden VMID, to disambiguate an ID shared across hosts")
    p.add_argument("finding_id")
    args = p.parse_args()

    ws = args.workspace
    fid = args.finding_id
    state = load(os.path.join(ws, ".range_traversal_state.json"), None)
    if state is None:
        print(f"FATAL: cannot read {ws}/.range_traversal_state.json", file=sys.stderr)
        sys.exit(1)

    # Find every (golden_vmid, host_rec, finding) carrying this fid.
    matches = []
    for gvmid, rec in state.get("hosts", {}).items():
        if fid in rec.get("findings", {}):
            if args.vmid and gvmid != args.vmid:
                continue
            matches.append((gvmid, rec, rec["findings"][fid]))

    if not matches:
        where = f" on host {args.vmid}" if args.vmid else ""
        print(f"No finding {fid}{where} in {ws}/.range_traversal_state.json", file=sys.stderr)
        sys.exit(1)
    if len(matches) > 1:
        print(f"Finding {fid} exists on {len(matches)} hosts — add --vmid <golden_vmid> to pick one:",
              file=sys.stderr)
        for gvmid, rec, finding in matches:
            print(f"  --vmid {gvmid}   {rec.get('host')}   status={finding.get('status')}", file=sys.stderr)
        sys.exit(2)

    gvmid, rec, finding = matches[0]
    wv = rec.get("working_vmid")
    key = f"{wv}:{fid}"

    poc_log = load(os.path.join(ws, ".range_poc_log.json"), {})
    rem_log = load(os.path.join(ws, ".range_remediate_log.json"), {})
    poc_result = load(os.path.join(ws, f"result_{wv}_{fid}.json"), None)
    rem_result = load(os.path.join(ws, f"remediation_result_{wv}_{fid}.json"), None)

    # ---- Identity ----
    hr(f"FINDING {fid}  —  {finding.get('status')}")
    kv("Host", f"{rec.get('host')}  (golden vmid {gvmid})")
    kv("OS", rec.get("os"))
    kv("Address", f"{rec.get('ipv4')}  {rec.get('ipv6') or ''}".strip())
    kv("Working clone", wv)
    kv("Scope", finding.get("scope"))
    kv("Category / kind", f"{finding.get('category')}  /  {finding.get('kind', '(pre-kind)')}")
    kv("Log/result key", key)

    # ---- What was found ----
    hr("1. WHAT WAS FOUND (scan / audit evidence)")
    if finding.get("scope") == "network":
        kv("Service", finding.get("service") or "(unidentified)")
        kv("Version", finding.get("version") or "(unknown)")
        kv("Port/proto", f"{finding.get('port')}/{finding.get('proto')}")
    else:
        kv("Command", finding.get("command"))
        if finding.get("item") is not None:
            kv("Item", clip(finding.get("item")))
        print("  Audit output:")
        dump_json_block(finding.get("output"))
    if finding.get("description"):
        kv("Description", clip(finding.get("description")))

    # ---- PoC ----
    # IMPORTANT: a finding's poc_method/poc_evidence and its canonical
    # result_<wv>_<fid>.json are OVERWRITTEN on every report-poc, including
    # the reverify pass. So for a reverified finding, the "recorded" fields
    # and the canonical file show the REVERIFY, not the original PoC — the
    # original survives ONLY in the per-attempt log below. Read the full
    # attempt log, not just the recorded fields, to judge the original PoC.
    attempts = poc_log.get(key, [])
    hr("2. PROOF OF CONCEPT — full attempt history")
    print(f"  {len(attempts)} attempt(s) logged under {key}, oldest first.")
    print("  (reachable=True is a confirming PoC; a later reachable=False is a")
    print("   reverify showing the fix closed it. The ORIGINAL PoC is attempt [1].)")
    if not attempts:
        print("\n  (no attempts logged — nothing in .range_poc_log.json for this key)")
    for i, a in enumerate(attempts, 1):
        res = a.get("result", {})
        kind = "PoC/confirm" if res.get("reachable") is True else (
            "reverify/other" if res.get("reachable") is False else "inconclusive")
        print(f"\n  --- attempt [{i}]  reachable={res.get('reachable')!r}  ({kind})"
              f"  {ts_str(a.get('timestamp'))} ---")
        print("    method:")
        print("      " + clip(res.get("method")).replace("\n", "\n      "))
        print("    evidence:")
        print("      " + clip(res.get("evidence")).replace("\n", "\n      "))
        if res.get("commands_run"):
            print("    commands_run: " + clip(res.get("commands_run")))
        if res.get("notes"):
            print("    notes: " + clip(res.get("notes")))

    print("\n  -- 'Recorded' fields on the finding (LATEST report — a reverify overwrites these) --")
    kv("poc_method (latest)", clip(finding.get("poc_method")))
    print("  poc_evidence (latest):")
    dump_json_block(finding.get("poc_evidence"))
    print("\n  Canonical result file "
          f"(result_{wv}_{fid}.json — also the LATEST report)"
          f"{' — MISSING' if poc_result is None else ''}:")
    if poc_result is not None:
        dump_json_block(poc_result)

    # ---- Remediation ----
    hr("3. REMEDIATION (the fix)")
    kv("Summary", clip(finding.get("remediation_summary")))
    kv("Attempts", finding.get("remediation_attempts", 0))
    kv("needs_operator_decision", finding.get("needs_operator_decision", False))
    if finding.get("risk_notes"):
        print("  Risk notes:")
        dump_json_block(finding.get("risk_notes"))
    rem_attempts = rem_log.get(key, [])
    print(f"\n  Remediation attempt log — {len(rem_attempts)} attempt(s) under {key}:")
    if not rem_attempts:
        print("    (none logged)")
    for i, a in enumerate(rem_attempts, 1):
        res = a.get("result", {})
        print(f"    [{i}] applied={res.get('applied')!r}  "
              f"needs_operator_decision={res.get('needs_operator_decision')!r}")
    print("\n  Canonical remediation result file "
          f"(remediation_result_{wv}_{fid}.json){' — MISSING' if rem_result is None else ''}:")
    if rem_result is not None:
        dump_json_block(rem_result)

    # ---- Reverification ----
    hr("4. REVERIFICATION (did the fix actually close it)")
    status = finding.get("status")
    verdict = {
        "reverified_closed": "FIX PROVEN ON CLONE — reverify re-ran the original method and it "
                             "no longer works. The remediation is proven effective and ready to "
                             "deploy; deploying it is the operator's decision.",
        "reverify_failed": "FAILED — the fix was tried on the clone but the finding is STILL exploitable.",
        "reverify_inconclusive": "INCONCLUSIVE — the fix was applied on the clone but reverify could not confirm the outcome.",
        "remediation_applied": "PENDING — a fix is applied on the clone but reverify has not run yet.",
        "remediation_planned": "NOT PROVEN — a remediation was proposed but not proven to work (untested, or it couldn't apply).",
        "poc_confirmed": "NOT REACHED — proven exploitable, no remediation proposed yet.",
        "risk_accepted": "N/A — operator accepted the risk via resolve-review.",
        "closed_manually": "N/A — operator closed this by hand via resolve-review.",
    }.get(status, f"(status {status})")
    kv("Verdict", verdict)
    print("  (reverify re-runs range_poc.py with --reverify and appends to the PoC attempt")
    print("   log above; a `reachable: false` later attempt is the closed proof. Nothing here")
    print("   is applied beyond the disposable clone — a proven fix is ready to deploy, not deployed.)")
    if finding.get("needs_operator_decision"):
        print("  ⚑ needs_operator_decision: deploying this fix is flagged as a conscious operator")
        print("    call — it changes intended design, or has consequences you must weigh first.")

    # ---- Operator review ----
    review = [r for r in rec.get("review_log", []) if r.get("finding_id") == fid]
    if review or finding.get("operator_note"):
        hr("5. OPERATOR REVIEW (resolve-review)")
        if finding.get("operator_note"):
            kv("Operator note", clip(finding.get("operator_note")))
        for r in review:
            print(f"  - resolution={r.get('resolution')}  prior={r.get('prior_status')}  "
                  f"note={r.get('note','')!r}")

    # ---- Files on disk ----
    hr("FILES ON DISK")
    for name, present in (
        (f"result_{wv}_{fid}.json", poc_result is not None),
        (f"remediation_result_{wv}_{fid}.json", rem_result is not None),
    ):
        kv(name, "present" if present else "MISSING")
    print(f"\n  (field values above are capped at {MAXFIELD} chars; open the files for the full text.)")


if __name__ == "__main__":
    main()
