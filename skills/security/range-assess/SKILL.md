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
operation and guest-exec call. `range_poc.py` and `range_remediate.py`
hand YOU (the agent) a filled, scope-appropriate task prompt and then
validate/log whatever result you report back — they never attempt
anything themselves. **"The local Qwen model does the PoC/remediation"
means your own backend model, acting through your own normal
tool-calling turn** (real network access for a network-scope finding,
real calls to `proxmox_bridge.py`'s guest-exec endpoint for a
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
(exit 1, before any network or guest-exec call) if that value matches
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
   INSIDE the guest, via `proxmox_bridge.py`'s guest-exec channel
   (`qm guest exec`, virtio-serial — not the guest's network
   interface): installed package/kernel versions, SUID/SGID binaries,
   world-writable files/dirs, cron/sudoers entries, loopback-only
   listening sockets, and Windows equivalents (unquoted service paths,
   AlwaysInstallElevated, local admin membership, etc.).

**Read `range_host_audit.py`'s own docstring before trusting its
output**: `qm guest exec` runs as the guest's privileged service
account (root/SYSTEM), so the audit sees everything — that's the point,
since every check exists to find conditions an *unprivileged* actor
could abuse. The audit result is evidence a condition exists, not
evidence it's been exploited; that's the PoC step's job.

Every finding, from either phase, is tracked the same way in
`range_traversal.py`'s state, tagged `"scope": "network"` or
`"scope": "local"`.

## Usage — single host, one pass through the loop

```
# 1. Clone + start the working image (proxmox_bridge.py, not this skill)
curl -X POST http://host.docker.internal:8811/vm/clone -d '{"template_vmid": 9001, "new_vmid": 1234, "name": "example-linux-host-ctf"}'
curl -X POST http://host.docker.internal:8811/vm/1234/start

# 2. Scan it — network, then local
python3 scripts/range_discover.py scan --target <working clone's ip>
python3 scripts/range_host_audit.py audit --golden-vmid 9001 --working-vmid 1234 --os Linux

# 3. For each finding, get the task prompt — this does NOT call a model
python3 scripts/range_poc.py assign \
    --golden-vmid 9001 --working-vmid 1234 --host example-linux-host \
    --finding-id f-abc123 --scope network --context-file /workspace/ctx_f-abc123.json

# 4. YOU do the real work with YOUR OWN tools (network for scope=network,
#    proxmox_bridge.py guest-exec calls for scope=local), write your
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
python3 scripts/range_traversal.py init --config ../config/hosts.json
python3 scripts/range_traversal.py next
python3 scripts/range_traversal.py record-scan --vmid <golden_vmid> --working-vmid <id> --target <ip> --discover scripts/range_discover.py
python3 scripts/range_traversal.py record-audit --vmid <golden_vmid> --working-vmid <id> --audit scripts/range_host_audit.py
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

**`next` walks ONE host at a time, in config order, to completion**
before moving to the next host's `clone_and_scan` — not round-robin
across hosts, and not round-robin across a single host's findings
either: it returns the first non-terminal finding in discovery order
and won't surface a later finding until that one reaches a terminal
status (`poc_not_reachable`, `poc_inconclusive`, `reverified_closed`,
or `review_needed`). This is deliberate, same principle as
`ctf_traversal.py`'s own single-problem-at-a-time handoff — it keeps
exactly one thing in flight per host at any moment.

`next`'s possible actions: `clone_and_scan`, `local_audit`,
`assign_poc`, `assign_remediate`, `assign_reverify`, `review_needed`,
or `{"done": true}`. `review_needed` means a remediation did not close
the finding on reverify (`reverify_failed`) — read `status` for that
finding's existing `remediation_summary`/evidence before trying
`assign-remediate` again, so the next attempt is actually informed by
what didn't work the first time, not a repeat of the identical
proposal.

**`next` deliberately does not drive the clone/start step itself** —
unlike ksnctf's near-free "fetch the next problem," cloning a Proxmox VM
can take 30-60+ minutes, so this engine treats "is the working clone up"
as a precondition it checks (`network_scan_status == "not_started"`)
rather than a step it drives. When `next` returns
`{"action": "clone_and_scan", ...}`, that's your cue to call
`proxmox_bridge.py` directly, then `record-scan` once the clone is
reachable.

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

Two rules that prevent this entirely:

1. **Issue `/vm/clone` and `/vm/<vmid>/start` as a background command
   from the very first attempt** — never foreground-first-and-fall-
   back. Then poll or wait on that one background call for its actual
   result, rather than guessing from a local timeout.
2. **A failed, timed-out, or ambiguous clone/start response is not
   proof the VM wasn't created.** Before deleting anything, check
   whether the vmid already exists and is further along than the
   error suggests (e.g. try `/vm/<vmid>/start` and read what kind of
   failure comes back — "doesn't exist yet" and "already running"
   fail differently) — or just wait and re-check, rather than sending
   a second mutating call for the same vmid.

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
   the guest-exec probe succeeds, each check's command actually runs
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
