---
name: range-assess
description: Internal assessment loop for CTF-range hosts on Proxmox — scan (network + local), demonstrate reachable findings, remediate, reverify
version: 2.0.0
metadata:
  hermes:
    tags: [security, range, proxmox, assessment]
    category: security
---

## What this is, and what it is not

This runs the operator's own internal assessment loop against their own
isolated Proxmox range (host inventory in `config/hosts.json` — see
that file's `_schema` block for the shape; the copy committed here is a
sanitized template, not a real range): for each host, scan it both over
the network and from inside the guest, demonstrate which findings are
actually reachable/exploitable, plan and apply a remediation, then
re-run the same demonstration to confirm each finding is closed.

**This is not a new attack-execution engine, and no script in this
skill ever calls a model.** `range_traversal.py` is orchestration and
bookkeeping only — same role `ctf_traversal.py` plays for ksnctf — wired
to the project's own `proxmox_bridge.py` for every VM lifecycle
operation. `range_poc.py` and `range_remediate.py`
hand YOU (the agent) a filled, scope-appropriate task prompt and then
validate/log whatever result you report back — they never attempt
anything themselves. **"The local Qwen model does the PoC/remediation"
means your own backend model, acting through your own normal
tool-calling turn** (real network access for a network-scope finding,
an SSH session as `root`/`Administrator` on the working clone for a
local-scope one) — not a second, separate, tool-less model call hidden
inside a script. (An earlier version of this skill tried exactly that
hidden-call shape and it was a real mistake: a bare Ollama chat call has
no tool access at all, so it could never have actually attempted
anything regardless of what its prompt asked for. See `range_poc.py`'s
own docstring for the full account.)

**Golden images are never touched directly.** Every host in
`config/hosts.json` is a golden VMID (`<host>-golden`, DO NOT
MODIFY/DELETE). Every script here that acts against a target takes a
separate `--working-vmid`/working clone (`<host>-ctf`) and hard-refuses
(exit 1, before any network or SSH call) if that value matches
the golden VMID passed alongside it. Creating and destroying the
working clone is `proxmox_bridge.py`'s job, done outside this skill's
own scripts.

**A host with any real-world safety dimension** (e.g. something named
after ICS/SCADA equipment) needs an explicit operator confirmation
recorded before it's treated as just another range host — see
`hosts.json`'s optional per-host `note` field.

## Two scan phases, not one

An earlier version of this skill only scanned over the network
(`range_discover.py`) — too narrow: it misses everything local
privilege escalation actually depends on. Every host now gets BOTH:

1. **Network scan** (`range_discover.py`) — nmap/port-connect from
   wherever this agent's own network egress reaches, same as before.
2. **Local host audit** (`range_host_audit.py`) — enumeration from
   INSIDE the guest, over SSH as `root` (Linux) / `Administrator`
   (Windows) using the `hermes` sandbox's own keypair, authorized on
   every target host: installed package/kernel versions, SUID/SGID
   binaries, world-writable files/dirs, cron/sudoers entries,
   loopback-only listening sockets, and Windows equivalents (unquoted
   service paths, AlwaysInstallElevated, local admin membership, etc.).
   (This originally went over `proxmox_bridge.py`'s guest-exec channel;
   see the "Local audit: SSH, not guest-exec" section below for why
   that changed.)

**Read `range_host_audit.py`'s own docstring before trusting its
output**: it connects as `root`/`Administrator` over SSH, so the audit
sees everything — that's the point, since every check exists to find
conditions an *unprivileged* actor could abuse. The audit result is
evidence a condition exists, not
evidence it's been exploited; that's the PoC step's job.

Every finding, from either phase, is tracked the same way in
`range_traversal.py`'s state, tagged `"scope": "network"` or
`"scope": "local"`.

## Local audit: SSH, not guest-exec

`range_host_audit.py` and the local-scope PoC/remediation prompts
originally ran over `proxmox_bridge.py`'s guest-exec channel (QEMU
Guest Agent, virtio-serial). On the real range, that failed with
"No QEMU guest agent configured" on every golden image tried
(9101–9103) — confirmed via `qm config <vmid> | grep -i agent` on the
Proxmox host: the `agent: 1` flag (which creates the virtio-serial
device the guest agent needs) was absent from all of them, not just
the daemon being unresponsive inside the guest. Rather than add that
config to every golden image, the operator switched this skill's local
vantage point to a standing channel instead: the `hermes` sandbox's own
SSH keypair, authorized on every target host for `root` (Linux) /
`Administrator` (Windows) — reachable now that the sandbox has a real
network route into the range. `--target <ip>` now flows through
`record-audit`/`assign-poc`/`assign-remediate` to `range_host_audit.py`
and the local-scope prompts the same way it already did for
network-scope findings, instead of being left empty for local ones.
The privilege model (full root/Administrator access, same caveat as
before) is unchanged — only the transport is different. Host-key
checking is deliberately disabled for this SSH channel (see
`range_host_audit.py`'s own docstring for why that's a reasoned
tradeoff here, not an oversight).

## Usage — single host, one pass through the loop

