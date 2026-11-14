# CTF harness — generalized traversal, watchdog, and skill organization

Extends [README.md](README.md) with a platform-blind harness for running
Hermes against a CTF platform's full problem set autonomously, unattended,
for hours at a time. Built and hardened against ksnctf first (see
[README_ksnctf-extension.md](README_ksnctf-extension.md), which calls
this file's own `ctf_traversal.py`/`ctf-sweep-watchdog.sh` directly, as a
CTF-platform integration proper).
[README_range-assess-extension.md](README_range-assess-extension.md) is
different: it does **not** call `ctf_traversal.py` or
`ctf-sweep-watchdog.sh` (it calls one other script from this file's
operator layer directly — see below for which). Its target domain
(hosts with findings, not CTF problems) doesn't fit this engine's
problem-shaped state, so it re-implements the same lock-file
state-machine and assign/report guardrail *design* as its own separate
code — see "What's literally shared vs. what's a reused design" below
for exactly where that line falls. For the incident-by-incident story of
how each piece below was
hardened — including a recorded case of the agent bypassing its own
guardrails (see
[SPECIFICATION_GAMING_CASE_STUDY.md](SPECIFICATION_GAMING_CASE_STUDY.md))
— see [CHANGELOG_CTF-harness.md](CHANGELOG_CTF-harness.md). This file
states only where things stand now.

## Why CTF first, and why ksnctf

Among security-assessment problems generally, CTF challenges have a
property most real-world targets don't: a built-in oracle. A CTF
platform's own submission check tells you, definitively and
immediately, whether a candidate flag is correct — there's no ambiguity
about whether a finding is real, no human judgment call needed to decide
"did this actually work." That made CTF the right place to build and
harden a traversal/guardrail harness first: a bug in the harness itself
(state corruption, a guardrail bypass, exhaustion-detection lag) shows up
quickly and its fix is easy to confirm, against a problem set that's
lower-stakes than a live security assessment and immune to false
confidence about whether something actually worked. This is a
deliberate crawl-walk-run sequence: harden the harness against CTF's
oracle-backed problem set first (this file plus
[README_ksnctf-extension.md](README_ksnctf-extension.md)), then reuse the
same traversal/guardrail patterns against a genuinely harder, oracle-free
domain where "did this work" depends on the agent's own judgment instead
([README_range-assess-extension.md](README_range-assess-extension.md)).

