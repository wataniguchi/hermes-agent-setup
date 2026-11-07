#!/usr/bin/env python3
"""
range_remediate.py — guardrail script for the remediation step. Same
assign/report shape as range_poc.py, and for the identical reason: no
model call anywhere in this file. `assign` fills and hands out the task
prompt (only for a finding that was actually confirmed — refuses on
empty/missing PoC evidence); the agent proposes/applies the fix itself,
using its own tools (a package update or config change over the
network session for a network-scope finding, a proxmox_bridge.py
guest-exec call for a local one); `report` validates and logs whatever
result the agent reports back.

Defensive content (patches, config changes), so this carries fewer
restrictions than range_poc.py (no attempt cap) — but still hard-guards
against targeting a golden image, and still refuses to hand out or log
anything for a finding that was never actually confirmed.

NEW, UNTESTED CODE — see range_poc.py's header for the identical caveat
about the two-phase assign/report flow not having been exercised
end-to-end yet.

Usage:
    python3 range_remediate.py assign \
        --golden-vmid 9001 --working-vmid <clone vmid> --host example-linux-host \
        --finding-id f-abc123 --context-file /workspace/finding_f-abc123_remediate_context.json \
        [--dry-run]

    python3 range_remediate.py report \
        --golden-vmid 9001 --working-vmid <clone vmid> --finding-id f-abc123 \
        --result-file /workspace/remediation_result_f-abc123.json
"""
import argparse
import json
import os
import sys
import time

LOG_PATH = "/workspace/.range_remediate_log.json"

PROMPT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "prompts", "remediation_task_prompt.md",
)


def load_log() -> dict:
    if not os.path.isfile(LOG_PATH):
        return {}
    with open(LOG_PATH) as f:
        return json.load(f)


def save_log(log: dict):
    with open(LOG_PATH, "w") as f:
        json.dump(log, f, indent=2)


def fill_prompt(host, working_vmid, golden_vmid, finding_id, context, dry_run) -> str:
    with open(PROMPT_PATH) as fh:
        template = fh.read()
    template = template.split("\n---\n", 1)[-1]
    values = {
        "host": host, "working_vmid": working_vmid, "golden_vmid": golden_vmid,
        "finding_id": finding_id,
        "scope": context.get("scope", "(unspecified)"),
        "finding_description": context.get("finding_description", "(no description provided)"),
        "target": context.get("target") or "n/a (local finding — no network target)",
        "poc_evidence": context.get("poc_evidence", ""),
        "poc_method": context.get("poc_method", ""),
        "dry_run": "true" if dry_run else "false",
    }
    for k, v in values.items():
        template = template.replace("{{" + k + "}}", str(v))
    return template


def cmd_assign(args):
    if args.working_vmid == args.golden_vmid:
        print(json.dumps({
            "assigned": False,
            "reason": (
                f"REFUSED: --working-vmid ({args.working_vmid}) is the same as "
                f"--golden-vmid ({args.golden_vmid}) — refusing to remediate a golden image."
            ),
        }, indent=2))
        sys.exit(1)

    if not os.path.isfile(args.context_file):
        print(json.dumps({"assigned": False, "reason": f"--context-file not found: {args.context_file}"}, indent=2))
        sys.exit(1)
    with open(args.context_file) as f:
        context = json.load(f)

    if not str(context.get("poc_evidence", "")).strip():
        print(json.dumps({
            "assigned": False,
            "reason": "REFUSED: context file's 'poc_evidence' is empty. This script only remediates "
                      "a finding that was actually confirmed by range_poc.py — pass the evidence "
                      "string from its report, not a placeholder.",
        }, indent=2))
        sys.exit(1)

    prompt = fill_prompt(args.host, args.working_vmid, args.golden_vmid, args.finding_id, context, args.dry_run)
    print(json.dumps({"assigned": True, "finding_id": args.finding_id, "prompt": prompt}, indent=2))


def cmd_report(args):
    if args.working_vmid == args.golden_vmid:
        print(json.dumps({
            "reported": False,
            "reason": f"REFUSED: --working-vmid ({args.working_vmid}) equals --golden-vmid ({args.golden_vmid}).",
        }, indent=2))
        sys.exit(1)

    if not os.path.isfile(args.result_file):
        print(json.dumps({"reported": False, "reason": f"--result-file not found: {args.result_file}"}, indent=2))
        sys.exit(1)
    with open(args.result_file) as f:
        result = json.load(f)

    if "applied" not in result or not isinstance(result["applied"], bool):
        print(json.dumps({
            "reported": False,
            "reason": "--result-file's 'applied' field must be a real boolean (true/false).",
        }, indent=2))
        sys.exit(1)

    key = f"{args.working_vmid}:{args.finding_id}"
    log = load_log()
    log.setdefault(key, []).append({"timestamp": time.time(), "dry_run": args.dry_run, "result": result})
    save_log(log)

    # Same canonical-copy fix as range_poc.py's cmd_report, and for the
    # identical reason — see its comment. Keyed by working vmid, matching
    # this log's own key, regardless of what --result-file was actually
    # named.
    canonical_path = os.path.join("/workspace", f"remediation_result_{args.working_vmid}_{args.finding_id}.json")
    with open(canonical_path, "w") as f:
        json.dump(result, f, indent=2)

    print(json.dumps({
        "reported": True, "finding_id": args.finding_id, "applied": result["applied"],
        "remediation_summary": result.get("remediation_summary", ""),
        "canonical_result_file": canonical_path,
    }, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_assign = sub.add_parser("assign")
    p_assign.add_argument("--golden-vmid", required=True)
    p_assign.add_argument("--working-vmid", required=True)
    p_assign.add_argument("--host", required=True)
    p_assign.add_argument("--finding-id", required=True)
    p_assign.add_argument("--context-file", required=True)
    p_assign.add_argument("--dry-run", action="store_true")

    p_report = sub.add_parser("report")
    p_report.add_argument("--golden-vmid", required=True)
    p_report.add_argument("--working-vmid", required=True)
    p_report.add_argument("--finding-id", required=True)
    p_report.add_argument("--result-file", required=True)
    p_report.add_argument("--dry-run", action="store_true", help="record-keeping only — mirrors whether assign was called with --dry-run")

    args = parser.parse_args()
    if args.command == "assign":
        cmd_assign(args)
    elif args.command == "report":
        cmd_report(args)


if __name__ == "__main__":
    main()