```
# 1. Clone + start the working image (proxmox_bridge.py, not this skill).
#    Start ONLY after the clone call has returned 200 — see "Cloning" below.
curl -X POST http://host.docker.internal:8811/vm/clone -d '{"template_vmid": 9001, "new_vmid": 1234, "name": "example-linux-host-ctf"}'
curl -X POST http://host.docker.internal:8811/vm/1234/start

# 2. Scan it — network, then local
python3 scripts/range_discover.py scan --target <working clone's ip>
python3 scripts/range_host_audit.py audit --golden-vmid 9001 --working-vmid 1234 --os Linux --target <working clone's ip>

# 3. For each finding, get the task prompt — this does NOT call a model
python3 scripts/range_poc.py assign \
    --golden-vmid 9001 --working-vmid 1234 --host example-linux-host \
    --finding-id f-abc123 --scope network --context-file /workspace/ctx_f-abc123.json

# 4. YOU do the real work with YOUR OWN tools (network for scope=network,
#    an SSH session as root/Administrator for scope=local), write your
#    result as JSON, then report it — THIS validates and logs it:
python3 scripts/range_poc.py report \
    --golden-vmid 9001 --working-vmid 1234 \
    --finding-id f-abc123 --result-file /workspace/result_f-abc123.json

# 5. For a confirmed finding (repeat the assign/do-the-work/report shape):
python3 scripts/range_remediate.py assign ...
python3 scripts/range_remediate.py report --result-file ...

# 6. Reverify — range_poc.py assign/report again, same finding-id,
#    confirming the remediation actually closed it

# 7. Stop + destroy the working image (proxmox_bridge.py, not this skill)
curl -X POST http://host.docker.internal:8811/vm/1234/stop
curl -X DELETE http://host.docker.internal:8811/vm/1234
```

Step 3's `--context-file` is a small JSON file YOU (or, in the
traversal flow below, `range_traversal.py` itself) write with the
finding's details — see `range_poc.py`'s own docstring for the exact
shape per scope. This keeps multi-line audit output and evidence out of
shell argv entirely.

## Usage — multi-host traversal (`range_traversal.py`)

**Honest framing, same as `ctf_traversal.py`'s own**: this does not
scan, exploit, or remediate anything itself — it tracks per-host,
per-finding state across however many separate agent turns a full range
sweep actually takes, and tells you the single next thing to do. Unlike
`ctf_traversal.py`'s one-shot `next`/`submit`, the PoC and remediation
steps here are explicitly two-phase (`assign` then `report`), because
the real work between them is now yours to do, not a blocking call this
script makes for you.

```
python3 scripts/range_traversal.py init --config ../config/hosts.json [--hosts <vmid1,vmid2,...>] [--fresh]
python3 scripts/range_traversal.py next
python3 scripts/range_traversal.py record-scan --vmid <golden_vmid> --working-vmid <id> --target <ip> --discover scripts/range_discover.py
python3 scripts/range_traversal.py record-audit --vmid <golden_vmid> --working-vmid <id> --target <ip> --audit scripts/range_host_audit.py
python3 scripts/range_traversal.py assign-poc --vmid <golden_vmid> --finding <finding_id> --poc-script scripts/range_poc.py [--reverify]
python3 scripts/range_traversal.py report-poc --vmid <golden_vmid> --finding <finding_id> --poc-script scripts/range_poc.py --result-file <path> [--reverify]
python3 scripts/range_traversal.py assign-remediate --vmid <golden_vmid> --finding <finding_id> --remediate-script scripts/range_remediate.py [--dry-run]
python3 scripts/range_traversal.py report-remediate --vmid <golden_vmid> --finding <finding_id> --remediate-script scripts/range_remediate.py --result-file <path>
python3 scripts/range_traversal.py status
```

`assign-poc`/`assign-remediate` write the finding's context file for
you automatically (from state) and forward the resulting prompt —
nothing to build by hand in the traversal flow. State persists in
`/workspace/.range_traversal_state.json`, locked the same way
`ctf_traversal.py` locks its own state — a separate `.lock` file held
for the whole load-modify-save span of every subcommand.

**`init` is additive-only by default, and refuses to silently merge
onto a stale, differently-scoped state file.** Found necessary for
real: a 3-host exercise pointed at a genuinely trimmed 3-host
`--config` still came back covering all 21 hosts, because the state
file at `/workspace/.range_traversal_state.json` already held an
earlier sweep's full host set and `init`'s own `setdefault`-everywhere
design (so re-running `init` mid-sweep to add a host never clobbers
progress on the others) silently kept it. `init` now checks for hosts
already in state that aren't part of the current `--config`/`--hosts`
set and refuses with `stale_hosts` listed, unless `--fresh` is passed
to explicitly wipe and start clean. `--hosts <vmid1,vmid2,...>` scopes
a run to a subset of `--config`'s hosts without needing a separate
trimmed config file at all.

