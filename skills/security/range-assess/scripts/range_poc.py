#!/usr/bin/env python3
"""
range_poc.py — guardrail script for the PoC step. Two subcommands,
`assign` and `report`, and NO model call anywhere in this file.

WHY THIS SHAPE, AND WHY IT CHANGED FROM AN EARLIER VERSION: an earlier
draft of this script made its own standalone call to Ollama's /api/chat
to ask a model whether a finding was exploitable. That was a mistake,
caught directly: a bare Ollama chat call has no tool access at all — no
shell, no network, no guest-exec — so it could never actually attempt
anything, network or local, no matter what the prompt asked for. The
correct shape, matching submit_flag.py's own role for ksnctf exactly:
this script never performs or judges an exploitation attempt itself. It
only (1) hands the calling agent a filled, scope-appropriate task prompt
to act on with ITS OWN tools — real shell/network access for a network
finding, real calls to proxmox_bridge.py's guest-exec endpoint for a
local one — and (2) validates and logs whatever result the agent
reports back after actually doing that work. "The local Qwen model does
PoC" means the agent's own backend model, acting through its own normal
tool-calling turn — not a second, tool-less, sidecar call hidden inside
this script.

Hard, code-enforced guardrails (checked before `assign` hands out a
prompt, and re-checked at `report` time since that's when an attempt is
actually consumed):
  - --working-vmid must differ from --golden-vmid.
  - At most MAX_ATTEMPTS_PER_FINDING REPORTED attempts per (vmid,
    finding_id), ever, tracked in /workspace/.range_poc_log.json.
    Calling `assign` repeatedly without ever reporting costs nothing —
    only a `report` call consumes an attempt, the same principle
    submit_flag.py applies to network/parse errors not counting as
    attempts (here: not having done real work yet isn't a counted
    attempt either).
  - At least MIN_SECONDS_BETWEEN_ATTEMPTS between successive REPORTED
    attempts for the same finding.

NEW, UNTESTED CODE beyond the guardrail logic itself (format checks,
cap, delay, audit log), which is straightforward and carried over
unchanged in spirit from the earlier version — the two-phase
assign/report flow has not been exercised end-to-end against a real
target yet.

Usage:
    # 1. Get the task prompt for this finding
    python3 range_poc.py assign \
        --golden-vmid 9001 --working-vmid <clone vmid> \
        --host example-linux-host --finding-id f-abc123 --scope network \
        --context-file /workspace/finding_f-abc123_context.json

    # 2. The agent does the real work with its own tools (network
    #    access for --scope network, proxmox_bridge.py guest-exec calls
    #    for --scope local), then writes its own result as JSON:
    #    {"reachable": true|false|"inconclusive", "method": "...",
    #     "evidence": "...", "commands_run": [...], "notes": "..."}
    #    to a file of its choosing.

    # 3. Report that result — this is what actually validates/logs it
    python3 range_poc.py report \
        --golden-vmid 9001 --working-vmid <clone vmid> \
        --finding-id f-abc123 --result-file /workspace/result_f-abc123.json
"""
import argparse
import json
import os
import sys
import time

LOG_PATH = "/workspace/.range_poc_log.json"
MAX_ATTEMPTS_PER_FINDING = 3
MIN_SECONDS_BETWEEN_ATTEMPTS = 30

PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")
PROMPT_PATH_BY_SCOPE = {
    "network": os.path.join(PROMPTS_DIR, "poc_task_prompt_network.md"),
    "local": os.path.join(PROMPTS_DIR, "poc_task_prompt_local.md"),
}


def load_log() -> dict:
    if not os.path.isfile(LOG_PATH):
        return {}
    with open(LOG_PATH) as f:
        return json.load(f)


def save_log(log: dict):
    with open(LOG_PATH, "w") as f:
        json.dump(log, f, indent=2)