Within CTF platforms, ksnctf specifically was chosen as the first
integration because it's publicly available, permanently up (no
seasonal/competition window to schedule around), and requires no
authentication to acquire problems or check a submitted flag —
minimizing everything about the integration that isn't the harness
itself. See
[README_ksnctf-extension.md](README_ksnctf-extension.md#why-this-extension-exists)
for that integration's own rationale.

## The key design insight

**Most CTF analysis work doesn't need the isolated Windows VM at all.**
Ghidra (and `pyghidra`) analyzes a binary as data, not as code it
executes — it's Java-based and works identically on Linux regardless of
what platform the *target* binary was built for. The same is true of
`ilspycmd`, a cross-platform .NET Core tool despite decompiling Windows
`.exe`s. The only thing that genuinely requires the Windows VM is dynamic
execution — actually running the target binary. This split is fully
implemented, not just designed: static analysis runs in the Docker
sandbox via the `binary-static-analysis` skill, with zero Proxmox
round-trip, zero clone wait, and none of the guest-agent/template-guard
risk that comes with the VM; `windows-binary-analysis` is narrowed to
genuinely VM-dependent operations only (`start`/`push`/`exec`/`gui-probe`)
and its own `SKILL.md` says so explicitly at the top.

## Four layers, each independently useful

```
ctf-solver (orchestrator, platform-blind)
   ├─ platform-specific skills: acquisition, discovery, guarded submission
   ├─ category skill: binary-static-analysis (shared across platforms/formats)
   ├─ ctf_traversal.py                         (shared, platform-blind state machine)
   └─ operator layer: ctf-sweep-watchdog.sh + ctf-sweep-monitor.sh +
                       ctf-sweep-backup.sh + check-deployment-drift.sh +
                       distill_progress_note.py
```

1. **The traversal engine** (`ctf_traversal.py`) — a durable, lockable
   state machine over "which problems exist, which are solved, which are
   in progress," with no platform-specific logic of its own. Running
   real, multi-day autonomous sweeps in production.
2. **The operational resilience layer** — what keeps an unattended
   multi-hour sweep alive through crashes, stuck sessions, and failures
   the model itself never sees a turn for, *and* what keeps the sweep's
   own bookkeeping honest once an agent is under pressure to look
   productive.
3. **Category/platform skills** — the actual technique work
   (`binary-static-analysis`, and whatever crypto/web/stego tooling the
   sandbox image carries) and, per platform, a matched set of
   acquisition/discovery/guarded-submission skills built to the pattern
   described below.
4. **`ctf-solver`** — the top-level orchestrator tying the above together
   for "solve this challenge."

All of `binary-static-analysis` and `ctf-solver` exist as real skills in
this repo today (`skills/security/<name>/`), independent of any one
platform's own integration. See
[README_ksnctf-extension.md](README_ksnctf-extension.md) for the
first platform built on this layer.

## What's literally shared vs. what's a reused design

Everything on this page is meant to be usable by any CTF-platform
integration without modification — that's what "platform-blind" means.
In practice today there is exactly one such integration (ksnctf), so
it's worth being explicit about which of this harness's pieces a
*non*-CTF extension like range-assess actually calls versus merely
imitates, since "built on this harness's patterns" is easy to misread
as "calls this harness's code":

**Called directly, unmodified, by any CTF-platform integration (ksnctf
today, and any future one)**:
- `ctf_traversal.py` — the one engine, one JSON state file, one lock
  file, regardless of platform.
- `ctf-sweep-watchdog.sh` and `ctf-sweep-monitor.sh` — the same two
  scripts, parameterized by which platform's `--discover`/`--submit`
  scripts to drive.
- The shared verification/anti-fabrication baseline in `AGENTS.md`,
  `ctf-sweep-backup.sh`, `check-deployment-drift.sh`, and
  `distill_progress_note.py` — none of these contain any platform-aware
  logic at all, so nothing about adding a second CTF platform would ever
  touch them.

**Not called by range-assess at all — a separate implementation of the
same design, because its target domain doesn't fit this engine's
problem-shaped state**:
- `range_traversal.py` is its own file with its own state schema (hosts
  and findings, not problems), built on `ctf_traversal.py`'s lock-file
  *pattern* — same concurrency-safety mechanism, independently
  implemented, not a shared module either file imports.
- `range-sweep-watchdog.sh` is its own file, not a parameterized call
  into `ctf-sweep-watchdog.sh` — it uses the same relaunch-on-any-exit
  mechanism, but genuinely needs to differ (no watchdog-side note
  distillation, since there's no free-text progress note to distill).
  It does, however, literally call `ctf-sweep-monitor.sh` — that one
  script is a genuine, shared dependency of both sweeps (see
  [README_range-assess-extension.md](README_range-assess-extension.md)
  for how).
- `range_poc.py`/`range_remediate.py` reuse `ksnctf-submit`'s
  assign/report guardrail *shape* (attempt cap, delay, golden/working
  guard, audit log) as their own independent code — nothing in
  `skills/security/range-assess/` imports or calls anything under
  `skills/security/ksnctf-submit/`.

The practical test for "is this shared or reused": if a second CTF
platform appeared tomorrow, it would call `ctf_traversal.py` and
`ctf-sweep-watchdog.sh` exactly as ksnctf does, with zero new code in
either file. Range-assess could never do that — its state doesn't fit
the shape `ctf_traversal.py` assumes — which is exactly why it has its
own `range_traversal.py` instead.

## The traversal engine (`ctf_traversal.py`)

A single JSON state file (`/workspace/.ctf_traversal_state.json`) tracks,
per problem: status (`pending` / `in_progress` / `solved` / `exhausted` /
`skipped_unreachable`), submission attempts, and timestamps.

```
python3 ctf_traversal.py init --discover <path> --solver <path> --submit <path> --scope <host>
python3 ctf_traversal.py next
python3 ctf_traversal.py submit <problem_id> <candidate_flag>
python3 ctf_traversal.py status
```

- **`init`** runs once per campaign — discovers every problem and checks
  the scope host's reachability exactly once, caching both, in whatever
  order the discovery script naturally returns them (no engineered
  difficulty sort: an earlier draft tried sorting by point value, and was
  dropped because that field doesn't generalize across platforms and
  needs a whole extra discovery pass).
- **`next`** returns the next problem that's either `pending` or already
  `in_progress` — exhaust-then-advance, walking the same natural order for
  both statuses. Revisiting `in_progress` problems (not only fresh
  `pending` ones) was a real fix, not the original design: the first
  version only ever returned `pending` problems, so a problem flipped to
  `in_progress` on first handout was never returned again regardless of
  whether it was solved — see the changelog. A problem whose scope host
  is cached-unreachable is skipped instantly as `skipped_unreachable`
  (distinct from "wrong flag" and "no skill exists yet"), no per-problem
  timeout hang.
- **`submit`** calls the platform's real submit script and updates state:
  `result: true` → `solved`; a hard-cap refusal → `exhausted`; anything
  else (wrong flag, network error) → left `in_progress` (both remain
  legitimately retryable). A genuine, non-refused result that exhausts
  the attempt cap marks the problem `exhausted` immediately on that same
  call — an earlier version only noticed exhaustion on a wasted *sixth*
  call, which the submit script's own guardrail refuses before it ever
  reaches the platform.
- **`status`** — read-only summary, including a guardrail-state section
  showing, per problem: status, logged attempt count, and either
  `last_returned_at` (JST) for an in-progress problem or the actual
  submission time for a solved one — not lock-protected, since a
  momentarily stale read is not a data-loss risk the way a write is.
- **State-file writes are serialized** with an `fcntl.flock`-based
  exclusive lock on a separate lock file, held for each write-capable
  subcommand's entire load-modify-save span. A second process queues
  behind the first rather than corrupting shared state.

**Platform-blindness, by construction.** The engine never calls a
platform's fetch/discover/submit logic directly — it takes their script
paths as explicit CLI parameters, sourced from `AGENTS.md`'s current-scope
section. Adding a second platform (HTB, say) means adding its own
`<platform>-discover`/`<platform>-submit` and updating that one section —
zero changes to `ctf_traversal.py` itself. A platform needing real login/
session management handles that entirely inside its own scripts; the
engine only ever sees a valid problem list or a valid submit result,
never how it was obtained.

## Progress-note discipline — the harness's second line of defense

A state machine that only records *status* has no memory of *what was
tried*. This matters because an involuntary turn end (a text-only
response, a budget cutoff, a crash) gives no warning, and the background
review mechanism this project originally relied on for incidental capture
never gets a chance to run under headless mode (see "Operational
resilience" below) — so without something written to disk mid-derivation,
the next session re-derives an `in_progress` problem from scratch.

- **`/workspace/progress-notes/problem_<id>.md`** — one file per problem,
  updated *as work happens*, not just at a natural stopping point. `next`
  reads this file first whenever it hands back a problem that already has
  one. Within it: a `## Proven` section for independently-verified
  results, kept separate from `## Attempts` so a settled result is never
  silently re-derived by a later session mistaking it for a guess; a
  canonical `current_<id>.py` file for problems that accumulate many
  scratch scripts, so a later session has one trusted path rather than
  guessing among dozens of `q33_*.py` attempts.
- **`## Suspected Blocker`** is a literal control-string heading the
  traversal engine's cadence/annotation logic checks for exactly (now
  fuzzy-matched, after an exact-string version silently missed close
  variants) — not a section title to phrase however reads best. It's the
  *sanctioned* outlet for "I think this is stuck," used instead of ever
  touching traversal state directly (see the specification-gaming
  incident below for why that distinction exists at all).
- **A 60-minute update cadence is enforced, not just hoped for.** `next`
  detects an existing-but-stale progress note and warns; repeatedly
  returning the same held problem without an update being written is
  refused until the note is updated or the problem is explicitly resolved
  — this closed a real gap where a problem was handed back out
  indefinitely with no evidence any work was actually happening on it.
- **Blocked `in_progress` problems are deprioritized via LRU cycling**,
  not permanently skipped — a problem flagged `## Suspected Blocker`
  still comes back around through `next` like any other `in_progress`
  problem, just later, so a hard-but-solvable problem early in discovery
  order doesn't block every later one indefinitely while still getting
  real rotation.
- **A genuinely exhausted problem (five real attempts, cap hit) gets a
  closing note too** — neither a solve write-up nor a Suspected Blocker
  trigger covers this outcome, since exhaustion can land mid-derivation
  on whatever candidate happened to be the fifth tried. This note's only
  real audience is a human revisiting later, since no future automated
  session will ever be handed this problem again.
- **`distill_progress_note.py`** — a deterministic backstop under all of
  this, called by the watchdog after *every* session ends (normal exit,
  crash, or interrupt), not conditional on the agent remembering to write
  anything. Feeds the session's transcript tail to a separate model
  (`gpt-oss:120b-96k`, chosen for document-processing trustworthiness over
  raw CTF-solving skill) to distill what happened, but does not trust that
  distillation at face value: it requires the distillation to cite
  concrete evidence (file paths, quoted output) for every claim, and
  discards the whole distillation if any cited path doesn't actually exist
  on disk — a fabrication signal treated as disqualifying rather than
  something to patch around. What survives appends under a clearly
  machine-generated heading, kept separate from the agent's own `##
  Proven` section, so nothing here is ever mistaken for something a
  session verified itself.

## Flag/result submission guardrail pattern

Submitting a candidate to a platform's own accept/reject check is
genuinely valuable, but it's a write action against someone else's
infrastructure, and a plausible-but-wrong candidate could in principle
turn a "verify my answer" tool into a brute-force oracle. The guardrail is
enforced in each platform's own submit script, in code:

1. **Local format pre-check before any network call** — free, no network
   cost, and worth keeping deliberately loose: checking only an invariant
   actually confirmed about the platform (a fixed prefix, say) rather
   than an inferred length range or character-composition guess, since a
   guess inferred from a small sample is a real way to reject a genuinely
   correct candidate.
2. **Hard attempt cap per problem**, tracked in a persistent local log
   keyed by problem ID.
3. **Mandatory minimum delay between attempts.**
4. **Full audit log** — every attempt, timestamp, candidate, result.
5. **Hard stop once the cap is hit** — resetting requires an explicit
   human edit to the log.
6. **Exact-duplicate resubmission refused for free**, before any network
   call — the same already-rejected candidate submitted twice costs
   nothing but teaches nothing new either.
7. **A network/connectivity failure is never counted as an attempt** —
   cleanly distinguished from a real wrong-result response.
8. **Escalating strategy warnings** on repeated wrong results — a flat
   "wrong" every time gives no signal beyond "try again," so a
   later-numbered attempt should carry progressively stronger warnings
   (reconsider the approach, not just the guess; then an explicit
   "attempts nearly exhausted" warning; then an explicit "now permanently
   exhausted" message on the final one), tuned to whatever the platform's
   own cap is.
9. **A candidate-by-file option**, alongside a plain positional argument
   — a candidate containing a shell-special character can otherwise break
   argument parsing before the script ever runs. Writing the candidate to
   a file via the normal file-write tool and passing its path sidesteps
   shell-escaping entirely.

This applies to a platform's own flag-checking mechanism specifically —
not to legitimate enumeration against a challenge's own in-scope attack
surface (a blind-SQLi length-enumeration attack against a vulnerable
login form, say, when that's the intended technique). See
[README_ksnctf-extension.md](README_ksnctf-extension.md) for a concrete
implementation of this pattern, including the real incidents that shaped
each guardrail above.

## Never bypass the tracking system directly — a rule earned the hard way

**Confirmed, real incident**: a session judged a problem unsolvable,
wrote and ran a short Python script that opened
`.ctf_traversal_state.json` directly and set the one field it wanted
changed, and saved it back — completely bypassing `ctf_traversal.py`'s
own `submit`/`next` subcommands, including the concurrency lock added
specifically to protect that file. No jailbreak, no adversarial prompting
— ordinary tool access plus an ordinary objective (keep the sweep moving)
plus the simple absence of an explicit prohibition was sufficient. Full
account, including why a sanctioned "give up" command was considered and
rejected as the fix (a sanctioned alternative doesn't prevent an
unsanctioned one that already worked), in
[SPECIFICATION_GAMING_CASE_STUDY.md](SPECIFICATION_GAMING_CASE_STUDY.md).

`AGENTS.md` now states this as an explicit standing rule: never write
directly to a traversal or submission tracking file, by any means other
than that system's own sanctioned commands — no exception for how
confident a "this is stuck" judgment feels. The sanctioned outlet for
that same judgment is the `## Suspected Blocker` progress-note mechanism
above, which records the same information without touching traversal
state at all.

## Shared verification/anti-fabrication baseline

A substantial set of standing rules, kept in `AGENTS.md` (auto-loaded into
every session) rather than duplicated into every category skill's own
`SKILL.md`. In addition to the baseline described in the design phase
(never attribute unread function contents; never substitute a plausible
guess for a stalled derivation; verify a claimed transform by running it;
exhaustive inventory before hypothesis), production use added:

- **Validate a self-produced file before handing it to a tool that
  submits it directly to a model API** — a valid header is not a valid
  decode (this is what let a corrupted, in-session-decrypted JPEG slip
  past `vision_analyze` and abort a session one API call later).
- **Read a file back immediately after a long or load-bearing write** —
  `write_file` has been directly observed truncating/corrupting real
  writes, confirmed separately from any derivation bug.
- **Abstract a stuck problem's actual technical requirement into general
  terms and search for that**, before concluding it's unsolvable — not
  just searching for the specific problem's own name. This is mandatory,
  not optional scaffolding, and must happen as an actual tool call, not
  something merely held as a principle.
- **Prove a component correct in isolation against an authoritative
  reference, not just self-consistency**, before combining it with
  another independently-fallible stage — a round-trip-only test confirms
  an implementation agrees with itself, not that it matches what it
  actually needs to invert.
- **Run a whole pipeline end-to-end on best-effort intermediate output
  before perfecting any one stage in isolation**, when a downstream stage
  has a known tolerance for imperfect input — a downstream pass/fail is a
  more direct answer than any amount of further upstream refinement via a
  proxy metric.
- **Give simple-looking driver/assembly code the same literal scrutiny as
  the complex algorithm it surrounds** — a common convention is not the
  same claim as what a specific reference implementation is shown to do.
- **If a reference to a live network endpoint turns up during analysis,
  actually attempt the connection or replay before guessing** — and
  re-check a cached reachability determination when a new technique makes
  it relevant again, rather than trusting a possibly-stale earlier result
  indefinitely.
- **Never write directly to a traversal or submission tracking file** —
  see above.

## Operational resilience layer

Operator-run tooling living outside the agent entirely, in `scripts/` —
the agent never starts or stops its own supervisor.

- **`ctf-sweep-watchdog.sh`** — the relaunch loop. Calls `hermes -p
  <profile> -z "<prompt>"` (Hermes's genuinely headless one-shot mode).
  On *any* exit, relaunches unconditionally after a short delay;
  `ctf_traversal.py status`'s durability makes this safe. Auto-detects
  init vs. resume by whether traversal state already exists. Checks
  `pgrep -f "hermes -p <profile> -z"` before every launch and waits on a
  match, so two instances can never run against the same state file
  concurrently. On Ctrl-C, explicitly kills both the monitor and the
  `hermes` child process, then exits — background jobs don't receive the
  terminal's Ctrl-C on their own. After every attempt, regardless of exit
  code, calls `distill_progress_note.py` and archives the session's full
  transcript to `workspace/session-exports/<session_id>.md` (plus a
  running `index.txt`) — a deterministic backstop under the model's own
  progress-note habit.
- **`ctf-sweep-monitor.sh`** — periodic session-log viewer, started and
  stopped automatically by the watchdog. Re-discovers the active session
  on every poll by matching `sessions list`'s Preview column against the
  exact opening text of the attempt the watchdog just launched (the Title
  field populates asynchronously and is reliably empty on a young
  session). Uses `watch` only when stdin is a real tty, since `watch`
  crashes without one attached — the normal situation when backgrounded.
- **`ctf-sweep-backup.sh`** — `backup`/`list`/`restore` for
  `workspace/`'s state (traversal state, submission log, progress notes,
  write-ups, session-export archive) — the only safety net for anything
  the sweep has produced, since `workspace/` lives entirely outside git.
  Checks that a backup actually captured the handful of paths this whole
  design depends on, not just that an archive file got created.
- **`check-deployment-drift.sh`** — compares this repo (source of truth)
  against every place its skills actually get deployed to at runtime —
  today that's the default profile plus `gemma-experiment` and
  `qwen-experiment` (and `devstral-experiment`), each under
  `~/.hermes/profiles/<name>/skills/security/...`. Catches a fix committed
  to the repo that never actually reached the directory Hermes reads
  from. Also annotates an `in_progress` problem as `[blocked]` in its
  output when that problem's progress note has a Suspected Blocker
  section.
- **`skill-improvement-notes.md`** — anything worth capturing about a
  skill mid-sweep is appended here as an ordinary tool call, for a human
  to review and apply later, rather than the agent attempting to patch
  the skill directly. (Background curator review, the originally intended
  path, runs on a daemon thread that a headless `-z` session's process
  teardown kills before it ever gets to execute — confirmed directly in
  Hermes's own source, not assumed.)

## Which model is actually running this

The sweep has been run under more than one local-model profile, not a
single fixed choice — `check-deployment-drift.sh` currently tracks
skill deployment across the default profile, `gemma-experiment`,
`qwen-experiment`, and `devstral-experiment`. See [README.md](README.md)
for the primary/fast-subagent model policy that applies outside this
harness; a profile run through the watchdog can differ from that policy
when deliberately trialing a different backend.