**A finding's `f-<hash>` ID is NOT a stable cross-host identifier for
"this kind of finding" — it only disambiguates findings within one
host's own scan.** `finding_id()` hashes `(scope, port, proto, service)`
for a network finding, or `(scope, category, command)` for a local one
— never the version string, the actual evidence, or the vmid. Confirmed
for real: two hosts with "port 22/tcp/ssh" open got the identical ID
despite one being a modern OpenSSH with nothing exploitable
(`poc_not_reachable`) and the other having a genuinely confirmed
root-key network login (`remediation_planned`, `needs_operator_decision:
true`) — same ID, two unrelated findings. This is harmless to the
pipeline itself (every script keys storage and result files by
`<vmid or working_vmid>:<finding_id>` together, never by a bare
`finding_id` alone — see `range_status_report.py`'s own `all_findings`
loop), but it's a real trap for a human (or a future script) skimming
state across hosts and assuming the same ID means the same underlying
condition. Always read a finding's own record (`poc_evidence`,
`version`, `risk_notes`, etc.) per host before drawing a range-wide
conclusion from how many hosts share an ID.

**Revised from the original design, after a real 21-host run exposed
two problems with it.** `next` used to walk ONE host at a time, in
config order, to a terminal status (or `review_needed`) before ever
issuing the next host's `clone_and_scan` — and a finding stuck at
`reverify_failed` had no resolution command at all. Combined, a single
stuck finding on host 3 would have silently halted hosts 4 through 21
forever; it never happened in that run purely by luck (25/25 reverifies
succeeded), not because the design prevented it.

`next` now still hands you ONE thing at a time (unchanged for solo,
non-delegated use), but:
- a `remediation_planned` finding (a `report-remediate` call where
  `applied` came back false) is retried automatically as
  `assign_remediate` up to a cap, instead of being silently skipped
  forever;
- `reverify_failed`, and `remediation_planned` past its retry cap, are
  surfaced as **non-blocking** `review_needed` notices — collected
  separately and reported once nothing else is left to do, never
  instead of a real host's actionable work. `next` will keep advancing
  through every other host regardless of how many findings are stuck
  in review;
- `resolve-review --vmid <golden_vmid> --finding <finding_id>
  --resolution accept_risk|retry|closed_manually [--note <text>]` is
  the only sanctioned way to close a review item — same principle
  AGENTS.md already states for `.ctf_traversal_state.json`: nothing but
  this script's own commands touches a tracked status.

`next`'s possible actions: `clone_and_scan`, `local_audit`,
`assign_poc`, `assign_remediate` (first attempt or a retry — check for
a `"retry": true` field), `assign_reverify`, or `{"done": true}` /
`{"done_pending_review": true, "review_needed": [...]}`. A
`done_pending_review` result means every host has either finished or
has nothing left but review items — read each one's `note` and call
`status` for the finding's full `remediation_summary`/`risk_notes`,
then **stop and report them** in your final summary. Do not call
`resolve-review` yourself, however clearly the right call seems —
confirmed for real: in an unattended run, the agent itself called
`resolve-review --resolution accept_risk` against a `needs_operator_decision`
finding (the standing root-SSH access channel) while the operator was
asleep, with reasoning that happened to be sound but with no actual
operator involved at any point. The phrase "once a human/operator has
decided" is not a condition the agent can satisfy on its own behalf —
there is no technical gate separating "a human decided" from "I decided
and I'm the one calling myself the human who decided," so the only safe
rule is that the agent never calls `resolve-review`, full stop.
`resolve-review` is for a human operator to run themselves, after the
fact, from outside this loop — not something handed back to the agent
to invoke once it judges the decision made.

**`next` deliberately does not drive the clone/start step itself** —
unlike ksnctf's near-free "fetch the next problem," cloning a Proxmox VM
can take 30-60+ minutes, so this engine treats "is the working clone up"
as a precondition it checks (`network_scan_status == "not_started"`)
rather than a step it drives. When `next` returns
`{"action": "clone_and_scan", ...}`, that's your cue to call
`proxmox_bridge.py` directly, then `record-scan` once the clone is
reachable.

## Delegating to subagents (`next-batch`, when driving more than one host at once)

**Why this exists**: a real 21-host sweep driven by `next` alone, one
action at a time, took 5 days — every PoC/remediate action is its own
full agent turn (real exploitation/remediation attempts over SSH or
network access, not a cheap lookup), and `next`'s one-at-a-time
handoff means the GPU sits idle during every tool-call round-trip
(an SSH command, an `nmap`/`sqlmap` run) while the primary waits.

**What delegation actually buys here, and what it doesn't**: measured
directly (`scripts/measure-ollama-concurrency.sh`) against this
project's delegation model — see `config/config.yaml`'s `delegation:`
block comment for the full numbers — concurrent requests did NOT
increase real token-generation throughput; this hardware time-slices
GPU decode across concurrent requests rather than batching them, so
aggregate tokens/sec stayed flat from 3-way through 16-way concurrency.
**Do not delegate range-assess work expecting N subagents to "think" N
times faster in aggregate — they won't.** The real win is structural:
a PoC/remediate subagent's wall-clock time is dominated by tool-call
waits (SSH/nmap/sqlmap round-trips), not generation — so while one
subagent is blocked on its own tool call, a different subagent's
reasoning turn can use the GPU instead of it sitting idle. That's an
I/O-overlap gain, not a throughput multiplier, and it's the only gain
delegation is actually expected to produce here. **This reasoning is
specific to PoC/remediate/audit actions and does NOT extend to
cloning** — see the "Cloning" section below for why.

