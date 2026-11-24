#!/usr/bin/env python3
"""
range-teardown.py — destroy the working clones of range hosts that are
completely finished, through the bridge.

WHY: the agent is supposed to destroy each host's working clone as soon as
all of that host's findings are finished (range-assess SKILL.md, "Storage
capacity" rule 1). Confirmed on 2026-11-24 that it can miss this: its
teardowns had always happened right before it cloned the next host, and
once clones were pre-cloned for it (scripts/range-preclone.py) that cue
was gone — four finished hosts' clones (9612, 9707, 9708, 9709, ~270 GiB
provisioned) were left running after the sweep completed.
range-sweep-watchdog.sh runs this when a sweep completes GRACEFULLY
(range_traversal.py is-done confirms it). It never runs on Ctrl-C/kill:
an interrupted sweep keeps its clones for the resumed run.

A clone is destroyed only if ALL of these hold — otherwise it is kept and
listed, so you can decide:
  - its host is `scanned` + `audited` (a scan_failed/audit_failed host's
    clone may be worth inspecting);
  - every one of its findings is in a known-final status
    (poc_not_reachable, poc_inconclusive, reverified_closed, risk_accepted,
    closed_manually). Anything else — remediation_planned awaiting your
    decision, reverify_failed, reverify_inconclusive, or a status this
    script doesn't know — keeps the clone;
  - the bridge says the vmid exists, isn't mid-clone, and carries the
    expected name `<host>-ctf` (guards against a vmid that has since been
    reused for something else).
The bridge's own guards still apply on top (golden images can never be
deleted through it).

Usage:
    python3 scripts/range-teardown.py --workspace ~/hermes-agent-setup/workspace --dry-run
    python3 scripts/range-teardown.py --workspace ~/hermes-agent-setup/workspace

Read-only on the state file. Standard library only. Exit code 0 unless the
state file can't be read or the bridge can't be reached.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

FINAL_STATUSES = {"poc_not_reachable", "poc_inconclusive", "reverified_closed",
                  "risk_accepted", "closed_manually"}
DESTROY_HTTP_TIMEOUT = 600


def bridge(method: str, base: str, path: str, timeout: int = 60):
    req = urllib.request.Request(base.rstrip("/") + path, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw, code = resp.read().decode(), resp.status
    except urllib.error.HTTPError as e:
        raw, code = e.read().decode(errors="replace"), e.code
    try:
        return code, json.loads(raw)
    except json.JSONDecodeError:
        return code, raw


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--workspace", default=os.path.expanduser("~/hermes-agent-setup/workspace"))
    p.add_argument("--bridge", default="http://127.0.0.1:8811")
    p.add_argument("--dry-run", action="store_true", help="list what would be destroyed; destroy nothing")
    args = p.parse_args()

    try:
        with open(os.path.join(args.workspace, ".range_traversal_state.json")) as f:
            hosts = json.load(f)["hosts"]
    except (OSError, json.JSONDecodeError, KeyError) as e:
        print(f"teardown: cannot read state file: {e}")
        return 1

    destroyed, kept = [], []
    try:
        for vmid, rec in hosts.items():
            wv = rec.get("working_vmid")
            if not wv:
                continue
            host = rec.get("host", "?")
            label = f"{host} ({vmid}) -> {wv}"

            code, st = bridge("GET", args.bridge, f"/vm/{wv}/status")
            if code != 200:
                kept.append(f"{label}: status check failed ({code}: {st})")
                continue
            if not st.get("exists"):
                continue  # already gone — the normal case
            if st.get("clone_in_progress") or st.get("lock"):
                kept.append(f"{label}: still being cloned / locked ({st.get('lock')})")
                continue
            if st.get("name") != f"{host}-ctf":
                kept.append(f"{label}: name is {st.get('name')!r}, expected '{host}-ctf' — not touching it")
                continue
            if rec.get("network_scan_status") != "scanned" or rec.get("local_audit_status") != "audited":
                kept.append(f"{label}: host is {rec.get('network_scan_status')}/{rec.get('local_audit_status')}"
                            " — kept for inspection")
                continue
            open_statuses = sorted({f.get("status") for f in rec.get("findings", {}).values()}
                                   - FINAL_STATUSES)
            if open_statuses:
                kept.append(f"{label}: findings still {', '.join(map(str, open_statuses))} — kept for you")
                continue

            if args.dry_run:
                destroyed.append(f"{label} [dry-run: would destroy]")
                continue
            code, res = bridge("DELETE", args.bridge, f"/vm/{wv}", timeout=DESTROY_HTTP_TIMEOUT)
            if code == 200:
                destroyed.append(label)
            else:
                detail = res.get("detail") if isinstance(res, dict) else res
                kept.append(f"{label}: DELETE failed ({code}: {detail})")
    except (urllib.error.URLError, OSError) as e:
        print(f"teardown: bridge unreachable ({e}) — nothing more done")
        return 1

    print(f"teardown: destroyed {len(destroyed)}, kept {len(kept)}")
    for line in destroyed:
        print(f"  destroyed: {line}")
    for line in kept:
        print(f"  KEPT:      {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
