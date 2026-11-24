#!/usr/bin/env python3
"""
range-preclone.py — keep the NEXT range host's working clone ready while
the agent is still busy with the current one.

WHY: every host's working clone is a full copy off the 5400 RPM SMR disk
(`vmdata`) onto `hot-ssd` — ~2.5 min for a Linux host, 16-33 min for a
Windows one — and the agent normally only starts that copy after finishing
the previous host, so the whole copy sits in the critical path. Confirmed
on 2026-11-24 that a clone which already exists is handled well: the agent
found the pre-existing 9707 (ready_to_start), started it and skipped the
clone entirely. This script makes that the normal case.

WHAT IT DOES, every --interval seconds:
  1. Reads /workspace's .range_traversal_state.json — READ-ONLY, never
     writes it, never claims anything.
  2. Candidates = hosts still `network_scan_status: not_started`, in state
     (= hosts.json) order — the order `next` hands them out. Working vmid =
     golden vmid + --offset (500: 9110 -> 9610, 9207 -> 9707).
  3. If ANY candidate's working clone already exists (pre-cloned earlier,
     or cloned by the agent and not yet recorded), there's already a clone
     waiting: do nothing. At most ONE waiting clone, so it never hoards
     `hot-ssd` space.
  4. Otherwise take the first candidate the agent hasn't claimed, check
     capacity (bridge GET /storage/<storage>/status?template_vmid=...), and
     clone it through the bridge's POST /vm/clone — so the golden-MAC
     restore, the `pristine` snapshot, the capacity limits and the
     one-clone-at-a-time rule all apply exactly as for the agent's own
     clones. The clone is left STOPPED; starting it is the agent's job.

WHAT IT NEVER DOES: start, stop, delete, or roll back anything; write
state; clone a host the agent has claimed; run a second clone while one is
in flight (the bridge refuses with 409, and this script just tries again
next round). Stop it any time with Ctrl-C — the sweep carries on exactly
as before. If Ctrl-C lands mid-clone, the bridge finishes that clone anyway
and it simply waits, stopped, like any other pre-clone.

Exits when no `not_started` hosts are left.

Usage (on the Mac, alongside the bridge and the watchdog):
    python3 scripts/range-preclone.py --workspace ~/hermes-agent-setup/workspace
    python3 scripts/range-preclone.py --workspace ... --once --dry-run   # show what it would do

Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

CLAIM_LEASE_SECONDS = 1800  # must match range_traversal.py's ACTION_LEASE_TIMEOUT_SECONDS
CLONE_HTTP_TIMEOUT = 7500   # bridge waits up to 7200s on a clone task


def log(msg: str) -> None:
    print(f"{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S%z')} {msg}", flush=True)


def bridge(method: str, base: str, path: str, body: dict | None = None, timeout: int = 60):
    """Returns (status_code, parsed_json_or_text). Never raises on HTTP errors."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base.rstrip("/") + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            code = resp.status
    except urllib.error.HTTPError as e:
        raw, code = e.read().decode(errors="replace"), e.code
    try:
        return code, json.loads(raw)
    except json.JSONDecodeError:
        return code, raw


def detail(payload) -> str:
    if isinstance(payload, dict):
        return str(payload.get("detail") or payload.get("reason") or payload)
    return str(payload)


def load_state(workspace: str) -> dict | None:
    path = os.path.join(workspace, ".range_traversal_state.json")
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        # Caught mid-write by range_traversal.py — just try again next round.
        return None