**How to actually delegate** — claim several ready actions at once
instead of calling `next` in a loop yourself:

```
python3 scripts/range_traversal.py next-batch --count <N> --claimant <id>
```

Returns up to `N` currently-unclaimed actions (across ALL hosts —
once a host's scan+audit are done, several of its findings can be
independently actionable at once, not just actions on different
hosts), each already marked claimed by `<id>` so a different
concurrent caller gets offered something else instead of colliding on
the same work. Hand each claimed action to its own `delegate_task`
subagent (one action per subagent call) — the two-phase assign/report
shape is unchanged; a delegated subagent calls `assign-*` then does
the real work then calls `report-*` exactly as a non-delegated turn
would. `report-*` clears the claim automatically as part of recording,
whoever ends up holding it.

**`--count` is NOT scoped to a host or finding set — size it to what
you actually intend to claim right now.** This happened for real: a
`--count 97` meant to grab one specific finding on one host instead
swept up 96 unrelated, already-discovered findings on a different
host that happened to be queued ahead of it — all correctly leased
under `ACTION_LEASE_TIMEOUT_SECONDS`, all needing manual
`release-claim` calls to undo. `next-batch` has no `--host`/`--finding`
filter; it just returns the first `N` currently-unclaimed actions
across the whole range, in whatever order the engine enumerates them.
If you want a specific action, use `assign-poc`/`assign-remediate`
directly (or `next` once, inspect what it returns, `release-claim` if
it's not what you wanted) rather than `next-batch` with a count picked
to "probably" land on it.

**If a subagent dies or times out without ever reporting**, its claim
expires on its own after `ACTION_LEASE_TIMEOUT_SECONDS` (30 minutes,
in `range_traversal.py`) and becomes claimable again — or release it
immediately instead of waiting out the timeout:

```
python3 scripts/range_traversal.py release-claim --vmid <golden_vmid> [--finding <finding_id>]
```

(omit `--finding` to release a host-level `clone_and_scan`/`local_audit`
claim instead of a finding-level one.)

**Match `--count` to `delegation.max_concurrent_children` in
`config.yaml`** (currently 6) — claiming more than that just means some
claimed actions sit waiting for a free subagent slot instead of being
worked immediately, with no benefit over claiming fewer at a time.

**Validate on 2-3 hosts before trusting this across a full rerun** —
same discipline as everything else in this skill: confirm claimed
actions land on genuinely different hosts/findings (not the same one
twice), that a subagent's `report-*` call actually clears its claim,
and that `status` reflects what you expect, before relying on this at
the full 21-host scale.

## Cloning: always background it, and never assume a failed call means a failed clone

Confirmed against a real run, not theoretical: `/vm/clone`'s HTTP
response doesn't come back until Proxmox actually finishes cloning —
the call blocks for the clone's full real duration (which, per the
note above, can be 30-60+ minutes). A foreground terminal/shell call's
own execution-timeout is very likely shorter than that. When it fires,
it kills *your own waiting client* — it does nothing to the clone
request already sitting with Proxmox, which keeps running, completely
unaffected, on the bridge side.

This produced a real, avoidable failure: a foreground `/vm/clone` call
timed out locally after 60s; the agent (reasonably, having no other
signal) retried with the same `new_vmid`; Proxmox rejected the retry
("config file already exists") because the FIRST call was still
genuinely in progress; the agent read that rejection as "a stale
leftover from a dead prior session" and deleted that vmid — destroying
its own first call's clone, which was seconds away from completing
successfully. It then had to clone a third time from scratch. One
`--os`-level timeout mismatch, one wasted clone (and the minutes it
takes), zero actual bugs in the clone logic itself.

Five rules that prevent this entirely:

1. **Issue `/vm/clone` and `/vm/<vmid>/start` as background commands
   from the very first attempt** — never foreground-first-and-fall-
   back. Then poll or wait on that one background call for its actual
   result, rather than guessing from a local timeout. The two are
   sequential, never parallel: `start` is issued only after the
   background `/vm/clone` call has returned — see rule 4.
2. **A failed, timed-out, or ambiguous clone/start response is not
   proof the VM wasn't created.** Before deleting anything, check
   whether the vmid already exists and how far along it is with the
   read-only `GET /vm/<vmid>/status` (see "Checking whether a working
   clone still exists" below — `exists`, `lock`, `clone_in_progress`,
   `ready_to_start`) — or just wait and re-check, rather than sending
   a second mutating call for the same vmid. **Never probe with
   `/vm/<vmid>/start`** to find out whether a clone is done: a start
   that lands while the clone is still finishing boots the VM on the
   wrong MAC (rule 4).
