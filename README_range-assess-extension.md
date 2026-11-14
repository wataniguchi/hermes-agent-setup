# range-assess extension — implementation on top of the CTF harness

A security-range assessment workflow built on top of
[README_CTF_harness.md](README_CTF_harness.md)'s shared patterns. For
the history of what broke along the way and how each thing was fixed,
see `CHANGELOG.range-assess-extension.md` — this file states only where
things stand now.

## What this solves

The underlying objective is to secure every host in the range against
how it could actually be compromised — by an adversary attacking it over
the network from outside, and by an adversary (or an offensive program
an adversary already implanted) operating locally on the host, trying to
escalate privilege or persist. Those are two different attack surfaces,
which is why there are two scan phases rather than one: a network scan
surfaces what's reachable from outside, and a local audit — run as
`root`/`Administrator` over SSH — surfaces what's exploitable from
inside, the same vantage point a local attacker or implant would
already have.

A scan alone only reports *theoretical* risk, though — a port that's
open, a binary that's SUID, a package that's out of date. None of that
says whether an attacker could actually get in through it, or whether a
fix someone applied actually closed the gap. This extension answers both
questions with evidence rather than assumption, for every host in the
range, unattended, by running the same loop on each one:

```
range_traversal.py drives this loop across every host in the range:

   clone + start a disposable working copy of the host
                     │
                     ▼
        ┌─────────────────────────┐
        │ 1. Scan                 │   network (range_discover.py) +
        │                         │   local audit over SSH as root/
        │                         │   Administrator (range_host_audit.py)
        └────────────┬────────────┘
                      │  candidate findings
                      ▼
        ┌─────────────────────────┐
        │ 2. Prove it (PoC)       │   range_poc.py: assign → agent attempts
        │                         │   the exploit with its own tools → report
        └────────────┬────────────┘
                      │  confirmed, evidence-backed findings
                      ▼
        ┌─────────────────────────┐
        │ 3. Remediate            │   range_remediate.py: assign → agent
        │                         │   applies the fix with its own tools →
        │                         │   report
        └────────────┬────────────┘
                      ▼
        ┌─────────────────────────┐
        │ 4. Reverify             │   range_poc.py --reverify: re-runs the
        │                         │   SAME PoC against the now-remediated
        │                         │   host
        └────────────┬────────────┘
                      ▼
          stop + destroy the working copy
```

