"""
range_traversal.py — orchestrator for the range assessment loop, built
on the same design as ctf_traversal.py: explicit params, a lock
spanning the whole load-modify-save span of every subcommand that
touches state, and bookkeeping-only logic that hands the real work out
to other scripts via subprocess — range_discover.py (network scan),
range_host_audit.py (local SSH enumeration), range_poc.py and
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

Per-host lifecycle this engine tracks. Two revisions from the original
design, both found necessary from a real 21-host run:

(1) `remediation_planned` (a report-remediate call where `applied` came
back false) now gets real follow-up instead of being silently skipped
forever: `next`/`next-batch` keep re-offering it as `assign_remediate`
(a retry) up to MAX_REMEDIATION_ATTEMPTS, after which — or immediately,
if the agent reported `needs_operator_decision: true` — it's surfaced
as a non-blocking review item instead of just vanishing.

(2) A finding with no further automatic path (`reverify_failed`, or
`remediation_planned` past its retry cap) no longer blocks the REST OF
THE SWEEP. The original design walked one host's findings to a terminal
status (or to review_needed) before ever issuing the next host's
clone_and_scan — which meant a single stuck finding on host 3 would
permanently halt hosts 4 through 21, discovered only because sequential
processing happened to get 25/25 reverifies right so far, by luck, not
by design. `next`/`next-batch` now collect review-needed findings
separately and keep advancing through every other host's real work;
review items are reported once nothing else is left to do, not instead
of it. `resolve-review` is the only way to move a finding off
`reverify_failed`/stalled-`remediation_planned` — same principle
AGENTS.md's standing rules state for `.ctf_traversal_state.json`:
nothing but this script's own commands touches a tracked status.

    network_scan_status: not_started -> scanned (or scan_failed)
    local_audit_status:  not_started -> audited (or audit_failed)
                                         (only attempted once network-scanned)

    per finding:
        (an inventory-category audit item starts `informational` — context
         only, no PoC — until `escalate` promotes it to `discovered`;
         a risk-category item and every network-scan finding start
         `discovered` directly)

        informational --escalate--> discovered
        discovered -> poc_confirmed -> remediation_planned -> remediation_applied -> reverified_closed
                   |-> poc_not_reachable           (retried in place, up to       |-> reverify_failed
                   |-> poc_inconclusive              MAX_REMEDIATION_ATTEMPTS)          |
                                                                              resolve-review
                                                                                  |-> risk_accepted
                                                                                  |-> closed_manually
                                                                                  |-> back to poc_confirmed /
                                                                                      remediation_planned (retry)

LEASING, for concurrent/delegated use: every action `next`/`next-batch`
hands out is claimed (in state["claims"], keyed by "<vmid>:host" for
the two per-host prerequisite actions or "<vmid>:<finding_id>" for a
finding-level one) so a second concurrent caller is offered something
else instead of colliding on the same work. A claim expires after
ACTION_LEASE_TIMEOUT_SECONDS if nothing ever reports back (a dead
subagent, a crashed session) — or release it explicitly with
`release-claim`. Reporting a result (`record-scan`, `record-audit`,
`report-poc`, `report-remediate`) always clears its own claim as part
of recording, whether or not it was actually this caller who held it.

Usage:
    python3 range_traversal.py init --config <hosts.json>
    python3 range_traversal.py next
    python3 range_traversal.py next-batch --count <n> --claimant <id>
    python3 range_traversal.py release-claim --vmid <golden_vmid> [--finding <finding_id>]
    python3 range_traversal.py record-scan --vmid <golden_vmid> --working-vmid <id> \
        --target <ip> --discover <path to range_discover.py>
    python3 range_traversal.py record-audit --vmid <golden_vmid> --working-vmid <id> \
        --target <ip> --audit <path to range_host_audit.py>
    python3 range_traversal.py assign-poc --vmid <golden_vmid> --finding <finding_id> \
        --poc-script <path to range_poc.py> [--reverify]
    python3 range_traversal.py report-poc --vmid <golden_vmid> --finding <finding_id> \
        --poc-script <path to range_poc.py> --result-file <path> [--reverify]
    python3 range_traversal.py assign-remediate --vmid <golden_vmid> --finding <finding_id> \
        --remediate-script <path to range_remediate.py> [--dry-run]
    python3 range_traversal.py report-remediate --vmid <golden_vmid> --finding <finding_id> \
        --remediate-script <path to range_remediate.py> --result-file <path>
    python3 range_traversal.py resolve-review --vmid <golden_vmid> --finding <finding_id> \
        --resolution accept_risk|retry|closed_manually [--note <text>]
    python3 range_traversal.py escalate --vmid <golden_vmid> --finding <finding_id> [--reason <text>]
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
import time

# Normally "/workspace" — the real mount point inside the hermes sandbox,
# where every script in this skill actually runs. RANGE_WORKSPACE lets a
# READ-ONLY command (status/next) be invoked safely from OUTSIDE the
# sandbox too — e.g. by range-sweep-watchdog.sh on the host, to check
# whether a sweep has genuinely finished before deciding whether to
# relaunch — without that invocation silently reading/writing a real
# "/workspace" directory that happens to exist on the host's own
# filesystem root, which would be a correctness and safety hazard, not
# just a wrong-answer one. Unset (the sandbox's own case): behavior is
# unchanged from before this existed.
WORKSPACE_DIR = os.environ.get("RANGE_WORKSPACE", "/workspace")
STATE_PATH = os.path.join(WORKSPACE_DIR, ".range_traversal_state.json")
STATE_LOCK_PATH = STATE_PATH + ".lock"
CONTEXT_DIR = WORKSPACE_DIR

MAX_REMEDIATION_ATTEMPTS = 3
ACTION_LEASE_TIMEOUT_SECONDS = 1800  # 30 min; a dead claimant's work becomes reclaimable after this
VALID_RESOLUTIONS = {"accept_risk", "retry", "closed_manually"}


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
    if finding.get("item"):
        # Split finding (see range_host_audit.py's SPLITTERS) — the
        # item text itself is the specific, actionable thing, more
        # useful here than the shared category/command every sibling
        # finding also carries.
        return f"{finding.get('category')}: {finding['item'][:200]}"
    return finding.get("description") or f"{finding.get('category')}: {finding.get('command')}"


def context_path(vmid: str, finding_id_: str, kind: str) -> str:
    # vmid-scoped on purpose: finding_id is a hash of (scope, port, proto,
    # service) or (scope, category, command) ALONE — never host or vmid —
    # so two unrelated findings on different hosts can share the identical
    # ID (confirmed for real: 9102's benign nginx redirect and 9103's
    # critical hr-news RCE both landed f-3b4c7dfc21). Before this fix, this
    # filename had no vmid in it at all, so whichever host's assign-poc/
    # assign-remediate ran LAST silently overwrote the other's context —
    # confirmed for real when 9102's nginx context was clobbered by 9103's
    # Apache one on disk, during the same real 3-host run that surfaced the
    # collision in the first place.
    return os.path.join(CONTEXT_DIR, f".range_context_{vmid}_{finding_id_}_{kind}.json")


def write_context(path: str, data: dict):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def cmd_init(config_path: str, hosts_filter: str = None, fresh: bool = False):
    if not os.path.isfile(config_path):
        print(json.dumps({"initialized": False, "reason": f"--config path does not exist: {config_path!r}"}, indent=2))
        sys.exit(1)
    with open(config_path) as f:
        cfg = json.load(f)

    # --hosts lets you scope a run to a subset of --config's hosts without
    # maintaining a separate trimmed config file. Found necessary for real:
    # a 3-host exercise was pointed at a genuinely trimmed 3-host config
    # file, but the state file still held an old 21-host sweep's state —
    # see the --fresh/stale-host guard below for why that no longer
    # silently happens either way.
    requested_vmids = None
    if hosts_filter:
        requested_vmids = [v.strip() for v in hosts_filter.split(",") if v.strip()]
        missing = [v for v in requested_vmids if v not in cfg.get("hosts", {})]
        if missing:
            print(json.dumps({
                "initialized": False,
                "reason": f"--hosts referenced vmid(s) not present in --config's hosts: {missing}",
            }, indent=2))
            sys.exit(1)
        hosts_to_seed = {v: cfg["hosts"][v] for v in requested_vmids}
    else:
        hosts_to_seed = cfg.get("hosts", {})

    with state_lock():
        existing_state = load_state() if os.path.isfile(STATE_PATH) else None
        existing_vmids = set(existing_state["hosts"].keys()) if existing_state else set()
        # init has always been additive-only by design (setdefault everywhere
        # below) so that re-running it mid-sweep to add a host never clobbers
        # progress on the others. That same design silently let a SMALLER,
        # differently-scoped run (e.g. --hosts/a trimmed --config meant to
        # limit a sweep to 3 hosts) inherit a stale LARGER state file's hosts
        # with zero indication it had happened — confirmed for real when a
        # 3-host exercise came back covering all 21 hosts from an old sweep's
        # state file that had never actually been cleared. Refuse instead of
        # silently merging whenever this init's host set doesn't match what's
        # already on disk; --fresh is the explicit opt-in to wipe and replace.
        stale_vmids = sorted(existing_vmids - set(hosts_to_seed.keys()))
        if stale_vmids and not fresh:
            print(json.dumps({
                "initialized": False,
                "reason": (
                    f"Existing state already has {len(existing_vmids)} host(s), including "
                    f"{len(stale_vmids)} NOT in this init's host set. init never truncates "
                    "state on its own, so proceeding would silently pull those stale hosts "
                    "into scope alongside whatever you're seeding now. Pass --fresh to wipe "
                    "the existing state and start clean with exactly this host set, or "
                    "re-run init with the full/matching host set if you actually meant to "
                    "resume the existing, larger sweep."
                ),
                "existing_hosts": sorted(existing_vmids),
                "stale_hosts": stale_vmids,
            }, indent=2))
            sys.exit(1)

        state = {"config_path": config_path, "hosts": {}} if fresh else (
            existing_state if existing_state is not None else {"config_path": config_path, "hosts": {}}
        )
        state["config_path"] = config_path
        for vmid, host_cfg in hosts_to_seed.items():
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
        "fresh": fresh,
        "hosts_filtered_from_config": requested_vmids is not None,
        "note": "Each host still needs its working clone (<host>-ctf) created and started "
                "via proxmox_bridge.py's /vm/clone and /vm/{vmid}/start BEFORE record-scan "
                "can run against it — that lifecycle step is intentionally outside this "
                "script, same division of labor as ctf_traversal.py leaving flag derivation "
                "to the agent.",
    }, indent=2))


def host_action_list(vmid: str, rec: dict):
    """Returns (actions, review) for ONE host: `actions` is every
    currently-available action for this host (NOT just the first —
    once scan+audit are both done, several of a host's findings can be
    independently actionable at once, which is what lets next-batch
    hand out real concurrent work within a single host, not just
    across hosts). `review` is any finding that has no further
    automatic action (reverify_failed, or remediation_planned past its
    retry cap / explicitly flagged needs_operator_decision) — these are
    NOT included in `actions`: they never block this host's other
    findings, and they never block cmd_next from moving on to another
    host. Each action/review item carries a "key" used for leasing;
    callers must strip it before printing to the agent."""
    actions, review = [], []

    if rec["network_scan_status"] == "not_started":
        actions.append({
            "key": f"{vmid}:host", "vmid": vmid, "host": rec["host"], "action": "clone_and_scan",
            "note": f"Clone+start {rec['host']}-golden (vmid {vmid}) as {rec['host']}-ctf via "
                    "proxmox_bridge.py, then call range_discover.py against its address and "
                    "feed the result to `record-scan`.",
        })
        return actions, review  # nothing else is possible for this host yet
    if rec["network_scan_status"] == "scanned" and rec["local_audit_status"] == "not_started":
        actions.append({
            "key": f"{vmid}:host", "vmid": vmid, "host": rec["host"], "working_vmid": rec["working_vmid"],
            "target": rec["last_scan_target"] or rec["ipv4"], "action": "local_audit",
            "note": "Call range_host_audit.py against the working clone over SSH (root/Administrator) "
                    "and feed the result to `record-audit`.",
        })
        return actions, review

    for fid, finding in rec["findings"].items():
        status = finding["status"]
        key = f"{vmid}:{fid}"
        if status == "discovered":
            actions.append({
                "key": key, "vmid": vmid, "host": rec["host"], "working_vmid": rec["working_vmid"],
                "finding_id": fid, "scope": finding["scope"], "action": "assign_poc",
                "description": finding_description(finding),
            })
        elif status == "poc_confirmed":
            actions.append({
                "key": key, "vmid": vmid, "host": rec["host"], "working_vmid": rec["working_vmid"],
                "finding_id": fid, "action": "assign_remediate",
                "description": finding_description(finding),
            })
        elif status == "remediation_planned":
            if finding.get("needs_operator_decision") or \
               finding.get("remediation_attempts", 0) >= MAX_REMEDIATION_ATTEMPTS:
                review.append({
                    "key": key, "vmid": vmid, "host": rec["host"], "finding_id": fid, "action": "review_needed",
                    "note": "Remediation was attempted but never came back `applied: true` — "
                            + ("the agent explicitly flagged needs_operator_decision. "
                               if finding.get("needs_operator_decision")
                               else f"retried {finding.get('remediation_attempts', 0)} time(s), at the cap. ")
                            + "Read `status` for remediation_summary/risk_notes, then call "
                              "`resolve-review` once a human/operator has decided what to do with it.",
                })
            else:
                actions.append({
                    "key": key, "vmid": vmid, "host": rec["host"], "working_vmid": rec["working_vmid"],
                    "finding_id": fid, "action": "assign_remediate", "retry": True,
                    "description": finding_description(finding),
                    "note": f"Retry {finding.get('remediation_attempts', 0) + 1}/{MAX_REMEDIATION_ATTEMPTS} "
                            "— the prior attempt reported applied: false.",
                })
        elif status == "remediation_applied":
            actions.append({
                "key": key, "vmid": vmid, "host": rec["host"], "working_vmid": rec["working_vmid"],
                "finding_id": fid, "scope": finding["scope"], "action": "assign_reverify",
                "description": finding_description(finding),
            })
        elif status == "reverify_failed":
            review.append({
                "key": key, "vmid": vmid, "host": rec["host"], "finding_id": fid, "action": "review_needed",
                "note": "Remediation did not close this finding on reverify — read `status` for its "
                        "remediation_summary/evidence, then call `resolve-review` once a human/operator "
                        "has decided what to do with it. This does NOT block this host's other findings "
                        "or any other host.",
            })
        # else: nothing to do. This covers the terminal statuses
        # (poc_not_reachable, poc_inconclusive, reverified_closed,
        # risk_accepted, closed_manually) AND `informational` — an
        # inventory item recorded for context that never enters the PoC
        # cycle on its own. `escalate` is the only thing that moves an
        # informational finding to `discovered`; until then it correctly
        # produces no action here and doesn't hold up completion.
    return actions, review


def all_actions(state: dict):
    actions, review = [], []
    for vmid, rec in state["hosts"].items():
        a, r = host_action_list(vmid, rec)
        actions.extend(a)
        review.extend(r)
    return actions, review


def _claim_active(state: dict, key: str, now: float) -> bool:
    claim = state.get("claims", {}).get(key)
    return bool(claim) and (now - claim["claimed_at"]) < ACTION_LEASE_TIMEOUT_SECONDS


def _claim(state: dict, key: str, claimant: str, now: float):
    state.setdefault("claims", {})[key] = {"claimant": claimant, "claimed_at": now}


def _clear_claim(state: dict, key: str):
    state.get("claims", {}).pop(key, None)


def _strip_key(action: dict) -> dict:
    return {k: v for k, v in action.items() if k != "key"}


def cmd_is_done():
    """Read-only completion check: same `all_actions` logic `next` uses to
    decide whether anything is left to do, but NEVER claims an action or
    writes state — unlike `next`, safe to call speculatively, any number
    of times, with no risk of colliding with a real in-progress claim.
    Exists specifically for range-sweep-watchdog.sh (run with RANGE_WORKSPACE
    set, from outside the sandbox) to check whether a sweep has genuinely
    finished before deciding whether to relaunch — confirmed for real that
    without this, the watchdog kept relaunching a finished sweep (exit code
    0, `done_pending_review: true`, clones already destroyed) exactly like
    a crash, because it had no way to tell the two apart."""
    now = time.time()
    state = load_state()
    actions, review = all_actions(state)
    pending = [a for a in actions if not _claim_active(state, a["key"], now)]
    done = not pending and not review
    done_pending_review = not pending and bool(review)
    print(json.dumps({
        "done": done,
        "done_pending_review": done_pending_review,
        "pending_actions": len(pending),
        "review_needed": [_strip_key(r) for r in review] if done_pending_review else [],
    }, indent=2))


def cmd_next():
    now = time.time()
    with state_lock():
        state = load_state()
        actions, review = all_actions(state)
        for action in actions:
            if _claim_active(state, action["key"], now):
                continue
            _claim(state, action["key"], "primary", now)
            save_state(state)
            print(json.dumps(_strip_key(action), indent=2))
            return
        save_state(state)
    if review:
        print(json.dumps({
            "done_pending_review": True,
            "review_needed": [_strip_key(r) for r in review],
            "summary": summarize(state),
        }, indent=2))
    else:
        print(json.dumps({"done": True, "summary": summarize(state)}, indent=2))


def cmd_next_batch(count: int, claimant: str):
    now = time.time()
    claimed = []
    with state_lock():
        state = load_state()
        actions, review = all_actions(state)
        for action in actions:
            if len(claimed) >= count:
                break
            if _claim_active(state, action["key"], now):
                continue
            _claim(state, action["key"], claimant, now)
            claimed.append(_strip_key(action))
        save_state(state)
    print(json.dumps({
        "claimed": claimed, "count": len(claimed),
        "review_needed": [_strip_key(r) for r in review],
    }, indent=2))


def cmd_release_claim(vmid: str, fid: str | None):
    key = f"{vmid}:{fid}" if fid else f"{vmid}:host"
    with state_lock():
        state = load_state()
        existed = key in state.get("claims", {})
        _clear_claim(state, key)
        save_state(state)
    print(json.dumps({"released": True, "key": key, "existed": existed}, indent=2))


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
            _clear_claim(state, f"{vmid}:host")
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
        _clear_claim(state, f"{vmid}:host")
        save_state(state)

    print(json.dumps({"recorded": True, "reachable": True, "findings_total": len(rec["findings"])}, indent=2))


def cmd_record_audit(vmid: str, working_vmid: str, target: str, audit_script: str, timeout: int):
    with state_lock():
        state = load_state()
        if vmid not in state["hosts"]:
            print(json.dumps({"recorded": False, "reason": f"vmid {vmid} not in state — run init first"}, indent=2))
            sys.exit(1)
        rec = state["hosts"][vmid]
        rec["working_vmid"] = working_vmid

        audit_args = [
            "audit", "--golden-vmid", vmid, "--working-vmid", working_vmid, "--os", rec["os"],
            "--target", target, "--timeout", str(timeout),
        ]
        audit_result = run_script(audit_script, audit_args)

        if audit_result.get("_error") or not audit_result.get("reachable", False):
            rec["local_audit_status"] = "audit_failed"
            _clear_claim(state, f"{vmid}:host")
            save_state(state)
            print(json.dumps({"recorded": True, "reachable": False, "audit_result": audit_result}, indent=2))
            return

        rec["local_audit_status"] = "audited"
        new_findings = 0
        for f in audit_result.get("findings", []):
            # f.get("item") disambiguates a split finding (see
            # range_host_audit.py's SPLITTERS) from its siblings in the
            # same category/command — without it, every item split out
            # of one bundled check would hash to the SAME finding_id and
            # collapse back into one finding, defeating the split.
            fid = finding_id("local", f["category"], f["command"], f.get("item") or "")
            if fid not in rec["findings"]:
                # kind comes from range_host_audit.py's RISK_CATEGORIES.
                # A "risk" item enters the PoC cycle (`discovered`); an
                # "inventory" item is recorded as `informational` — kept
                # and searchable, but never auto-assigned a PoC. Default
                # to "risk" if an older audit script didn't send a kind,
                # so behavior only changes once the tagging audit is
                # deployed (no silent reclassification of an in-flight
                # sweep's existing findings — those keep whatever status
                # they already have). The agent can promote a specific
                # informational item with `escalate`.
                kind = f.get("kind", "risk")
                initial_status = "discovered" if kind == "risk" else "informational"
                rec["findings"][fid] = {
                    "scope": "local",
                    "category": f["category"], "command": f["command"], "item": f.get("item"),
                    "kind": kind,
                    "output": f.get("output", ""), "description": f.get("description", ""),
                    "status": initial_status, "poc_evidence": "", "poc_method": "", "remediation_summary": "",
                }
                new_findings += 1
        _clear_claim(state, f"{vmid}:host")
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
                    "output": finding["output"], "description": finding["description"],
                    "target": rec["last_scan_target"] or rec["ipv4"]}
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
    ctx_path = context_path(vmid, fid, "poc")
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
        _clear_claim(state, f"{vmid}:{fid}")
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

    if finding["status"] not in ("poc_confirmed", "remediation_planned"):
        print(json.dumps({
            "assigned": False,
            "reason": f"finding {fid} has status '{finding['status']}', expected 'poc_confirmed' "
                      "(first attempt) or 'remediation_planned' (retry after applied: false).",
        }, indent=2))
        sys.exit(1)

    context = {
        "scope": finding["scope"], "finding_description": finding_description(finding),
        "target": rec["last_scan_target"] or rec["ipv4"],
        "poc_evidence": finding["poc_evidence"], "poc_method": finding["poc_method"],
    }
    ctx_path = context_path(vmid, fid, "remediate")
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
        # Previously discarded entirely — needs_operator_decision/risk_notes
        # are exactly what host_action_list() now needs to tell a genuine
        # "operator must decide" outcome apart from a plain retry, and
        # what resolve-review's caller needs to read before deciding.
        finding["needs_operator_decision"] = bool(result.get("needs_operator_decision", False))
        finding["risk_notes"] = result.get("risk_notes", finding.get("risk_notes", ""))
        finding["remediation_attempts"] = (
            finding.get("remediation_attempts", 0) + 1 if status == "remediation_planned" else 0
        )
        _clear_claim(state, f"{vmid}:{fid}")
        save_state(state)

    print(json.dumps({
        "vmid": vmid, "finding_id": fid, "status": status,
        "remediation_attempts": finding["remediation_attempts"],
        "needs_operator_decision": finding["needs_operator_decision"],
    }, indent=2))


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


def cmd_escalate(vmid: str, fid: str, reason: str):
    """Promote an `informational` (inventory) finding into the PoC cycle,
    i.e. set it to `discovered` so `next` will offer it as `assign_poc`.
    This is the deliberate escape hatch for the inventory/risk split:
    most inventory (a running service, an open port, an installed
    package) is context, not a finding to exploit — but when the agent
    spots a specific inventory item that genuinely looks exploitable (a
    kernel at a known-vulnerable version, a service whose version has a
    public RCE), this is how it opts that ONE item in, with a recorded
    reason, instead of every inventory item being tested by default.
    Only `informational` can be escalated; anything already in the PoC
    cycle or terminal is left alone."""
    with state_lock():
        state = load_state()
        if vmid not in state["hosts"] or fid not in state["hosts"][vmid]["findings"]:
            print(json.dumps({"escalated": False, "reason": f"vmid {vmid} / finding {fid} not found in state"}, indent=2))
            sys.exit(1)
        finding = state["hosts"][vmid]["findings"][fid]
        if finding["status"] != "informational":
            print(json.dumps({
                "escalated": False,
                "reason": f"finding {fid} has status '{finding['status']}', not 'informational' — "
                          "escalate only promotes an inventory item into the PoC cycle; it is not "
                          "for re-opening a finding that's already been through it.",
            }, indent=2))
            sys.exit(1)
        finding["status"] = "discovered"
        finding.setdefault("escalation_log", []).append({
            "reason": reason or "(no reason given)", "escalated_at": time.time(),
        })
        save_state(state)
    print(json.dumps({
        "escalated": True, "vmid": vmid, "finding_id": fid,
        "new_status": "discovered",
        "note": "`next` will now offer this as assign_poc like any other discovered finding.",
    }, indent=2))


def cmd_resolve_review(vmid: str, fid: str, resolution: str, note: str):
    """The only sanctioned way to move a finding off `reverify_failed` or
    a `remediation_planned` stuck past its retry cap — same principle
    AGENTS.md's standing rules already state for .ctf_traversal_state.json:
    nothing but this script's own commands touches a tracked status.
    `accept_risk`/`closed_manually` are real terminal outcomes (an
    operator judgment call, or a fix applied by hand outside this
    tool) — NOT silent abandonment; they're recorded in the host's own
    review_log, same spirit as the closing-note requirement AGENTS.md
    already imposes for an exhausted CTF problem. `retry` resets the
    attempt counter and sends it back for a fresh remediation pass."""
    if resolution not in VALID_RESOLUTIONS:
        print(json.dumps({
            "resolved": False,
            "reason": f"--resolution must be one of {sorted(VALID_RESOLUTIONS)}, got {resolution!r}",
        }, indent=2))
        sys.exit(1)

    with state_lock():
        state = load_state()
        if vmid not in state["hosts"] or fid not in state["hosts"][vmid]["findings"]:
            print(json.dumps({"resolved": False, "reason": f"vmid {vmid} / finding {fid} not found in state"}, indent=2))
            sys.exit(1)
        rec = state["hosts"][vmid]
        finding = rec["findings"][fid]
        prior_status = finding["status"]

        is_stuck_remediation_planned = (
            prior_status == "remediation_planned"
            and (finding.get("needs_operator_decision") or finding.get("remediation_attempts", 0) >= MAX_REMEDIATION_ATTEMPTS)
        )
        if prior_status != "reverify_failed" and not is_stuck_remediation_planned:
            print(json.dumps({
                "resolved": False,
                "reason": f"finding {fid} has status '{prior_status}', which isn't currently "
                          "flagged for review (reverify_failed, or remediation_planned past its "
                          "retry cap / flagged needs_operator_decision) — nothing to resolve.",
            }, indent=2))
            sys.exit(1)

        rec.setdefault("review_log", []).append({
            "finding_id": fid, "prior_status": prior_status, "resolution": resolution,
            "note": note or "", "resolved_at": time.time(),
        })
        if resolution == "accept_risk":
            new_status = "risk_accepted"
        elif resolution == "closed_manually":
            new_status = "closed_manually"
        else:  # "retry"
            finding["remediation_attempts"] = 0
            # reverify_failed means the APPLIED remediation didn't hold —
            # retry from poc_confirmed so a fresh fix is proposed, not a
            # repeat of the same one. A stalled remediation_planned just
            # needs its attempt cap reset; it's already sitting where
            # host_action_list() will retry it on its own.
            new_status = "poc_confirmed" if prior_status == "reverify_failed" else "remediation_planned"
        finding["status"] = new_status
        # The operator decision is now made, whichever way it went —
        # accept_risk and closed_manually are just as final as retry's
        # reset. Leaving this true after a terminal resolution meant a
        # resolved finding kept showing up in any needs_operator_decision
        # filter forever, as if it were still waiting on you.
        finding["needs_operator_decision"] = False
        finding["operator_note"] = note or finding.get("operator_note", "")
        _clear_claim(state, f"{vmid}:{fid}")
        save_state(state)

    print(json.dumps({
        "resolved": True, "vmid": vmid, "finding_id": fid,
        "prior_status": prior_status, "new_status": new_status,
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
    p_init.add_argument("--hosts", default=None,
                         help="comma-separated subset of --config's vmids to seed, e.g. "
                              "9101,9102,9103 — omit to use every host in --config")
    p_init.add_argument("--fresh", action="store_true",
                         help="wipe any existing state and start clean with exactly this "
                              "init's host set. Required if existing state already has "
                              "host(s) outside that set — init refuses to silently merge "
                              "a smaller/different run onto a stale larger state file")

    sub.add_parser("next")
    sub.add_parser("is-done")

    p_nb = sub.add_parser("next-batch")
    p_nb.add_argument("--count", type=int, required=True, help="max number of actions to claim")
    p_nb.add_argument("--claimant", required=True, help="identifier for whoever's claiming these, e.g. a subagent id")

    p_rc = sub.add_parser("release-claim")
    p_rc.add_argument("--vmid", required=True, help="the golden VMID (hosts.json key)")
    p_rc.add_argument("--finding", default=None, help="omit to release a host-level claim (clone_and_scan/local_audit)")

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
    p_ra.add_argument("--target", required=True, help="working clone's IPv4/IPv6 address")
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

    p_res = sub.add_parser("resolve-review")
    p_res.add_argument("--vmid", required=True, help="the golden VMID (hosts.json key)")
    p_res.add_argument("--finding", required=True)
    p_res.add_argument("--resolution", required=True, choices=sorted(VALID_RESOLUTIONS))
    p_res.add_argument("--note", default="", help="operator's reasoning — recorded in the host's review_log")

    p_esc = sub.add_parser("escalate")
    p_esc.add_argument("--vmid", required=True, help="the golden VMID (hosts.json key)")
    p_esc.add_argument("--finding", required=True)
    p_esc.add_argument("--reason", default="",
                       help="why this inventory item is worth a PoC after all (e.g. "
                            "'kernel 5.4.0-x matches CVE-xxxx, worth attempting') — recorded "
                            "in the finding's escalation_log")

    sub.add_parser("status")

    p_rh = sub.add_parser("reset-host")
    p_rh.add_argument("--vmid", required=True, help="the golden VMID (hosts.json key)")
    p_rh.add_argument("--reason", default="",
                       help="why this host is being reset, e.g. 'working clone manually "
                            "destroyed in Proxmox outside the sanctioned teardown' — recorded "
                            "in state's reset_history for an audit trail")

    args = parser.parse_args()
    if args.command == "init":
        cmd_init(args.config, args.hosts, args.fresh)
    elif args.command == "next":
        cmd_next()
    elif args.command == "is-done":
        cmd_is_done()
    elif args.command == "next-batch":
        cmd_next_batch(args.count, args.claimant)
    elif args.command == "release-claim":
        cmd_release_claim(args.vmid, args.finding)
    elif args.command == "record-scan":
        cmd_record_scan(args.vmid, args.working_vmid, args.target, args.discover, args.nmap_path, args.ports)
    elif args.command == "record-audit":
        cmd_record_audit(args.vmid, args.working_vmid, args.target, args.audit, args.timeout)
    elif args.command == "assign-poc":
        cmd_assign_poc(args.vmid, args.finding, args.poc_script, args.max_attempts, args.time_budget_minutes, args.reverify)
    elif args.command == "report-poc":
        cmd_report_poc(args.vmid, args.finding, args.poc_script, args.result_file, args.reverify)
    elif args.command == "assign-remediate":
        cmd_assign_remediate(args.vmid, args.finding, args.remediate_script, args.dry_run)
    elif args.command == "report-remediate":
        cmd_report_remediate(args.vmid, args.finding, args.remediate_script, args.result_file)
    elif args.command == "resolve-review":
        cmd_resolve_review(args.vmid, args.finding, args.resolution, args.note)
    elif args.command == "escalate":
        cmd_escalate(args.vmid, args.finding, args.reason)
    elif args.command == "status":
        cmd_status()
    elif args.command == "reset-host":
        cmd_reset_host(args.vmid, args.reason)


if __name__ == "__main__":
    main()
