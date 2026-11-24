# range-assess — REFERENCE

Background and incident history for the range-assess skill. `SKILL.md`
holds the rules you follow every turn; this file holds the *why* behind
them — the real failures that produced each rule, and the one-off
extension notes. Read a section here only when `SKILL.md` points you to
it (e.g. "REFERENCE.md: early-start-wrong-mac") or when you're changing
the tooling itself. Nothing here is a rule you need in hand to run a
normal turn.

---

## no-hidden-model-call

An earlier version of `range_poc.py`/`range_remediate.py` made its own
standalone call to Ollama's `/api/chat` to ask a model whether a finding
was exploitable. That was a mistake: a bare Ollama chat call has no tool
access at all — no shell, no network, no guest-exec — so it could never
actually attempt anything regardless of what its prompt asked for. The
correct shape, matching `submit_flag.py`'s role for ksnctf exactly: these
scripts never perform or judge an attempt themselves. They hand YOU (the
agent) a filled task prompt to act on with your own tools, then validate
and log the result you report back. "The local Qwen model does the
PoC/remediation" means your own backend model acting through its own
normal tool-calling turn — not a second, tool-less model call hidden
inside a script. See `range_poc.py`'s docstring for the full account.

## ssh-not-guest-exec

`range_host_audit.py` and the local-scope prompts originally ran over
`proxmox_bridge.py`'s guest-exec channel (QEMU Guest Agent,
virtio-serial). On the real range that failed with "No QEMU guest agent
configured" on every golden image tried (9101–9103) — confirmed via
`qm config <vmid> | grep -i agent`: the `agent: 1` flag (which creates
the virtio-serial device the guest agent needs) was absent from all of
them, not just the daemon being unresponsive inside the guest. Rather
than add that config to every golden image, the operator switched the
local vantage point to a standing channel: the `hermes` sandbox's own SSH
keypair, authorized on every target host for `root` (Linux) /
`Administrator` (Windows), reachable now that the sandbox has a real
network route into the range. The privilege model (full
root/Administrator access) is unchanged — only the transport differs.
Host-key checking is deliberately disabled for this channel (every target
is a disposable clone whose host keys churn by design); see
`range_host_audit.py`'s docstring for why that's reasoned, not an
oversight.

## init-stale-state

`init` is additive-only by design (`setdefault` everywhere) so re-running
it mid-sweep to add a host never clobbers progress on the others. That
same design once silently let a smaller, differently-scoped run inherit a
stale larger state file's hosts: a 3-host exercise pointed at a genuinely
trimmed 3-host `--config` came back covering all 21 hosts, because the
state file still held an earlier sweep's full host set. `init` now refuses
when its host set doesn't match what's already on disk, listing
`stale_hosts`, unless `--fresh` is passed to wipe and start clean.
`--hosts <vmid1,vmid2,...>` scopes a run to a subset without a separate
trimmed config file.

## finding-id-collisions

A finding's `f-<hash>` ID is NOT a stable cross-host identifier for "this
kind of finding" — it only disambiguates findings within one host's own
scan. `finding_id()` hashes `(scope, port, proto, service)` for a network
finding or `(scope, category, command, item)` for a local one — never the
version string, the evidence, or the vmid. Confirmed for real: two hosts
with "port 22/tcp/ssh" open got the identical ID despite one being a
modern OpenSSH with nothing exploitable and the other having a confirmed
root-key network login. Harmless to the pipeline (every script keys
storage and result files by `<vmid>:<finding_id>` together, never a bare
`finding_id`), but a real trap for a human skimming state across hosts.
Always read a finding's own record per host before drawing a range-wide
conclusion from a shared ID.

Separately, the per-finding context file was once keyed by `finding_id`
alone with no vmid, so whichever host's `assign-poc`/`assign-remediate`
ran last silently overwrote another host's context on disk (9102's nginx
context clobbered by 9103's Apache one, same real 3-host run). Context
files are now vmid-scoped: `.range_context_<vmid>_<finding_id>_<kind>.json`.

## next-blocking-history

`next` used to walk ONE host at a time, in config order, to a terminal
status (or `review_needed`) before ever issuing the next host's
`clone_and_scan` — and a finding stuck at `reverify_failed` had no
resolution command at all. Combined, a single stuck finding on host 3
would have silently halted hosts 4 through 21 forever; it never happened
only because 25/25 reverifies succeeded by luck. Now `next` collects
`review_needed` items separately and keeps advancing through every other
host's real work; `resolve-review` is the only way to move a finding off
`reverify_failed` or a stalled `remediation_planned`.