Scanning (step 1) only produces *candidates* — a condition that looks
exploitable. Step 2 is what turns a candidate into a confirmed finding:
an actual attempt against the actual host, with real evidence, not a
severity score inferred from a scanner's signature match. Step 4 closes
the loop the same way: remediation is only trusted once the exact same
PoC that originally proved the risk is re-run against the fixed host and
fails to reproduce it — not because the fix was applied, but because
it's been shown to hold. `range_traversal.py` repeats this per-host loop
across the whole configured host list automatically, so a full range
sweep runs unattended the same way a ksnctf sweep does (see
[README_CTF_harness.md](README_CTF_harness.md)'s operator layer).

## Why this extension exists

The CTF harness's traversal engine and guardrail patterns were built
generally enough to apply to any "visit a list of targets, try things
against each, track state durably" problem — not only a CTF platform's
list of problems. This extension is the proof of that: the same
lock-file state-machine shape and the same assign/report guardrail shape
`ksnctf-submit` uses, reused against a genuinely different kind of
target — a set of Proxmox-hosted hosts in a security range, each
assessed for network- and local-privilege-escalation findings, rather
than a platform's list of challenge problems.

This is also a deliberately harder step than CTF: a CTF platform's own
submission check is a built-in oracle that says, definitively, whether a
candidate flag is correct; a security-range finding has no such oracle —
whether a PoC actually demonstrates exploitability, or a remediation
actually closes the gap, depends on the agent's own judgment, backed by
real evidence, rather than a platform's yes/no response. The harness was
deliberately hardened against CTF's oracle-backed problem set first, so
most of the traversal/guardrail machinery arriving here was already
proven before being asked to carry a harder, judgment-dependent domain.
See
[README_CTF_harness.md](README_CTF_harness.md#why-ctf-first-and-why-ksnctf)
for that reasoning in full.

## Architecture: what this extension adds

This extension supplies its own full skill
(`skills/security/range-assess/`) rather than slotting into the CTF
harness's existing skills, since the target domain (hosts with
findings, not problems with flags) doesn't match the harness's
ksnctf-shaped skill boundaries. It reuses the harness's *design*, not
its code, with one specific exception (`ctf-sweep-monitor.sh`, called
directly — see "Operational layer" below): `range_traversal.py` is its
own implementation of `ctf_traversal.py`'s lock-file state pattern, and
`range_poc.py`/`range_remediate.py` are their own implementation of the
same assign/report guardrail shape `ksnctf-submit` uses (see
[README_ksnctf-extension.md](README_ksnctf-extension.md)) — enforcing
attempt caps, delays, and scope guards in code, the same way, against a
different kind of action, but as entirely separate code from either
file. See
[README_CTF_harness.md](README_CTF_harness.md#whats-literally-shared-vs-whats-a-reused-design)
for exactly where that line falls across every piece of the harness.

None of the four stages above call a model themselves. "The local model
does PoC/remediation" means the agent's own backend model, acting
through its own normal tool-calling turn (real network access for a
network-scope finding, or an SSH session as `root`/`Administrator` on
the working clone for a local-scope one) — the same division of labor
`ctf-solver` already uses for ksnctf: this extension writes all
orchestration, scanning, and state management, and leaves all technique
work to the agent's own tool-calling turn. `range_poc.py` and
`range_remediate.py` only fill and hand out the task prompt for each
step (`assign`), then validate and log whatever result comes back
(`report`) — never attempting the work themselves.

## `range_discover.py` — network-phase scan

Stateless nmap-or-fallback network scan of one target, feeding the
network-scope half of the two-phase finding model below.

## `range_host_audit.py` — local-phase scan over SSH

Runs alongside `range_discover.py` in step 1 of the loop above because a
network-only scan misses everything local privilege escalation depends
on — SUID binaries, world-writable files a cron job reads, sudoers
misconfig, and the like are all invisible from outside the host.

Local enumeration over SSH as `root` (Linux) / `Administrator`
(Windows), using the `hermes` sandbox's own keypair, authorized on every
target host (installed packages, kernel version, SUID/SGID binaries,
world-writable files/dirs, cron/sudoers, loopback-only listening
sockets, and Windows equivalents). Read-only enumeration only — no
guest-state mutation, no exploitation attempt. Its own docstring explains
the privilege caveat worth understanding before trusting any output: it
connects as `root`/`Administrator`, so this script sees everything on
purpose, to find conditions an *unprivileged* actor could abuse — seeing
a condition is not the same as having demonstrated it's exploitable.

This runs over SSH, not Proxmox's guest-exec channel — the golden images
never had the QEMU guest agent configured at all, confirmed via `qm
config | grep -i agent` returning nothing on every one checked (see
`CHANGELOG.range-assess-extension.md`'s sixth entry). The privilege
model is the same full root/Administrator visibility either way; only
the transport changed. Host-key checking is deliberately disabled for
this channel — see `range_host_audit.py`'s own docstring for the
reasoning (a private, operator-controlled range; targets are disposable
clones this skill itself destroys and re-creates routinely, so host keys
churn by design).

## `range_poc.py` / `range_remediate.py` — guarded assign/report

`assign`/`report` guardrail scripts, the same pattern `ksnctf-submit`
uses: `assign` fills and hands out a scope-appropriate task prompt and
enforces the attempt cap/delay/golden-vmid guards; `report` validates
and logs whatever result you report back after actually doing the work.
No model call in either file.

Every `report` call also writes a canonical copy to
`/workspace/result_<working_vmid>_<finding_id>.json` (or
`remediation_result_...`), regardless of what `--result-file` path was
actually passed — matching the attempt-log's own key scheme
(`working_vmid:finding_id`), so a human-readable result file can always
be cross-referenced against its log entry without knowing a
golden↔working vmid mapping by heart.

## `range_traversal.py` — the orchestrator

Built on [README_CTF_harness.md](README_CTF_harness.md)'s
`ctf_traversal.py` lock-file state pattern, with a `record-audit`
subcommand (for the local-audit phase) and `assign-*`/`report-*` pairs
(instead of single blocking calls) for `poc`/`remediate`/`reverify`.

## `range_status_report.py` — human-readable status

A read-only statistics report over `/workspace`'s actual on-disk state
for a human to read directly (defaults to text, not JSON, unlike every
other script here — `--json` for machine-readable). Goes beyond
`range_traversal.py status`'s own host/finding counts: PoC and
remediation attempt-log stats (totals, average attempts/finding, which
findings are at the attempt cap), a timing span across both logs, and a
cross-check that every finding past the PoC/remediation stage actually
has its canonical result file on disk under the working-vmid naming
scheme above, flagging any that are missing or still sitting under a
stale golden-vmid name.

## Operational layer

- `prompts/poc_task_prompt_network.md`, `poc_task_prompt_local.md`,
  `remediation_task_prompt.md` — the actual text handed to you for each
  step. Scope and safety constraints and a required JSON output shape
  only, no technique content anywhere.
- `scripts/range-sweep-init-prompt.txt` / `range-sweep-resume-prompt.txt`
  — the operational prompts fed to Hermes to run the loop autonomously,
  covering the two-phase assign/report shape and the local-audit step.
- `scripts/range-sweep-watchdog.sh` — the outer-loop launcher, using
  [README_CTF_harness.md](README_CTF_harness.md)'s `ctf-sweep-watchdog.sh`
  mechanism: headless `hermes -p <profile> -z <prompt>` oneshot calls,
  auto-resume on any exit, init/resume prompt selection, session-id
  discovery, transcript archiving, and a periodic session-log monitor
  companion. Its own file, not the CTF harness's shared watchdog reused
  as-is — unlike ksnctf, this extension needs a separate watchdog
  because it has no watchdog-side note distillation: `range_traversal.py`'s
  state is structured JSON with nothing comparable to distill, unlike
  `ctf_traversal.py`'s free-text per-problem progress notes (see the
  watchdog's own header comment for the full reasoning).
- `AGENTS.md`'s range-assessment section — current scope and the
  SSH-based local-audit approach described above.

## Shared-infrastructure touch points

Two files outside this skill's own directory carry functionality this
extension depends on:

- `proxmox/proxmox_bridge.py`:
  - **`PVE_GOLDEN_VMIDS`** — a set of protected vmids (generalized from
    a single template vmid) that `guard_not_template()` refuses to
    mutate, covering every golden image this extension clones from, not
    just one.
  - **Request-logging middleware prints local wall-clock time**
    (whatever timezone the Mac Studio this runs on is actually set to,
    via `datetime.now().astimezone()`) instead of always `...Z` (UTC) —
    e.g. `2026-11-04 21:49:08.905+0900` instead of
    `2026-11-04 12:49:08.905Z`. A logging-only property; no behavioral
    difference anywhere else in the bridge.
  - **`clone_vm()` transplants the golden image's own `net0` line** (MAC
    included) onto every clone, so a clone's network identity always
    matches its golden image's dnsmasq reservation with no manual
    per-vmid configuration. See
    `CHANGELOG.range-assess-extension.md`'s fourth entry for the gap
    this closed.
- `scripts/ctf-sweep-monitor.sh` — takes a `--session-id-file <path>`
  flag, defaulting to the ksnctf sweep's own
  `.ctf-sweep-current-session-id` path so `ctf-sweep-watchdog.sh`'s own
  call to this script is unaffected. `range-sweep-watchdog.sh` reuses
  this same script (not a forked copy) by passing its own
  `--session-id-file workspace/.range-sweep-current-session-id` — one
  implementation, shared by both sweeps.

## Additional items

### Confirmed facts about the range environment

- **`config/hosts.json`** ships as **a sanitized template, not a real
  range**: a `_schema` block documenting every field, plus two dummy
  entries (one Linux, one Windows) showing the shape. Keep the real,
  filled-in file elsewhere (untracked) and pass it explicitly via
  `--config`.
- **Golden vs. working vmid is the same invariant the base Proxmox setup
  relies on** ([README.md](README.md) §12): only one working clone of a
  given golden vmid is ever live at once, so a clone's transplanted MAC
  (above) is never duplicated on the wire.
- **Every target host authorizes the sandbox's own SSH keypair** for
  `root`/`Administrator` — the same keypair baked into
  `docker/hermes-sandbox.Dockerfile` (see [README.md](README.md) §12),
  not a range-assess-specific credential.
- **Canonical result-file naming** keys every result by
  `working_vmid:finding_id`, matching the attempt log, specifically so a
  result file and its log entry can be cross-referenced without memorizing
  a golden↔working vmid mapping (see `range_poc.py`/`range_remediate.py`
  above).

### What's confirmed working vs. what's still genuinely untested

**Verified end-to-end with synthetic/mocked state**: the full
`range_traversal.py` state machine (both scan phases, both scopes, the
full assign→report cycle for PoC and remediation, reverify, every
guardrail refusal — golden/working mismatch, empty evidence, the
attempt cap, the min-delay — `reset-host`, and the local-scope `target`
plumbing), `range_poc.py`'s and `range_remediate.py`'s canonical
result-file naming, and `range_status_report.py`'s full set of checks.
Every Python file compiles clean and `hosts.json` is valid JSON.

**Confirmed working against the real range**: the sandbox's network
route into the range (Docker host networking); `proxmox_bridge.py`'s
`clone_vm()` MAC transplant — a cloned host's network now comes up
correctly, matching its golden image's dnsmasq reservation, with no
manual intervention; and the SSH local-audit path
(`range_host_audit.py`, and the local-scope halves of
`range_poc.py`/`range_remediate.py`'s task prompts) — the sweep now in
progress is producing real local-scope findings (SUID/SGID binaries,
world-writable files/dirs, cron entries, package/kernel versions, etc.)
across multiple real hosts, which only happens if the SSH connection,
command quoting, and privilege model are all actually working end to
end against those hosts.

**Still genuinely untested, or a known limitation, right now**:

- **`GET /vm/{vmid}/status`'s `exists: false` detection** was written
  from error text Proxmox returned in a *different* call (a start
  attempt on an already-destroyed vmid), not yet confirmed against
  this exact endpoint called on a genuinely nonexistent vmid.
- **`range-sweep-watchdog.sh`'s Ctrl-C/cleanup** does not reach a
  `terminal(background=true)` job the agent itself backgrounded
  mid-attempt (a backgrounded clone/start chain, or a polling loop) —
  confirmed directly; no code-level fix is available from this side.
  The watchdog's header comment and interrupted-attempt message explain
  the actual fix (`docker exec <container> ps -ef` / kill the pid, or
  `docker restart <container>`).
- **The actual exploitation/remediation attempts, and whether
  `range_discover.py`'s nmap parsing and `range_host_audit.py`'s check
  list produce something useful against this range's real hosts** — no
  script can simulate either; that's the agent's own tool-calling turn
  each time. `SKILL.md`'s "What's genuinely untested here" section
  gives a staged validation order — one host, one low-risk finding
  first.
