---
name: range-assess
description: Internal assessment loop for CTF-range hosts on Proxmox — scan (network + local), demonstrate reachable findings, remediate, reverify
version: 2.0.0
metadata:
  hermes:
    tags: [security, range, proxmox, assessment]
    category: security
---

This file is the rules you follow every turn. The *why* behind each rule
— the real incidents that produced it — lives in `REFERENCE.md` beside
this file; a pointer like "(REFERENCE.md: early-start-wrong-mac)" means
read that section only if you need the background. You do not need
`REFERENCE.md` in hand to run a normal turn.

## What this is, and what it is not

This runs the operator's own internal assessment loop against their own
isolated Proxmox range (host inventory in `config/hosts.json`; the
committed copy is a sanitized template, not a real range). For each host:
scan it over the network and from inside the guest, demonstrate which
findings are genuinely reachable/exploitable, plan and apply a
remediation, then re-run the same demonstration to confirm each finding
is closed.

- **No script here ever calls a model.** `range_traversal.py` is
  orchestration and bookkeeping; `range_poc.py`/`range_remediate.py` hand
  YOU a filled task prompt and validate the result you report back. "The
  local Qwen model does the PoC" means your own backend model acting
  through its own tool-calling turn — never a tool-less model call hidden
  in a script. (REFERENCE.md: no-hidden-model-call)
- **Golden images are never touched directly.** Every host in
  `config/hosts.json` is a golden VMID (`<host>-golden`, DO NOT
  MODIFY/DELETE). Every acting script takes a separate
  `--working-vmid`/working clone (`<host>-ctf`) and hard-refuses if it
  matches the golden VMID. Creating/destroying the working clone is
  `proxmox_bridge.py`'s job.
- **A host with any real-world safety dimension** (e.g. named after
  ICS/SCADA equipment) needs an explicit operator confirmation recorded
  in `hosts.json`'s per-host `note` field before it's treated as just
  another range host.

## The bridge (`proxmox_bridge.py`) — the endpoints you use

The bridge manages VM **lifecycle only**. These are all the endpoints you
need — call them directly; do NOT fetch `/openapi.json` or probe for other
paths:

- `POST /vm/clone` — clone a golden to its working vmid (golden + 500)
- `POST /vm/<vmid>/start` · `POST /vm/<vmid>/stop` · `DELETE /vm/<vmid>`
- `GET  /vm/<vmid>/status` — `exists`/`status`/`lock`/`clone_in_progress`/`ready_to_start`
- `GET  /storage/hot-ssd/status?template_vmid=<golden>` — capacity before a clone
- `POST /vm/<vmid>/snapshot` · `POST /vm/<vmid>/rollback?snapname=pristine&start=true`

A POST that carries a JSON body (`/vm/clone`, `/vm/<vmid>/snapshot`) **must**
send `-H 'Content-Type: application/json'` — without it the bridge rejects the
body with `422 "Input should be a valid dictionary"`. `start`/`stop`/`DELETE`
and `rollback` take no body (rollback uses query params), so they don't need it.

**The bridge runs NOTHING inside a guest — there is no guest-exec.** The
golden images have no QEMU guest agent, so `POST /vm/<vmid>/exec` always
returns `500 "No QEMU guest agent configured"`. Everything that runs a
command inside a host — both scans, the audit, every PoC and remediation —
goes over **SSH** (the sandbox's authorized keypair, as
`root`/`Administrator`). Never reach for a bridge exec call, and never grep
a script's source to re-learn this. (REFERENCE.md: ssh-not-guest-exec)

## Two scan phases, and which findings become work

Every host gets BOTH scans:

1. **Network scan** (`range_discover.py`) — nmap/port-connect from the
   agent's own network egress.
2. **Local host audit** (`range_host_audit.py`) — enumeration from inside
   the guest over SSH as `root`/`Administrator` using the `hermes`
   sandbox's authorized keypair: package/kernel versions, SUID/SGID,
   world-writable files/dirs, cron/sudoers, loopback-only sockets, and
   Windows equivalents. It connects as root, so it sees everything — the
   output is evidence a condition *exists*, not that it's been exploited.
   (REFERENCE.md: ssh-not-guest-exec)

**Risk vs. inventory.** The audit tags each finding `kind: "risk"` or
`kind: "inventory"` (see `RISK_CATEGORIES` in `range_host_audit.py`).
`record-audit` records a risk item as `discovered` (enters the PoC cycle)
and an inventory item as `informational` — kept and searchable, but never
auto-assigned a PoC. Inventory is context (a running service, an open
port, an account existing, an installed package), not a finding to
"exploit" or "fix." If you spot a specific inventory item that genuinely
looks exploitable (a kernel at a known-vulnerable version), opt that ONE
item in with a recorded reason:

```
python3 scripts/range_traversal.py escalate --vmid <golden_vmid> --finding <finding_id> --reason "<why>"
```

That flips it `informational -> discovered` so `next` offers it like any
other finding. Everything from either scan is tracked in
`range_traversal.py`'s state, tagged `scope: network|local`.

**What `reachable: true` requires.** The PoC prompt carries the full
standard; the short version: report `true` only when you DEMONSTRATED,
this turn, a realistic lower-privilege actor gaining access they
shouldn't have *through this specific finding*, with the proof in
`evidence`. A condition merely existing — a service running, a port
listening, an account present, a CVE-matching banner — is NOT `true`;
that's `false` (you showed no path) or `inconclusive` (you couldn't
tell).

## Usage — single host, one pass through the loop

```
# 1. Clone + start the working image (proxmox_bridge.py, not this skill).
#    Start ONLY after the clone call returns 200 — see "Cloning" below.
curl -X POST http://host.docker.internal:8811/vm/clone -H 'Content-Type: application/json' -d '{"template_vmid": 9001, "new_vmid": 1234, "name": "example-linux-host-ctf"}'
curl -X POST http://host.docker.internal:8811/vm/1234/start

# 2. Scan it — network, then local
python3 scripts/range_discover.py scan --target <clone ip>
python3 scripts/range_host_audit.py audit --golden-vmid 9001 --working-vmid 1234 --os Linux --target <clone ip>

# 3. Per finding, get the task prompt (does NOT call a model)
python3 scripts/range_poc.py assign --golden-vmid 9001 --working-vmid 1234 \
    --host example-linux-host --finding-id f-abc123 --scope network --context-file /workspace/ctx.json

# 4. YOU do the real work with YOUR OWN tools, write the result JSON, then report it:
python3 scripts/range_poc.py report --golden-vmid 9001 --working-vmid 1234 \
    --finding-id f-abc123 --result-file /workspace/result_f-abc123.json

# 5. Confirmed finding → remediate (same assign/do/report shape)
python3 scripts/range_remediate.py assign ...
python3 scripts/range_remediate.py report --result-file ...

# 6. Reverify — range_poc.py assign/report again, same finding-id
# 7. Stop + destroy the working image
curl -X POST http://host.docker.internal:8811/vm/1234/stop
curl -X DELETE http://host.docker.internal:8811/vm/1234
```

In the multi-host flow below, `range_traversal.py` writes each finding's
context file for you, so you never build one by hand.

## Usage — multi-host traversal (`range_traversal.py`)

It tracks per-host, per-finding state across however many turns a sweep
takes and tells you the single next thing to do. PoC and remediation are
two-phase (`assign` then `report`) because the real work between them is
yours.

```
python3 scripts/range_traversal.py init --config ../config/hosts.json [--hosts <vmids>] [--fresh]
python3 scripts/range_traversal.py next
python3 scripts/range_traversal.py record-scan  --vmid <g> --working-vmid <w> --target <ip> --discover scripts/range_discover.py
python3 scripts/range_traversal.py record-audit --vmid <g> --working-vmid <w> --target <ip> --audit scripts/range_host_audit.py
python3 scripts/range_traversal.py assign-poc   --vmid <g> --finding <fid> --poc-script scripts/range_poc.py [--reverify]
python3 scripts/range_traversal.py report-poc   --vmid <g> --finding <fid> --poc-script scripts/range_poc.py --result-file <path> [--reverify]
python3 scripts/range_traversal.py assign-remediate --vmid <g> --finding <fid> --remediate-script scripts/range_remediate.py [--dry-run]
python3 scripts/range_traversal.py report-remediate --vmid <g> --finding <fid> --remediate-script scripts/range_remediate.py --result-file <path>
python3 scripts/range_traversal.py escalate --vmid <g> --finding <fid> [--reason <text>]
python3 scripts/range_traversal.py status
```

State persists in `/workspace/.range_traversal_state.json`, locked per
subcommand. Key behaviors:

- **`init` is additive-only and refuses to merge onto a stale,
  differently-scoped state file** — pass `--fresh` to wipe and start
  clean, or `--hosts` to scope to a subset. (REFERENCE.md: init-stale-state)
- **A finding's `f-<hash>` ID is not a cross-host identifier** — the same
  ID on two hosts can be two unrelated conditions. Read each finding's own
  record before drawing a range-wide conclusion. (REFERENCE.md:
  finding-id-collisions)
- **`next` hands you ONE action at a time** and never blocks the rest of
  the sweep on a stuck finding: `reverify_failed` and
  `remediation_planned` past its retry cap become non-blocking
  `review_needed` notices. (REFERENCE.md: next-blocking-history)