def check_guardrail(key: str, log: dict):
    attempts = log.get(key, [])
    if len(attempts) >= MAX_ATTEMPTS_PER_FINDING:
        return (
            f"REFUSED: {len(attempts)} PoC attempts already logged for {key} — "
            f"hard cap of {MAX_ATTEMPTS_PER_FINDING} reached. Resetting requires "
            f"editing {LOG_PATH} directly."
        )
    if attempts:
        elapsed = time.time() - attempts[-1]["timestamp"]
        if elapsed < MIN_SECONDS_BETWEEN_ATTEMPTS:
            wait = MIN_SECONDS_BETWEEN_ATTEMPTS - elapsed
            return (
                f"REFUSED: only {elapsed:.0f}s since the last PoC attempt for {key} — "
                f"minimum {MIN_SECONDS_BETWEEN_ATTEMPTS}s enforced. Wait {wait:.0f}s more."
            )
    return None


def build_reverify_banner(finding_id: str, context: dict) -> str:
    prior_method = context.get("prior_poc_method") or "(not recorded)"
    prior_evidence = context.get("prior_poc_evidence") or "(not recorded)"
    remediation_summary = context.get("remediation_summary") or "(not recorded)"
    return (
        "## This is a REVERIFICATION, not a fresh attempt\n\n"
        f"This exact finding (`{finding_id}`) was already confirmed exploitable, "
        "and a remediation has since been applied. Your job now is narrower than "
        "the original PoC: **repeat the specific method that worked before** and "
        "confirm it no longer does — this is not an invitation to explore new "
        "techniques.\n\n"
        f"- Method that worked originally: {prior_method}\n"
        f"- Evidence that confirmed it: {prior_evidence}\n"
        f"- Remediation applied since then: {remediation_summary}\n\n"
        "If the exact original method is no longer applicable (e.g. the service "
        "was removed entirely rather than patched), say so in your `notes` — that "
        "still counts as closed, but say why. If you find a DIFFERENT way to "
        "achieve the same impact despite the remediation, report `reachable: "
        "true` and describe the new method — the remediation genuinely failed to "
        "close the underlying risk even though it blocked the original path.\n\n"
        "Any audit output or evidence shown elsewhere in this prompt may have "
        "been captured BEFORE this remediation was applied — treat it as "
        "historical context for what was originally found, not as proof of the "
        "current state. What matters now is what you observe when you actually "
        "retry it.\n"
    )


def fill_prompt(scope: str, host: str, working_vmid: str, golden_vmid: str, finding_id: str,
                 context: dict, max_attempts: int, time_budget_minutes: int, reverify: bool) -> str:
    template_path = PROMPT_PATH_BY_SCOPE[scope]
    with open(template_path) as fh:
        template = fh.read()
    template = template.split("\n---\n", 1)[-1]

    values = {
        "host": host, "working_vmid": working_vmid, "golden_vmid": golden_vmid,
        "finding_id": finding_id, "max_attempts": max_attempts,
        "time_budget_minutes": time_budget_minutes,
        "reverify_banner": build_reverify_banner(finding_id, context) if reverify else "",
    }
    if scope == "network":
        values.update({
            "ipv4_or_ipv6": context.get("target", "(unknown)"),
            "port": context.get("port", "(unknown)"),
            "proto": context.get("proto", "tcp"),
            "service": context.get("service") or "(unknown — not identified by scan)",
            "version": context.get("version") or "(unknown)",
            "scan_method": context.get("scan_method", "range_discover.py"),
        })
    else:
        values.update({
            "category": context.get("category", "(unspecified)"),
            "command": context.get("command", "(unspecified)"),
            "audit_output": context.get("output", "(no audit output provided)"),
            "finding_description": context.get("description", "(no description provided)"),
        })
    for k, v in values.items():
        template = template.replace("{{" + k + "}}", str(v))
    return template


