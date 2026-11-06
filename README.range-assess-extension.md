# range-assess — extension to hermes-agent-setup

This is an overlay, not a standalone package: every path below mirrors
your real repo's own layout exactly, so you can drop this directory
tree directly into your checkout (it will not collide with anything
that already exists, except the one file noted as "patched").

## Architecture, in one paragraph

Every host gets scanned two ways — over the network
(`range_discover.py`) and from inside the guest via Proxmox's
guest-exec channel (`range_host_audit.py`, new) — because a
network-only scan misses everything local privilege escalation depends
on (SUID binaries, world-writable files a cron job reads, sudoers
misconfig, etc.). For each finding from either phase, `range_poc.py`
and `range_remediate.py` hand YOU (the agent/Hermes session) a filled
task prompt and validate/log whatever result you report back — neither
script calls a model itself. **"The local Qwen model does PoC/
remediation" means the agent's own backend model, acting through its
own normal tool-calling turn** (real network access, or real calls to
`proxmox_bridge.py`'s guest-exec endpoint) — not a hidden sidecar call.
An earlier version of this extension got that wrong: it had
`range_poc.py`/`range_remediate.py` call Ollama directly with a
tool-less one-shot prompt, which could never have actually attempted
anything regardless of what the prompt asked for. See `range_poc.py`'s
own docstring for the full account of that mistake and the fix.

## What's genuinely new (all files, none existed in your repo before)

- `skills/security/range-assess/` — the whole new skill:
  - `SKILL.md` — usage for both the single-host and multi-host-sweep
    shapes, the two-phase assign/report flow, and a staged validation
    plan.
  - `config/hosts.json` — **a sanitized template, not your real
    range**: a `_schema` block documenting every field, plus two dummy
    entries (one Linux, one Windows) showing the shape. Keep your real,
    filled-in file elsewhere (untracked) and pass it explicitly via
    `--config`.
  - `scripts/range_discover.py` — stateless nmap-or-fallback network
    scan of one target. Unchanged in shape from the first draft of this
    extension.
  - `scripts/range_host_audit.py` — **new**: local enumeration via
    `proxmox_bridge.py`'s `POST /vm/{vmid}/exec` guest-exec channel
    (installed packages, kernel version, SUID/SGID binaries,
    world-writable files/dirs, cron/sudoers, loopback-only listening
    sockets, and Windows equivalents). Read-only enumeration only — no
    guest-state mutation, no exploitation attempt. Its own docstring
    explains the privilege caveat worth understanding before trusting
    any output: `qm guest exec` runs as the guest's privileged account
    (root/SYSTEM), so this script sees everything on purpose, to find
    conditions an *unprivileged* actor could abuse — seeing a condition
    is not the same as having demonstrated it's exploitable.
  - `scripts/range_poc.py`, `scripts/range_remediate.py` — **rewritten**
    from an earlier, mistaken "call Ollama directly" shape into
    `assign`/`report` guardrail scripts (the pattern `submit_flag.py`
    already uses for ksnctf): `assign` fills and hands out a
    scope-appropriate task prompt and enforces the attempt
    cap/delay/golden-vmid guards; `report` validates and logs whatever
    result you report back after actually doing the work. No model call
    in either file.
  - `scripts/range_traversal.py` — the orchestrator, modeled on
    `ctf_traversal.py`'s lock-file state pattern, extended with a
    `record-audit` subcommand (for the new local-audit phase) and
    `assign-*`/`report-*` pairs (instead of single blocking calls) for
    `poc`/`remediate`/`reverify`.
  - `prompts/poc_task_prompt_network.md`, `poc_task_prompt_local.md`,
    `remediation_task_prompt.md` — the actual text handed to you for
    each step. Scope and safety constraints and a required JSON output
    shape only, no technique content anywhere.
- `scripts/range-sweep-init-prompt.txt` / `range-sweep-resume-prompt.txt`
  (top-level, alongside your existing `ctf-sweep-*-prompt.txt` files) —
  the operational prompts fed to Hermes to run the loop autonomously,
  updated for the two-phase assign/report shape and the local-audit
  step, with the division-of-labor reminder (you do the real work,
  nothing happens automatically between `assign` and `report`) stated
  up front.
- `scripts/range-sweep-watchdog.sh` — the outer-loop launcher,
  mirroring `ctf-sweep-watchdog.sh`'s mechanism: headless
  `hermes -p <profile> -z <prompt>` oneshot calls, auto-resume on any
  exit, init/resume prompt selection, session-id discovery, transcript
  archiving, and (see below) a periodic session-log monitor companion.
  No watchdog-side note distillation, though — see its own header
  comment for why (briefly: `range_traversal.py`'s state is structured
  JSON with nothing comparable to distill, unlike `ctf_traversal.py`'s
  free-text per-problem notes).
- `AGENTS.md.range-assess-addition.md` — a suggested new section for
  your real `AGENTS.md`, delivered separately since I don't know its
  exact current shape.

## What's patched (two existing files, minimally)

- `proxmox/proxmox_bridge.py` — three independent changes:
  - `guard_not_template()`/`PVE_TEMPLATE_VMID` generalized to a
    `PVE_GOLDEN_VMIDS` set, backward-compatible if unset.
  - Its request-logging middleware now prints local wall-clock time
    (whatever timezone the Mac Studio this runs on is actually set to,
    via `datetime.now().astimezone()`) instead of always `...Z` (UTC) —
    e.g. `2026-11-04 21:49:08.905+0900` instead of
    `2026-11-04 12:49:08.905Z`. Pure logging change, no behavior
    difference. Diff it against your original to confirm — every other
    line is untouched.
  - `clone_vm()` now transplants the golden image's own `net0` line
    (MAC included) onto every clone — see the fourth "gap found" entry
    below for why.
- `scripts/ctf-sweep-monitor.sh` — its session-id file, originally
  hardcoded to the ksnctf sweep's own
  `.ctf-sweep-current-session-id`, is now a `--session-id-file <path>`
  flag, **defaulting to that exact same hardcoded path** — so
  `ctf-sweep-watchdog.sh`'s own unmodified call to this script behaves
  identically to before. `range-sweep-watchdog.sh` reuses this same
  script (not a forked copy) by passing its own
  `--session-id-file workspace/.range-sweep-current-session-id`. Diff
  it against your original: the only changes are that one flag, the
  usage/help text, and the `watch` re-invocation line that now also
  passes it through.

## Explicit division of labor, carried through every file above

Claude wrote all orchestration, scanning/enumeration, state management,
and the methodological prompt templates. Nothing here contains exploit
code or technique, and nothing here calls a model — `range_poc.py`/
`range_remediate.py` hand that work to YOU, the agent, through your own
normal tool-calling turn, the same way `ctf-solver` already delegates
pwn/web/crypto technique work to you on ksnctf.

## Verified so far, and what's still genuinely untested

Every Python file compiles clean, `hosts.json` is valid JSON, and the
full `range_traversal.py` state machine — both scan phases, both scopes
(network and local), the full assign→report cycle for PoC and
remediation, reverify, and every guardrail refusal (golden/working
mismatch, empty evidence, the attempt cap, the min-delay) — was
exercised end-to-end against synthetic/mocked scan and audit output and
confirmed to behave correctly. What's still genuinely untested is
everything downstream of a REAL target: whether `range_discover.py`'s
nmap parsing and `range_host_audit.py`'s check list produce something
useful against this range's real hosts, and the actual
exploitation/remediation attempts themselves, since no script can
simulate those. `SKILL.md`'s "What's genuinely untested here" section
gives a staged validation order — one host, one low-risk finding first.

**A second real gap found on an actual run, now fixed**: against a real
Proxmox clone, a foreground `/vm/clone` call's own execution-timeout
fired well before the clone itself finished (the call blocks for the
clone's full real duration server-side, independent of what happens to
the client). The agent read that local timeout as failure, retried
with the same `new_vmid`, got a real rejection from Proxmox because
the first call was still genuinely in progress, misread that
rejection as a stale leftover, and deleted its own first call's
clone — which was seconds from succeeding — forcing a third clone
from scratch. Fixed in `range-sweep-init-prompt.txt` (steps b2/b3
under `clone_and_scan`) and `SKILL.md` (new "Cloning: always
background it..." section): always background the `/vm/clone`/
`/vm/<vmid>/start` calls from the first attempt, and never delete a
vmid on a failed/ambiguous response without first checking whether it
actually exists further along than the error suggests.

**A related, separate operational gap, now documented rather than
"fixed"** (there's no code-level fix available from this side): Ctrl-C
on `range-sweep-watchdog.sh` only stops the oneshot `hermes -z` CLI
process — confirmed directly that it does NOT reach a
`terminal(background=true)` job the agent itself launched mid-attempt
(e.g. a backgrounded clone/start chain, or a polling loop hitting
`/vm/<id>/exec`). Those are owned by Hermes's own long-lived gateway
process and keep running, and keep calling the bridge, regardless.
Restarting the bridge process doesn't stop it either — there's nothing
server-side to lose, something is still actively sending requests from
inside the sandbox container. `range-sweep-watchdog.sh`'s header
comment and its interrupted-attempt message now explain this and give
the actual fix (`docker ps` / `docker exec <container> ps -ef` / kill
the offending pid, or `docker restart <container>`), since the
watchdog has no visibility into, or any way to reach, a process the
agent backgrounded on its own.

**A third real gap found live, now fixed**: an operator manually
destroyed a working clone directly in Proxmox (outside the bridge,
outside `range_traversal.py`), leaving the agent with a stale
`working_vmid` in state and no read-only way to check reality — the
bridge had no status/list endpoint at all, only mutating calls, so the
agent fell back to guessing from an ambiguous `exec` error. Fixed with
two additions: `proxmox_bridge.py` gets a new read-only
`GET /vm/{vmid}/status` (`{"exists": false}` for a destroyed vmid,
status/name/uptime otherwise — deliberately NOT behind the
golden-image guard, since it's harmless even against a golden vmid);
`range_traversal.py` gets a new `reset-host` subcommand that clears a
host's stale `working_vmid` and both scan statuses back to
`not_started` (leaving already-recorded findings untouched) so `next`
issues a fresh `clone_and_scan` instead of reusing a vmid that's gone.
Verified the state-transition logic end-to-end with synthetic state
(reset clears the right fields, leaves findings/evidence alone,
records a `reset_history` entry, and `next` correctly re-issues
`clone_and_scan` afterward) and cleaned up every test artifact
afterward. **Not yet verified**: the status endpoint's `exists: false`
detection was written from error text Proxmox returned in a different
call (a start attempt on an already-destroyed vmid) — confirm the
actual error text/code for `GET .../status/current` on a genuinely
nonexistent vmid against your real Proxmox before trusting that
branch; broaden the match in `vm_status()` if it's phrased differently.

**A fourth real gap found live, now fixed and confirmed working**: a
real clone's NIC came up with no IP and, separately, `enp0s18` wasn't
even administratively up — both traced to one root cause, not two.
Proxmox gives a full clone's NIC a freshly generated MAC by default;
this range's dnsmasq static reservations (`range-gw/dnsmasq.d/
range.conf`) are keyed to each golden image's own original MAC, so the
clone matched no reservation at all, and the guest's own network
config (consistent with a NetworkManager profile pinned to that exact
MAC) never activated for the new one either — one mismatch, two
visible symptoms. Fixed in `proxmox_bridge.py`'s `clone_vm()`: read the
template's own `net0` line at clone time and transplant it verbatim
onto the new vmid before returning, so every clone of a given golden
image carries that image's exact MAC (and therefore its exact dnsmasq
reservation and IP) every time, with no new per-vmid config to
maintain. Safe under the existing golden/working invariant — only one
working clone of a given golden vmid is ever live at once, so the MAC
is never duplicated on the wire. **Confirmed working against the real
range**: after redeploying, destroying the stale clone, and re-cloning,
the host's network came up correctly with no manual intervention.

**One real gap found and fixed after initial delivery**: reverify's
assigned prompt originally carried no signal that it was a
reverification at all — same prompt as the first attempt, with no
mention of the method/evidence that originally confirmed the finding
or the remediation just applied, and (for local findings) no flag that
the audit evidence shown predates the fix. A reverify built that way
only proves "I tried something and it failed," not "the specific thing
that worked before no longer does" — a materially weaker claim. Fixed:
`range_poc.py assign --reverify` now renders a `## This is a
REVERIFICATION` banner carrying the original `poc_method`/
`poc_evidence` and the `remediation_summary`, pulled automatically by
`range_traversal.py` from stored state, with an explicit instruction to
repeat that specific method and an explicit caveat that other evidence
in the prompt may be stale. Verified end-to-end (banner absent on a
plain `assign`, present with the right values on `assign --reverify`,
including through the full `range_traversal.py` state-driven path, not
just a hand-built context file).