`next`'s possible actions: `clone_and_scan`, `local_audit`, `assign_poc`,
`assign_remediate` (check for `"retry": true`), `assign_reverify`, or
`{"done": true}` / `{"done_pending_review": true, "review_needed": [...]}`.

**Never call `resolve-review` yourself.** A `done_pending_review` result
means every host is finished except review items awaiting the operator —
read each `note`, call `status` for the full
`remediation_summary`/`risk_notes`, then **stop and report them** in your
summary. "Once a human has decided" is not something you can satisfy on
your own behalf; `resolve-review` is the operator's, run from outside this
loop. (REFERENCE.md: why-agent-never-self-resolves)

**`next` does not drive the clone/start step** — when it returns
`clone_and_scan`, that's your cue to call `proxmox_bridge.py` yourself
(subject to the Cloning rules), then `record-scan`.

## Delegating to subagents (`next-batch`)

Claim several ready actions at once instead of looping `next` yourself:

```
python3 scripts/range_traversal.py next-batch --count <N> --claimant <id>
python3 scripts/range_traversal.py release-claim --vmid <g> [--finding <fid>]
```

`next-batch` returns up to `N` currently-unclaimed actions across all
hosts, each leased to `<id>`; hand each to its own `delegate_task`
subagent, which runs the normal assign/do/report shape. A dead subagent's
claim expires after `ACTION_LEASE_TIMEOUT_SECONDS` (30 min), or release it
explicitly. Match `--count` to `delegation.max_concurrent_children`, and
size it to what you actually intend to claim — it has no host/finding
filter and returns the first N actions in enumeration order. Delegation
buys I/O-overlap, not more throughput, and applies only to
PoC/remediate/audit — **never to cloning.** (REFERENCE.md:
delegation-measurement)

## Cloning (via `proxmox_bridge.py`, not this skill)

Five rules. The incidents behind them: REFERENCE.md clone-retry-destroyed,
parallel-clone-burst, early-start-wrong-mac.

1. **Issue `/vm/clone` and `/vm/<vmid>/start` as background commands from
   the first attempt** — never foreground-first-and-fall-back. Wait on
   that one call for its result. The two are sequential: `start` only
   after `/vm/clone` returns `200` (rule 4).
2. **A failed, timed-out, or ambiguous response is not proof the VM
   wasn't created.** Before deleting anything, check with the read-only
   `GET /vm/<vmid>/status` (`exists`, `lock`, `clone_in_progress`,
   `ready_to_start`) or just wait and re-check — don't fire a second
   mutating call for the same vmid. **Never probe with `/vm/<vmid>/start`**
   to see if a clone is done (rule 4).
3. **Never more than one `/vm/clone` in flight across the whole range.**
   Cloning is one blocking disk-copy on shared storage; it gets none of
   delegation's I/O-overlap benefit. Clone one host at a time, in `next`'s
   order, verifying each produced a real disk before the next. The bridge
   enforces this: a second concurrent `/vm/clone` gets `409 "another clone
   is already in progress"` and creates nothing — that means "wait and
   retry", not an error to work around (the other clone may be the
   operator's pre-clone helper, rule 5). `next-batch` is for
   PoC/remediate/audit only, never to drive concurrent `clone_and_scan`.
4. **Start a working clone only after `/vm/clone` returns `200`; never
   scan one whose clone call didn't.** The bridge applies the golden MAC
   (which gives the clone its range address) at the very end of the clone,
   so `status: stopped` from the start of a clone is NOT "ready" — only a
   `200` / `ready_to_start: true` is. Read the bridge's responses:
   - `/vm/clone` → `200`: start it, then scan.
   - `/vm/<vmid>/start` → `409` "still being cloned"/"locked": wait for
     `/vm/clone` to return, then start. Not an error to work around.
   - `/vm/clone` → `409` "was STARTED before /vm/clone finished": the
     bridge already stopped the VM and fixed the MAC — start it once, scan.
   - `/vm/clone` → any other non-`200` (e.g. `500` "net0 could not be
     set"): do NOT start or scan. Check with `GET /vm/<vmid>/status`; if
     it exists, destroy it (stop + `DELETE`) and clone again. Rule 2 is
     about not deleting an in-progress clone — NOT permission to use one
     whose call returned non-`200`.
   - Before `record-scan`, confirm the target answers at its range address
     (`ping -c 2 -W 2 <ip>`, or `nmap -sn <ip>` if ping is absent, retried
     for a few minutes while it boots). If it never answers, check the
     clone instead of recording — `record-scan` on an unreachable target
     writes `scan_failed`, undoable only by an operator `reset-host`.
5. **Your host's working clone may already exist — check before cloning.**
   A working clone is always vmid **golden + 500** (9110 → 9610) named
   `<host>-ctf`. Clone only the host `next` just handed you — getting the
   *next* host ready ahead of time is the pre-clone helper's
   (`scripts/range-preclone.py`) job, not yours. The operator may run that
   helper, or you may have cloned your current host earlier and not
   recorded it. On a `clone_and_scan`, first `GET
   /vm/<golden+500>/status`:
   - `exists: false` → clone it (capacity check first, below).
   - `ready_to_start: true` → a finished clone with the golden MAC and a
     `pristine` snapshot. **Start it — do not clone again or destroy it.**
     Then continue as after your own clone. (REFERENCE.md: pre-clone-helper)
   - `clone_in_progress: true` or a `lock` → someone's clone of it is
     still running: poll every minute or two until `ready_to_start`, then
     start. (The bridge would refuse start/clone/delete with `409` anyway.)

## Storage capacity (`hot-ssd`)

`hot-ssd` is a ≈223GB LVM thin pool on one external USB SSD — it cannot
hold the range's full footprint at once (the 9 Windows hosts alone would
be ≈620GB of clones). A nearly-full thin pool doesn't fail cleanly; it can
hang the whole storage layer. (REFERENCE.md: storage-hang-incident) Two
rules:

1. **Tear down a host's working clone the moment its last finding reaches
   a final status** — not when you start the next host. (REFERENCE.md:
   teardown-not-at-clone-time) A host whose only remaining findings are
   `review_needed` (awaiting the operator) counts as finished for
   teardown: destroy its clone and list those findings in your summary —
   their evidence is already in state. **Before reporting the sweep done**,
   walk every host's `working_vmid` in `status`, check each with `GET
   /vm/<vmid>/status`, and destroy any still-existing clone of a finished
   host. The watchdog runs `scripts/range-teardown.py` on completion as a
   safety net, not a replacement for doing this yourself.
2. **Before `/vm/clone`, check headroom** (you have no shell on Proxmox,
   so `lvs` is unavailable):
   ```
   curl -s "http://host.docker.internal:8811/storage/hot-ssd/status?template_vmid=<golden_vmid>"
   ```
   Always include `?template_vmid=` — without it the check can't size this
   clone (a Windows image is up to 100 GiB). It returns `used_percent`,
   `template_disk_gib`, `projected_percent`, and `ok_to_clone`. If
   `ok_to_clone` is false, destroy finished hosts' clones first, then
   recheck. The bridge enforces the same limits: `/vm/clone` returns `507`
   at ≥75% used or >95% projected — "free space first", never "retry". If
   every finished clone is already gone and it still says no, stop and
   report it.

## Recovering a working clone

- **`GET /vm/{vmid}/status`** is the read-only "does this vmid exist, and
  what's it doing" check (`exists`, `status`, `lock`, `clone_in_progress`,
  `ready_to_start`) — use it instead of inferring from a mutating call's
  side effects. (REFERENCE.md: clone-exists-gap)
- **If a clone is genuinely gone** (destroyed outside this skill), clear
  the stale state so `next` re-issues `clone_and_scan`:
  ```
  python3 scripts/range_traversal.py reset-host --vmid <golden_vmid> --reason "<why>"
  ```
  This does NOT touch recorded findings; a finding already `poc_confirmed`
  keeps evidence collected against the clone that's now gone — worth a
  second look against the new one.
- **If a clone is broken but still exists** (a PoC/remediation crashed it,
  SSH stopped answering, it won't boot), roll back to its `pristine`
  snapshot instead of a full re-clone:
  ```
  curl -X POST "http://host.docker.internal:8811/vm/<vmid>/rollback?snapname=pristine&start=true"
  ```
  Seconds instead of minutes; the working vmid doesn't change, so no
  `reset-host`. Re-run the reachability check afterward. **A rollback to
  `pristine` resets the WHOLE host** — every remediation already applied
  on it is gone. So if any of that host's findings is already
  `remediation_applied` or `reverified_closed`, do NOT roll back: stop and
  report the broken clone (a reverify against a reset clone would wrongly
  read `reachable: true`). That's an operator decision. Optionally, before
  a risky remediation, take your own snapshot to roll back to instead:
  ```
  curl -X POST http://host.docker.internal:8811/vm/<vmid>/snapshot \
    -H "Content-Type: application/json" \
    -d '{"name": "pre-remediate-<short-fid>", "description": "<what changes>"}'
  ```

## Extending the tooling

Changing the bridge's golden-image guard, or adding an audit check (and
its risk/inventory classification and splitter): REFERENCE.md
extending-the-bridge-guard and extending-the-audit-checklist. Overall
validation status: REFERENCE.md validation-status.