def one_round(args) -> str:
    """Runs one decision round. Returns 'done' when nothing is left to do."""
    state = load_state(args.workspace)
    if state is None:
        log("state file not readable yet — waiting")
        return "wait"

    now = time.time()
    claims = state.get("claims", {})
    candidates = [
        (vmid, rec) for vmid, rec in state["hosts"].items()
        if isinstance(rec, dict) and rec.get("network_scan_status") == "not_started"
    ]
    if not candidates:
        log("no not_started hosts left — nothing more to pre-clone, exiting")
        return "done"

    # One waiting clone at most: if any candidate's working clone exists, stop here.
    for vmid, rec in candidates:
        wv = int(vmid) + args.offset
        code, st = bridge("GET", args.bridge, f"/vm/{wv}/status")
        if code != 200:
            log(f"bridge status check for {wv} failed ({code}: {detail(st)}) — waiting")
            return "wait"
        if st.get("exists"):
            what = "being cloned" if st.get("clone_in_progress") or st.get("lock") else \
                   ("ready, waiting for the agent" if st.get("ready_to_start") else st.get("status"))
            log(f"{rec['host']} ({vmid}) already has working clone {wv} ({what}) — nothing to do")
            return "wait"

    # First candidate the agent hasn't claimed (a claimed host is the agent's to clone).
    for vmid, rec in candidates:
        claim = claims.get(f"{vmid}:host")
        if claim and now - claim.get("claimed_at", 0) < CLAIM_LEASE_SECONDS:
            log(f"{rec['host']} ({vmid}) is claimed by {claim.get('claimant')} — leaving it to the agent")
            continue
        wv = int(vmid) + args.offset
        code, cap = bridge("GET", args.bridge, f"/storage/{args.storage}/status?template_vmid={vmid}")
        if code != 200:
            log(f"capacity check failed ({code}: {detail(cap)}) — waiting")
            return "wait"
        if not cap.get("ok_to_clone"):
            log(f"not pre-cloning {rec['host']} ({vmid}): {cap.get('reason')}")
            return "wait"
        name = f"{rec['host']}-ctf"
        if args.dry_run:
            log(f"[dry-run] would clone {vmid} -> {wv} '{name}' on {args.storage} "
                f"(pool {cap.get('used_percent')}% -> projected {cap.get('projected_percent')}%)")
            return "wait"
        log(f"pre-cloning {rec['host']}: {vmid} -> {wv} '{name}' on {args.storage} "
            f"(pool {cap.get('used_percent')}% -> projected {cap.get('projected_percent')}%)")
        started = time.time()
        code, res = bridge("POST", args.bridge, "/vm/clone",
                           {"template_vmid": int(vmid), "new_vmid": wv, "name": name,
                            "storage": args.storage},
                           timeout=CLONE_HTTP_TIMEOUT)
        mins = (time.time() - started) / 60
        if code == 200:
            log(f"pre-cloned {wv} in {mins:.1f} min (pristine snapshot: "
                f"{res.get('pristine_snapshot')}) — left STOPPED for the agent")
        elif code == 409:
            log(f"bridge busy, not cloning now: {detail(res)}")
        elif code == 507:
            log(f"bridge refused for capacity: {detail(res)}")
        else:
            log(f"clone of {wv} failed after {mins:.1f} min ({code}): {detail(res)} — "
                f"check it with GET /vm/{wv}/status before anything else")
        return "wait"

    log("every not_started host is claimed by the agent — nothing to pre-clone")
    return "wait"


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--workspace", default=os.path.expanduser("~/hermes-agent-setup/workspace"))
    p.add_argument("--bridge", default="http://127.0.0.1:8811")
    p.add_argument("--storage", default="hot-ssd")
    p.add_argument("--offset", type=int, default=500, help="working vmid = golden vmid + offset")
    p.add_argument("--interval", type=int, default=300, help="seconds between rounds")
    p.add_argument("--once", action="store_true", help="run a single round and exit")
    p.add_argument("--dry-run", action="store_true", help="report what would be cloned; clone nothing")
    args = p.parse_args()

    # range-sweep-watchdog.sh stops this with SIGTERM (a background job of a
    # non-interactive shell ignores SIGINT); treat it like Ctrl-C so the
    # stop gets logged the same way.
    def _on_term(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _on_term)

    log(f"range-preclone: workspace={args.workspace} bridge={args.bridge} storage={args.storage} "
        f"offset=+{args.offset} interval={args.interval}s{' DRY-RUN' if args.dry_run else ''}")
    try:
        while True:
            try:
                outcome = one_round(args)
            except (urllib.error.URLError, OSError) as e:
                log(f"bridge unreachable ({e}) — waiting")
                outcome = "wait"
            if outcome == "done" or args.once:
                return
            time.sleep(args.interval)
    except KeyboardInterrupt:
        log("stopped — any clone already sent to the bridge will still finish there")


if __name__ == "__main__":
    main()