## why-agent-never-self-resolves

Confirmed for real: in an unattended run the agent itself called
`resolve-review --resolution accept_risk` against a `needs_operator_decision`
finding (the standing root-SSH access channel) while the operator was
asleep, with reasoning that happened to be sound but with no actual
operator involved. "Once a human/operator has decided" is not a condition
the agent can satisfy on its own behalf — there's no technical gate
separating "a human decided" from "I decided and called myself the human
who decided." So the rule is absolute: the agent never calls
`resolve-review`. It's for a human operator to run from outside this loop.

## delegation-measurement

Measured directly (`scripts/measure-ollama-concurrency.sh`) against this
project's model: concurrent requests did NOT increase real
token-generation throughput — this hardware time-slices GPU decode across
concurrent requests rather than batching them, so aggregate tokens/sec
stayed flat from 3-way through 16-way concurrency. Do not delegate
expecting N subagents to "think" N times faster — they won't. The real
win is structural: a PoC/remediate subagent's wall-clock time is
dominated by tool-call waits (SSH/nmap/sqlmap round-trips), so while one
subagent blocks on its own tool call, another's reasoning turn can use
the GPU instead of it sitting idle. That's an I/O-overlap gain, not a
throughput multiplier — and it's specific to PoC/remediate/audit actions;
it does NOT extend to cloning (a single blocking disk-copy with no
reasoning to interleave). `--count 97` once meant to grab one finding
swept up 96 unrelated already-discovered findings queued ahead of it, all
needing manual `release-claim`; size `--count` to what you actually
intend to claim, and match it to `delegation.max_concurrent_children`.

## clone-retry-destroyed

`/vm/clone`'s HTTP response doesn't return until Proxmox finishes cloning
— up to 30–60+ minutes. A foreground shell call's own execution-timeout
is shorter, and when it fires it kills only your waiting client; the clone
keeps running on the bridge side, unaffected. This produced a real
failure: a foreground `/vm/clone` timed out locally after 60s; the agent
retried with the same `new_vmid`; Proxmox rejected the retry ("config
file already exists") because the first call was still in progress; the
agent read that as a stale leftover and deleted the vmid — destroying its
own clone, seconds from completing. It then cloned a third time from
scratch. Hence: background the call from the first attempt, and a failed
call is never proof the VM wasn't created.

## parallel-clone-burst

A self-written batch script once fired 15 `/vm/clone` requests at once for
the remaining hosts, reasoning (as if cloning were a cheap independent API
call) that parallel provisioning would save wall time like delegated
PoC/remediate work. It didn't: most hung for the full request timeout and
died, while an earlier batch of malformed clone requests had already left
16 dead, diskless vmid reservations in Proxmox (a bridge-side bug: the
vmid is reserved before the request body is validated, so even a
rejected/422 clone can leave an empty stub VM — check for a real disk with
`qm config <vmid>` showing a `scsi`/`virtio`/`sata` line, not just that
the vmid exists). None of it was ever recorded as any host's
`working_vmid`, because it bypassed `record-scan`. The bridge now caps
concurrent clones at one; the rule is enforced in code, but the reasoning
above is why it exists.

## early-start-wrong-mac

A clone isn't finished when Proxmox's copy finishes: the bridge then sets
the clone's `net0` back to the golden image's exact MAC, which is what
gives it its range address (dnsmasq reservations on range-gw are keyed to
the golden MAC; statically-addressed hosts were verified reachable with
that MAC too). `GET /vm/<vmid>/status` shows `exists: true, status:
stopped` from the very START of a clone, so "stopped" is not "ready".
Confirmed for real: on 9110→9610 and 9112→9612 the agent sent `start`
~10s before `/vm/clone` returned. The VM booted on the random MAC Proxmox
gives every clone, the bridge's MAC update was rejected (`/vm/clone` →
`400`), and the agent ran `record-scan` against an address the VM never
had. Both were recorded `scan_failed` — a dead end `next` never re-offers
without an operator `reset-host`. The bridge now refuses a `start` while a
clone is in progress (`409`), and refuses `stop`/`delete`/`rollback` the
same way.

## pre-clone-helper