3. **Never have more than one `/vm/clone` in flight across the whole
   range at once.** Cloning is a heavy disk-copy operation on ONE
   shared Proxmox host and storage backend — it does not get the
   I/O-overlap benefit "Delegating to subagents" above describes for
   PoC/remediate actions. That benefit exists specifically because a
   PoC/remediate subagent's wall-clock time is dominated by waiting on
   its OWN tool calls (SSH/nmap/sqlmap round-trips) while a different
   subagent's reasoning turn uses the GPU instead — there is no
   equivalent overlap for a clone, which is a single blocking
   disk-copy with no reasoning in between to interleave with anything
   else. Confirmed for real: a self-written batch script fired 15
   `/vm/clone` requests at once for the range's remaining hosts,
   reasoning (reasonably, if cloning were a cheap independent API
   call, which it isn't) that parallel provisioning would save wall
   time the same way delegated PoC/remediate work does. It didn't —
   most of the 15 hung for the full request timeout and died, while an
   EARLIER, separate batch of malformed clone requests had already left
   16 dead, diskless vmid reservations sitting in Proxmox (a bridge-side
   bug: the vmid gets reserved before the request body is validated, so
   even a rejected/422 clone call can leave an empty stub VM behind —
   check for a real disk, e.g. `qm config <vmid>` showing a
   `scsi`/`virtio`/`sata` line, not just that the vmid exists). None of
   this — successful clones included — ever got recorded as any host's
   `working_vmid`, because it bypassed `record-scan` entirely. Clone
   hosts strictly one at a time, in the same order `next` would hand
   them to you, waiting for each one to fully complete and verifying it
   actually produced a disk before starting the next. `next-batch` is
   for claiming PoC/remediate/audit actions across already-provisioned
   hosts — never use it, or any ad hoc script, to drive more than one
   host's `clone_and_scan` concurrently. The bridge now enforces this
   itself: a second `/vm/clone` while one is running gets `409 "another
   clone is already in progress"` and creates nothing — that means
   "wait and retry later", not an error to work around. (The other clone
   may be the operator's pre-clone helper — see rule 5.)
4. **Start a working clone only after `/vm/clone` has returned `200` —
   and never scan one whose clone call didn't.** A clone is not finished
   when Proxmox's copy finishes: the bridge then sets the clone's `net0`
   back to the golden image's exact MAC, which is what gives it its
   range address (dnsmasq reservations on range-gw are keyed to the
   golden MAC; statically-addressed hosts were verified reachable with
   that MAC too). `GET /vm/<vmid>/status` shows `exists: true, status:
   stopped` from the very START of a clone, so "stopped" is not "ready"
   — only `/vm/clone` returning `200` (or `ready_to_start: true`) is.
   Confirmed for real: on two hosts (9110→9610, 9112→9612) `start` was
   sent ~10s before `/vm/clone` returned. The VM booted on the random
   MAC Proxmox gives every clone, the bridge's MAC update was then
   rejected (`/vm/clone` → `400`), and the agent carried on and ran
   `record-scan` against an address the VM never had. Both hosts were
   recorded `scan_failed` — which `range_traversal.py` treats as a dead
   end (`next` never offers that host again; recovery needs an operator
   `reset-host`). The bridge now refuses this itself (`start` → `409`
   while a clone is in progress), but read its responses this way:
   - `/vm/clone` → `200`: start it, then scan.
   - `/vm/<vmid>/start` → `409` "still being cloned" / "locked by
     Proxmox": not an error to work around — wait for the background
     `/vm/clone` call to return, then start.
   - `/vm/clone` → `409` "was STARTED before /vm/clone finished": the
     bridge has already stopped the VM and applied the golden MAC. The
     clone is fine — start it once, then scan.
   - `/vm/clone` → any other non-`200` (e.g. `500` "net0 could not be
     set"): do NOT start or scan that vmid. Check it with `GET
     /vm/<vmid>/status`; if it exists, destroy it (stop + `DELETE`) and
     clone again. Rule 2's "a failed call is not a failed clone" is
     about not deleting a clone that's still in progress — it is NOT
     permission to use a clone whose call came back non-`200`.
   - Before `record-scan`, confirm the target actually answers at its
     range address (e.g. `ping -c 2 -W 2 <ip>` from the sandbox, or
     `nmap -sn <ip>` if ping isn't installed — retried for a few minutes
     while it boots). If it never answers, check the
     clone (status, MAC) instead of recording — `record-scan` on an
     unreachable target writes `scan_failed`, and that can't be undone
     without an operator.
5. **Your host's working clone may already exist — check before cloning.**
   A host's working clone always uses vmid **golden + 500** (9110 → 9610,
   9207 → 9707) and the name `<host>-ctf`. The operator may run a
   pre-clone helper (`scripts/range-preclone.py`, on the Mac) that clones
   the NEXT `not_started` host ahead of time while you work on the
   current one; you may also have cloned it yourself earlier and not yet
   recorded it. So when `next` hands you `clone_and_scan`, first call
   `GET /vm/<golden+500>/status`:
   - `exists: false` → clone it yourself (capacity check first, see
     "Storage capacity" below).
   - `ready_to_start: true` → it's a finished clone with the golden MAC
     and a `pristine` snapshot. **Start it — do not clone again and do
     not destroy it** as a "stale leftover". Then continue exactly as
     after your own clone (reachability check, `record-scan`, ...).
     Confirmed working for real: 9707 was found this way and started,
     saving a 33-minute Windows clone.
   - `clone_in_progress: true` or a `lock` → someone else's clone of it is
     still running. Don't start, clone, or delete it: poll the status
     every minute or two until `ready_to_start` is true, then start it.
     (The bridge would refuse all three with `409` anyway.)
   A clone that's waiting for you costs `hot-ssd` space, so don't leave it
   unused: start it when its host comes up.

## Storage capacity: `hot-ssd` cannot physically hold every clone at once

`hot-ssd` is an LVM thin pool on ONE external USB SSD (Buffalo, UAS,
240GB nominal, ≈223.57GB as the actual thin pool) — not a large shared
SAN, not elastic. Do the arithmetic before assuming clones can just
accumulate: the 9 Windows-domain hosts alone
(`BAF-DC1/DC2`, `DC1-beg`, `DC2-beg`, `ADCS-BAF/BEG`, `mail-int-baf`,
`mail-beg`, `sp-int-baf`) are 60-100GB each — **620GB total if their
working clones all existed at once, nearly 3x the entire pool.** This
is not a margin-of-safety concern, it is a hard physical impossibility:
the range's full footprint cannot coexist as working clones on this
storage. Nothing in `range_traversal.py`'s state machine enforces this
— `next`'s possible actions have no teardown step, so it is entirely on
you to destroy each host's working clone once you're done with it,
same as the single-host "Usage" example's step 7 already shows, but
that discipline does NOT happen automatically in the traversal/
`next-batch` flow and this skill has already observed it failing to
happen in practice (a fully reverified, hours-old host's working clone
left running/existing with nothing further to do on it).

**Confirmed for real, same incident as the cloning-concurrency one
above**: the pool hit 93.58% actual data usage, which is almost
certainly why the 14:36 parallel clone burst's underlying `lvcreate`
call stalled indefinitely (out of real blocks to allocate) rather than
failing cleanly — and because that one stuck process held the volume
group's own lock the whole time, it silently froze EVERY later LVM
operation (every subsequent clone, every destroy, even the Proxmox web
UI) for over 7 hours, with no error presented to the agent at all
(requests just hung past the 2-hour HTTP timeout and came back `504`).
A nearly-full thin pool is not merely "will fail with a clear error
when full" — it can hang the whole storage layer first.

Two rules:

1. **Destroy (stop + delete) a host's working clone as soon as every
   one of its findings reaches a terminal status** (closed, risk
   accepted, or otherwise resolved via `resolve-review`) — do not leave
   a finished host's clone sitting around while working through the
   rest of the range. Check this explicitly; don't assume it already
   happened. **Teardown belongs to FINISHING a host, not to starting the
   next one.** Confirmed for real (2026-11-24): teardowns had only ever
   happened right before cloning the next host, so once clones were
   pre-cloned for the agent (Cloning rule 5) that moment never came, and
   four finished hosts' clones (9612, 9707, 9708, 9709) were still
   running when the sweep completed. So: the moment a host's last
   finding reaches a final status, stop + `DELETE` its clone — and
   **before you report the sweep as done**, go through every host's
   `working_vmid` in `status`, check each with `GET /vm/<vmid>/status`,
   and destroy any clone still existing whose host is completely
   finished. **For teardown, a host whose only unfinished findings are
   ones `next` reports as `review_needed` (awaiting the operator) counts
   as finished** — destroy its clone and list those findings in your
   summary. Its evidence is already in state, and the operator can
   re-clone the host if they want to look; keeping such clones around
   during the sweep would let them pile up on `hot-ssd` (the same
   operator-decision finding has appeared on many hosts at once). The
   watchdog also runs
   `scripts/range-teardown.py` with the same rules when a sweep
   completes, as a safety net — not as a replacement for doing it
   yourself.
2. **Before issuing `/vm/clone`, check the pool actually has headroom**
   through the bridge (you have no shell on the Proxmox host, so `lvs`
   is not available to you):
   ```
   curl -s "http://host.docker.internal:8811/storage/hot-ssd/status?template_vmid=<golden_vmid>"
   ```
   Always include `?template_vmid=` — without it the check can't see how
   big this particular clone is (a Windows golden image is up to 100 GiB,
   nearly half the pool). It returns the pool's `used_percent` (the thin
   pool's Data%), the template's worst-case clone size
   (`template_disk_gib`), the `projected_percent` after cloning it, and
   `ok_to_clone`. If
   `ok_to_clone` is false, do not clone: destroy finished hosts' working
   clones first (rule 1), then check again. The bridge enforces the same
   limits itself — `/vm/clone` returns `507` with the same `reason` when
   usage is at/above 75% or the clone could push it above 95% — so a
   `507` means "free space first", never "retry". The failure mode when
   this pool runs genuinely out of space is not a clean error, it's the
   kind of multi-hour silent hang described above. If every finished
   host's clone is already gone and it still says no, stop and report it
   — that's an operator decision, not something to work around.

   The bridge's full list of endpoints is at
   `GET http://host.docker.internal:8811/openapi.json` — read that
   instead of guessing at paths (confirmed for real: an agent probed
   `/storage`, `/lvs`, `/pool`, `/volumes`, ... — all 404 — looking for
   this check before it existed, then cloned without it).

## Checking whether a working clone still exists, and recovering if it doesn't

`proxmox_bridge.py`'s `GET /vm/{vmid}/status` is the read-only "does
this vmid still exist, and what's it doing" check — use it instead of
guessing from a mutating call's side effects. It returns
`{"vmid": ..., "exists": false}` for a vmid that's gone, or
`{"vmid": ..., "exists": true, "status": "running"|"stopped", ...}`
otherwise. For an existing vmid it also returns `lock` (Proxmox's own
config lock, e.g. `"clone"` while a clone is copying),
`clone_in_progress` (the bridge is still inside `/vm/clone` for it —
the golden MAC isn't applied yet), and `ready_to_start` (true only when
neither of those holds and the VM is stopped). Use these — not
`status: stopped` alone — to tell whether a fresh clone is finished.

This exists because of a real gap, found live: an operator manually
destroyed a working clone directly in Proxmox (outside this bridge,
outside `range_traversal.py` — so neither one knew it happened).
`range_traversal.py`'s own state kept recording that host's
`working_vmid` as real, and there was previously no read-only way to
check otherwise — the agent fell back to a plain `GET` that doesn't
exist on this bridge (405), then to reading a `/vm/{vmid}/exec` call's
error as a proxy signal, which is ambiguous (a fast failure there
looks the same whether the vmid is genuinely gone or the guest agent
just isn't up yet).

If `GET /vm/{vmid}/status` confirms a host's working clone is
genuinely gone (manually destroyed, or destroyed by some other process
outside this skill's own teardown step), clear the stale state with:

```
python3 scripts/range_traversal.py reset-host --vmid <golden_vmid> \
    --reason "working clone manually destroyed in Proxmox outside the sanctioned teardown"
```

This clears `working_vmid` and resets both scan statuses to
`not_started` so `next` issues a fresh `clone_and_scan` — it does NOT
touch any already-recorded finding's status or evidence. Be aware of
what that implies: if a finding was already `poc_confirmed` (or
further) before the reset, its evidence was collected against the
clone that's now gone — worth a second look against the new clone
rather than assuming it still holds, the same caution a reverify
banner already applies to pre-remediation audit output elsewhere in
this skill. Every reset is recorded in that host's `reset_history` in
state, so `status` shows an audit trail of when and why this happened.

**NEW, UNTESTED**: `GET /vm/{vmid}/status`'s "doesn't exist" detection
was written from the error text Proxmox returned in a *different* call
(a start attempt on an already-destroyed vmid), not yet confirmed
against this exact endpoint called on a genuinely nonexistent vmid.
Verify the real error text/status code once against a real target
before trusting the `exists: false` branch.

## Snapshots: every clone has a `pristine` restore point

Every clone made through the bridge's `/vm/clone` ends with a disk-only
snapshot named `pristine`, taken while the clone was stopped and had
never booted, with the golden MAC already applied — i.e. identical to the
golden image. `GET /vm/<vmid>/snapshots` lists a clone's snapshots. (A
clone that was started before `/vm/clone` returned has no `pristine`:
its disk had already changed.)

**Use it instead of destroy + re-clone when a working clone is broken** —
a PoC or remediation crashed something, SSH stopped answering, the guest
won't boot, or its state is otherwise unknown:

```
curl -X POST "http://host.docker.internal:8811/vm/<vmid>/rollback?snapname=pristine&start=true"
```

That takes seconds instead of a full re-clone (minutes for Linux, up to
33 minutes for Windows). Without `start=true` the VM is left STOPPED.
Either way, re-run the reachability check before going on. The working
vmid doesn't change, so no `reset-host` is needed.

**A rollback to `pristine` resets the WHOLE host** — every remediation
already applied on that clone is gone along with whatever broke. So:
- Before any remediation has been applied on that host: roll back freely.
- If any of that host's findings is already `remediation_applied` or
  `reverified_closed`: **don't roll back — stop and report the broken
  clone instead.** Those fixes would silently disappear, and a reverify
  against the reset clone would wrongly come back `reachable: true`.
  That's an operator decision, not yours.

Optionally, before a remediation you expect could break the host, take
your own snapshot and roll back to that instead (same rules, but it only
discards what happened after it):

```
curl -X POST http://host.docker.internal:8811/vm/<vmid>/snapshot \
  -H "Content-Type: application/json" \
  -d '{"name": "pre-remediate-<short-finding-id>", "description": "<what is about to change>"}'
```

Snapshots take `hot-ssd` space as the VM diverges from them; they are
removed along with the VM when you destroy it.

## What's genuinely untested here

This whole skill is new, end to end — none of it has been run against
a real host in the range yet. The full state-machine plumbing
(`range_traversal.py`'s every subcommand, `range_poc.py`/
`range_remediate.py`'s assign/report pair and every guardrail, both
scope paths) HAS been exercised against synthetic/mocked
discover-and-audit output and confirmed to behave correctly end-to-end
— what's genuinely unverified is everything downstream of a REAL
target: whether `range_discover.py`'s nmap parsing and
`range_host_audit.py`'s check list actually produce something useful
against this range's real hosts, and whether an agent's own real
exploitation/remediation attempts (the part no script can simulate)
actually work the way the prompts assume. Validate in stages, not all
at once:

1. **`range_discover.py` alone**, against one known Linux host you can
   also check with a manual `nmap` run — confirm the reported findings
   actually match, and specifically check the nmap-XML path separately
   from the stdlib fallback (no service detection at all).
2. **`range_host_audit.py` alone**, against one working clone — confirm
   the SSH probe succeeds, each check's command actually runs
   (some, like the RPM query on a Debian host, are EXPECTED to fail —
   that's a per-check failure recorded in `errors`, not a reason to
   distrust the rest), and the output is something you could actually
   reason about (not, say, truncated mid-JSON by `MAX_OUTPUT_CHARS` in
   a way that loses the interesting part — raise that constant if so).
3. **`range_poc.py` assign/report, one finding at a time** — confirm
   the filled prompt reads sensibly for both scopes, the golden-vmid
   guard actually refuses when tested on purpose, and the attempt
   cap/delay guardrails actually trigger when tested on purpose (not
   just reasoned through).
4. **`range_remediate.py` assign/report** — confirm the empty-evidence
   guard actually refuses when tested on purpose, then a real run with
   real evidence.
5. **The reverify step** — confirm a real re-run against a genuinely
   fixed finding reports `reachable: false`, not just that the scripts
   run without error. A reverify's assigned prompt carries a
   `## This is a REVERIFICATION` banner naming the ORIGINAL method/
   evidence and the remediation applied since — confirm that banner
   actually renders with the right values (not placeholders) before
   trusting a `reverified_closed` status: a reverify that doesn't
   target the same method that originally worked proves less than it
   looks like it does.
6. **`range_traversal.py`'s full loop** only once 1-5 are each
   independently trusted against a real target — confirm `init` seeds
   state correctly, `next` returns the expected action at each stage of
   one host's full lifecycle (both scan phases, both scopes, remediate,
   reverify), and `status`'s summary counts match what actually
   happened.
7. **`GET /vm/{vmid}/status` and `reset-host`** — against a real
   target, confirm the status endpoint's `exists: false` branch
   actually fires for a genuinely destroyed vmid (not just a guess
   from a different call's error text — see that endpoint's own
   comment), and that `reset-host` followed by `next` correctly
   re-issues `clone_and_scan` rather than reusing the stale vmid.
8. **The lease/retry/review mechanism and `next-batch` delegation**
   (added after the first real 21-host run, not part of the original
   validation list above) — the claim/release/next-batch mechanics are
   now confirmed against real concurrent `delegate_task` subagents
   (disjoint host-level claims, genuine wall-clock overlap — not
   synthetic fixtures; see `CHANGELOG.md` for details), `report-poc`
   clearing a real **per-finding** claim (`"<vmid>:<finding_id>"`, not
   just a host-level one) — see the `--count` caveat above — and now
   `resolve-review --resolution accept_risk` against a real finding
   (not a fixture): a genuinely-hardened, legitimately-open SSH service
   correctly flagged `needs_operator_decision` and closed as
   `risk_accepted`, with `review_log` recording the resolution. All
   four claim-clearing report commands and, as of a second real
   21-host run, **all three** `resolve-review` resolutions are now
   confirmed real: `retry` correctly reset `remediation_attempts` to 0
   and `needs_operator_decision` to `false` while leaving status at
   `remediation_planned` (not `reverify_failed`, so no transition back
   to `poc_confirmed`), `closed_manually` and `accept_risk` both landed
   their expected terminal statuses — all three against the same
   recurring golden-image SSH-root-login finding on three different
   hosts, deliberately resolved three different ways purely for
   validation coverage (see `CHANGELOG.range-assess-extension.md`'s
   eighth/ninth gaps). Still not exercised: a real remediation
   genuinely hitting its retry cap (`MAX_REMEDIATION_ATTEMPTS`) under
   real, not synthetic, `applied: false` results.
   `delegation.max_concurrent_children: 10` in `config.yaml` is a
   conservative setting, not the hardware's real ceiling — see
   `CHANGELOG.md` for the measurement behind it.

## Extending `proxmox_bridge.py`'s golden-image guard

`proxmox_bridge.py`'s existing `guard_not_template()` protects exactly
one VMID (`PVE_TEMPLATE_VMID`, default 9000). A multi-host range has
more than one golden image to protect the same way. See the project's
`proxmox_bridge.py` patch notes for the `PVE_GOLDEN_VMIDS` extension
(comma-separated set, falls back to the existing singular env var if
unset) — this skill's own scripts guard the working/golden-vmid
distinction independently at the finding-action layer too (hard refusal
when the two match), but that's a second, redundant layer on top of the
bridge's own guard, not a substitute for patching the bridge — the
bridge is what actually stands between an API call and the golden VM's
disk.

## Extending the local audit's check list

`range_host_audit.py`'s `LINUX_CHECKS`/`WINDOWS_CHECKS` lists are a
deliberately non-exhaustive starting baseline, not a claim of
completeness. Add a check by appending a `(category, argv,
description)` tuple to the relevant list — every check is read-only
enumeration by design (no guest-state mutation, no exploitation
attempt), so a new check should stay in that category: find/ls/cat-style
metadata reads, package/service listings, PowerShell `Get-*` cmdlets.