def cmd_assign(args):
    if args.working_vmid == args.golden_vmid:
        print(json.dumps({
            "assigned": False,
            "reason": (
                f"REFUSED: --working-vmid ({args.working_vmid}) is the same as "
                f"--golden-vmid ({args.golden_vmid}). This script never operates "
                "against a golden image — pass the actual working clone's VMID."
            ),
        }, indent=2))
        sys.exit(1)

    if not os.path.isfile(args.context_file):
        print(json.dumps({"assigned": False, "reason": f"--context-file not found: {args.context_file}"}, indent=2))
        sys.exit(1)
    with open(args.context_file) as f:
        context = json.load(f)

    key = f"{args.working_vmid}:{args.finding_id}"
    log = load_log()
    refusal = check_guardrail(key, log)
    if refusal:
        print(json.dumps({"assigned": False, "reason": refusal}, indent=2))
        sys.exit(1)

    prompt = fill_prompt(
        args.scope, args.host, args.working_vmid, args.golden_vmid, args.finding_id,
        context, args.max_attempts, args.time_budget_minutes, args.reverify,
    )
    attempts_used = len(log.get(key, []))
    print(json.dumps({
        "assigned": True, "scope": args.scope, "finding_id": args.finding_id,
        "reverify": args.reverify,
        "attempts_used": attempts_used, "attempts_remaining": MAX_ATTEMPTS_PER_FINDING - attempts_used,
        "prompt": prompt,
    }, indent=2))


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

    reachable = result.get("reachable")
    if reachable not in (True, False, "inconclusive"):
        print(json.dumps({
            "reported": False,
            "reason": f"--result-file's 'reachable' field must be true, false, or \"inconclusive\" — got {reachable!r}",
        }, indent=2))
        sys.exit(1)
    if reachable is True and not str(result.get("evidence", "")).strip():
        print(json.dumps({
            "reported": False,
            "reason": "REFUSED: 'reachable': true with empty 'evidence' — a confirmed finding needs the "
                      "concrete proof (command output, retrieved value, etc.), not just a verdict.",
        }, indent=2))
        sys.exit(1)

    key = f"{args.working_vmid}:{args.finding_id}"
    log = load_log()
    refusal = check_guardrail(key, log)
    if refusal:
        print(json.dumps({"reported": False, "reason": refusal}, indent=2))
        sys.exit(1)

    log.setdefault(key, []).append({"timestamp": time.time(), "result": result})
    save_log(log)

    # Also save a canonical copy, keyed by WORKING vmid (not golden) since
    # that's what the log above is keyed by and what a human cross-
    # referencing /workspace needs to match against it. Confirmed as a real
    # gap on a live run: the agent is free to name --result-file anything
    # ("a file of its own choosing", per the prompt), and in practice it
    # named these after the golden vmid instead — consistent, but at odds
    # with this log's own key, making the two hard to cross-reference. This
    # guarantees a predictably-named copy exists regardless of what the
    # agent actually passed as --result-file (a no-op overwrite if it
    # already matches).
    canonical_path = os.path.join("/workspace", f"result_{args.working_vmid}_{args.finding_id}.json")
    with open(canonical_path, "w") as f:
        json.dump(result, f, indent=2)

    print(json.dumps({
        "reported": True, "finding_id": args.finding_id, "reachable": reachable,
        "method": result.get("method", ""), "evidence": result.get("evidence", ""),
        "attempts_used": len(log[key]), "attempts_remaining": MAX_ATTEMPTS_PER_FINDING - len(log[key]),
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
    p_assign.add_argument("--scope", choices=["network", "local"], required=True)
    p_assign.add_argument("--context-file", required=True,
                           help="JSON file with scope-appropriate fields — see this script's own docstring")
    p_assign.add_argument("--max-attempts", type=int, default=5,
                           help="passed into the prompt as the agent's own per-call technique budget")
    p_assign.add_argument("--time-budget-minutes", type=int, default=20)
    p_assign.add_argument("--reverify", action="store_true",
                           help="this is a re-check of a finding after remediation, not the first PoC attempt — "
                                "adds a banner instructing the agent to repeat the ORIGINAL method "
                                "(from --context-file's prior_poc_method/prior_poc_evidence/remediation_summary)")

    p_report = sub.add_parser("report")
    p_report.add_argument("--golden-vmid", required=True)
    p_report.add_argument("--working-vmid", required=True)
    p_report.add_argument("--finding-id", required=True)
    p_report.add_argument("--result-file", required=True,
                           help="JSON file the agent wrote with its own result — "
                                "{reachable, method, evidence, commands_run, notes}")

    args = parser.parse_args()
    if args.command == "assign":
        cmd_assign(args)
    elif args.command == "report":
        cmd_report(args)


if __name__ == "__main__":
    main()
