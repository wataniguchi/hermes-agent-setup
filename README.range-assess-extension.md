# range-assess — extension to hermes-agent-setup

This is an overlay, not a standalone package: every path below mirrors
your real repo's own layout exactly, so you can drop this directory
tree directly into your checkout (it will not collide with anything
that already exists, except the one file noted as "patched"). For the
history of what broke along the way and how each thing was fixed, see
`CHANGELOG.range-assess-extension.md` — this file states only where
things stand now.

## Architecture, in one paragraph

Every host gets scanned two ways — over the network
(`range_discover.py`) and from inside the guest over SSH as
`root`/`Administrator` (`range_host_audit.py`, new — see the sixth
"gap found" entry below for why it's SSH and not Proxmox's guest-exec
channel, which is where it started) — because a
network-only scan misses everything local privilege escalation depends
on (SUID binaries, world-writable files a cron job reads, sudoers
misconfig, etc.). For each finding from either phase, `range_poc.py`
and `range_remediate.py` hand YOU (the agent/Hermes session) a filled
task prompt and validate/log whatever result you report back — neither
script calls a model itself. **"The local Qwen model does PoC/
remediation" means the agent's own backend model, acting through its
own normal tool-calling turn** (real network access, or an SSH session
as `root`/`Administrator` on the working clone) — not a hidden sidecar call.
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
  - `scripts/range_host_audit.py` — **new**: local enumeration over SSH
    as `root` (Linux) / `Administrator` (Windows), using the `hermes`
    sandbox's own keypair, authorized on every target host
    (installed packages, kernel version, SUID/SGID binaries,
    world-writable files/dirs, cron/sudoers, loopback-only listening
    sockets, and Windows equivalents). Read-only enumeration only — no
    guest-state mutation, no exploitation attempt. Its own docstring
    explains the privilege caveat worth understanding before trusting
    any output: it connects as `root`/`Administrator`, so this script
    sees everything on purpose, to find conditions an *unprivileged*
    actor could abuse — seeing a condition is not the same as having
    demonstrated it's exploitable.
  - `scripts/range_status_report.py` — **new**: a read-only statistics
    report over `/workspace`'s actual on-disk state for a human to read
    directly (defaults to text, not JSON, unlike every other script
    here — `--json` for machine-readable). Goes beyond
    `range_traversal.py status`'s own host/finding counts: PoC and
    remediation attempt-log stats (totals, average attempts/finding,
    which findings are at the attempt cap), a timing span across both
    logs, and — directly motivated by the result-file-naming gap above
    — a cross-check that every finding past the PoC/remediation stage
    actually has its canonical `result_*.json`/`remediation_result_*.json`
    file on disk under the working-vmid naming scheme, flagging any
    that are missing or still sitting under a stale golden-vmid name
    from before that fix.
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

This section states only where things stand *now*. The story of what
broke along the way and exactly how each thing was fixed — useful for
"why does the code do this?" questions, not for deciding whether
something currently works — is kept separately in
`CHANGELOG.range-assess-extension.md`.

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