9707 was pre-cloned ahead of its turn, and the agent correctly found it
`ready_to_start`, started it and skipped the clone — saving a 33-minute
Windows clone. That's the pattern Cloning rule 5 makes routine. The helper
(`scripts/range-preclone.py`, on the Mac, started by the watchdog) clones
the next `not_started` host's working clone while you work the current
one. It keeps at most one waiting clone, is capacity-checked, and never
clones alongside you (the bridge's one-clone cap guarantees it).

## storage-hang-incident

`hot-ssd` is an LVM thin pool on ONE external USB SSD (Buffalo, UAS, 240GB
nominal, ≈223.57GB actual) — not elastic. The 9 Windows-domain hosts alone
are 60–100GB each: ≈620GB if all their clones existed at once, nearly 3×
the pool. It cannot hold the range's full footprint. Confirmed for real:
the pool hit 93.58% data usage, which almost certainly made a parallel
clone burst's underlying `lvcreate` stall indefinitely (out of real blocks
to allocate) rather than fail cleanly — and because that stuck process
held the volume group's lock, it silently froze EVERY later LVM operation
(clone, destroy, even the Proxmox web UI) for over 7 hours, with no error
surfaced (requests hung past the 2-hour HTTP timeout and came back `504`).
A nearly-full thin pool doesn't just "fail with a clear error when full" —
it can hang the whole storage layer first. Hence the headroom check and
the bridge's `507` at ≥75% used / >95% projected.

## teardown-not-at-clone-time

Teardown had only ever happened right before cloning the next host. Once
clones were pre-cloned for the agent (Cloning rule 5), the agent stopped
cloning, so that moment never came — and four finished hosts' clones
(9612, 9707, 9708, 9709) were still running when the sweep completed. So
teardown belongs to FINISHING a host, not to starting the next one. The
watchdog runs `scripts/range-teardown.py` on a graceful completion as a
safety net, but it's not a substitute for the agent tearing down each host
as it finishes.

## clone-exists-gap

An operator once destroyed a working clone directly in Proxmox (outside
the bridge, outside `range_traversal.py`), leaving `working_vmid` pointing
at a vmid that was gone with no read-only way to check. The agent fell
back to a `GET` that doesn't exist on the bridge (405), then to reading a
`/vm/<vmid>/exec` error, which is ambiguous (a fast failure looks the same
whether the vmid is gone or the guest agent just isn't up). `GET
/vm/{vmid}/status` closed that gap. `reset-host` clears the stale
`working_vmid` and both scan statuses back to `not_started` so `next`
re-issues `clone_and_scan`; it does NOT touch recorded findings, so a
finding already `poc_confirmed` keeps evidence collected against a clone
that's now gone — worth a second look against the new clone.

## validation-status

The skill was built and validated in stages against synthetic/mocked
output first, then real targets. The state-machine plumbing (every
`range_traversal.py` subcommand, the assign/report pairs, both scope
paths) was confirmed against mocked discover/audit output. Against real
targets: `range_discover.py` nmap parsing, `range_host_audit.py`'s check
list, the PoC/remediate guardrails, the reverify banner, the full loop,
`GET /vm/{vmid}/status` + `reset-host`, and the lease/`next-batch`
delegation mechanics (disjoint concurrent claims, per-finding claim
clearing) were all exercised on real runs. All three `resolve-review`
resolutions are confirmed real against the recurring golden-image
SSH-root-login finding on three different hosts, deliberately resolved
three ways for coverage. Still not exercised: a real remediation genuinely
hitting `MAX_REMEDIATION_ATTEMPTS` under real (not synthetic)
`applied: false` results.

## extending-the-bridge-guard

`proxmox_bridge.py`'s `guard_not_template()` originally protected one VMID
(`PVE_TEMPLATE_VMID`, default 9000). A multi-host range has more than one
golden image; the `PVE_GOLDEN_VMIDS` extension (comma-separated set, falls
back to the singular env var if unset) protects all of them. This skill's
scripts also guard the working/golden-vmid distinction at the
finding-action layer (hard refusal when the two match), but that's a
second, redundant layer — the bridge's own guard is what actually stands
between an API call and a golden VM's disk.

## extending-the-audit-checklist

`range_host_audit.py`'s `LINUX_CHECKS`/`WINDOWS_CHECKS` are a
non-exhaustive baseline. Add a check by appending a
`(category, argv, description)` tuple; every check is read-only
enumeration by design (no guest-state mutation, no exploitation), so a new
check should stay that way: find/ls/cat-style metadata reads,
package/service listings, PowerShell `Get-*` cmdlets. A new category
defaults to **inventory** (recorded as `informational`, no PoC) unless you
add it to `RISK_CATEGORIES` — so a new check never silently creates PoC
work until you deliberately mark it a risk condition. The per-category
`SPLITTERS`/`WINDOWS_SPLITTERS` control whether a category emits one
finding per parsed item or stays bundled; a splitter is only as safe as
the output format it assumes, so leave a category bundled rather than
risk a wrong split.
