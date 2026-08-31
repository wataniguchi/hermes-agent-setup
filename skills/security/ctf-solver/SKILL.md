---
name: ctf-solver
description: Top-level orchestrator — acquire a challenge, route by delivery mode, hand off honestly to what actually exists
version: 1.0.0
metadata:
  hermes:
    tags: [security, ctf, orchestration]
    category: security
---

## What this actually automates, and what it honestly doesn't

This is the entry point for "solve this challenge," but it isn't a
claim that every kind of CTF challenge is fully automated end-to-end
— it isn't. Be clear with yourself about which case you're in:

- **Downloadable binary (PE/ELF/Mach-O, native or .NET)**: genuinely
  complete, automated pipeline — acquisition, format detection, and a
  direct recommendation for `binary-static-analysis`'s
  `ghidra-inventory`/`ghidra-decompile`/`dotnet-decompile`. This is the
  primary path right now — see the reachability note below for why.
- **Downloadable non-binary file** (`.pcap`, `.docx`, `.apk`, `.zip`,
  images): acquired automatically, but no dedicated analysis skill
  exists yet. The output gives a hint toward the relevant raw tool
  already in the sandbox (`zsteg`, `scapy`, etc.) — using it is manual,
  agent-driven work from there.
- **Embedded web app / direct SSH access — currently postponed, not
  just unautomated.** `ctfq.u1tramarine.blue` — the live-target host
  behind both of these modes — is confirmed unreachable from the
  sandbox's network right now. This is a separate host from
  `ksnctf.sweetduet.info` (confirmed reachable — `ksnctf-fetch` and
  `ksnctf-submit` both work against it), so this only affects these two
  modes. Worth periodically re-checking whether this resolves; it may be
  a temporary or environment-specific restriction rather than a
  permanent fact about the platform.

## Usage — single problem

```
python3 .../ctf_solver.py solve <problem_url> --scope <allowed_host> [--fetch <path>]
```

`--scope` must come from `AGENTS.md`'s "Current CTF challenge scope"
section — read it yourself and pass it explicitly, same as every other
skill in this project. `--fetch` defaults to `ksnctf-fetch`; override it
for a different platform's own fetch script. This script does not read
`AGENTS.md` itself.

## Usage — multi-problem traversal (`ctf_traversal.py`)

**Honest framing, worth internalizing before using this**: this does
not and cannot solve problems itself. Deriving a flag requires genuine
reasoning — the agent's job. This is orchestration and bookkeeping only:
discovering the full problem set, handing them over one at a time in
order, skipping unworkable ones instantly using a cached reachability
check, and tracking solved/skipped/exhausted state across calls — so the
agent's actual effort goes toward the puzzle, not toward remembering
what's already been tried.

```
python3 .../ctf_traversal.py init --discover <path> --solver <path> --submit <path> --scope <host>
python3 .../ctf_traversal.py next
python3 .../ctf_traversal.py submit <problem_id> <candidate_flag>
python3 .../ctf_traversal.py status
```

`init` runs once per traversal campaign (not once per call) — it
discovers every problem and checks the scope host's reachability exactly
once, caching both. State persists in
`/workspace/.ctf_traversal_state.json` across sessions, so `next` and
`submit` can be called repeatedly over however many separate agent turns
a full traversal actually takes.

`next` returns the next problem that's either `pending` or already
`in_progress`, walking the discovery's natural order for both —
having already fetched and classified it via `--solver`
(`ctf_solver.py`). If that classification needs the scope host and the
cached check found it unreachable, the problem is marked
`skipped_unreachable` immediately — no network call, no waiting, no
rediscovering the same fact slowly for every affected problem.

**Revisiting `in_progress` problems, not just handing out fresh
`pending` ones, is deliberate and was fixed from a real gap.** An
earlier version only ever returned `pending` problems — the instant a
problem was first handed out it flipped to `in_progress` and, since
that status is neither `pending` nor terminal, it was never returned
by `next` again, for any reason, regardless of whether it was ever
actually solved. Confirmed directly: a traversal can reach `"done":
true` with real problems sitting permanently `in_progress`, abandoned
the moment a later problem got attempted — and everything this file
documents for recovering unfinished work (progress notes, the
session-export archive) was being written for problems `next` would
structurally never route back to. Walking the same natural order for
both statuses means an `in_progress` problem is picked up again before
any later `pending` one, giving that recovery machinery an actual
chance to be used.

`submit` calls the platform's real submit script and updates state:
`result: true` → marked `solved`; a hard-cap refusal → marked
`exhausted`; anything else (wrong flag, network error) → left
`in_progress`, since both remain legitimately retryable.

**`in_progress` in `ctf_traversal.py`'s state means only that — it
carries no memory of what was actually tried.** A oneshot invocation's
own reasoning is gone the moment its turn ends, including an
involuntary end (a text-only response with no tool call, a budget
cutoff, a crash) that gives no advance warning at all — confirmed
directly that the per-session background review this project relied on
for incidental capture never gets the chance to run under headless
mode (see `CTF_GENERALIZATION_DESIGN.md`'s "Operational resilience"
section for why). Without something written to disk, the next session
picking up an `in_progress` problem re-derives it completely from
scratch, discarding however much real progress the previous one made.

The fix: `/workspace/progress-notes/problem_<id>.md`, one file per
problem, kept updated *as work happens* — what's been discovered or
ruled out, the exact state of any partial derivation, relevant file
paths, and the concrete next step to try. Written the same way as the
write-up below (an ordinary tool call, not a narrated summary), but
on a different cadence: little and often during genuine progress,
not just once at a natural stopping point, since the whole reason it
exists is to survive stops that arrive with no warning. When `next`
returns a problem that already has one of these files, read it before
doing anything else.

**On a `result: true`, write a solve write-up before moving on.** One
tool call to `/workspace/writeups/problem_<id>.md`, covering: the
problem title, the core technique/category, the actual derivation
steps that worked, the key tools/commands involved, and — if
relevant — what any wrong attempts revealed about the right approach.
One file per problem, not a shared running log, to avoid write
ordering/contention across a long traversal and to keep each write-up
independently referenceable later. This is a tool call like any other
step in the loop, not a narrated summary — write the file and continue
immediately into the next `next` call; do not describe its contents in
chat, per the no-narration rule below.

**Skill improvement notes — capture, don't patch.** Background curator
writes require the target skill to be curator-managed (`hermes curator
adopt <name>`) — but even once adopted, confirmed directly that the
write itself happens on a background daemon thread that a headless
`-z`/oneshot invocation never waits for before exiting: the process
tears down before the thread gets a chance to run, so it never
reliably lands regardless of ownership. If you notice something a
skill's `SKILL.md` should say differently — a gap, a wrong assumption,
a missed edge case — append a note to
`/workspace/skill-improvement-notes.md` in one ordinary tool call
instead of attempting to patch the skill directly: which skill, what
was noticed, and the suggested change. This is opportunistic, not a
required per-problem step — don't pause the loop looking for something
to note; if it comes up naturally, capture it in the same turn and
continue immediately, same discipline as the write-up step above.

**Never produce a prose-only turn while a traversal is active — not even
with the intention of following it with a tool call.** This has happened
for real, twice, in two different ways: once as plain narration with no
tool call at all ("4 solved, 3 in progress, continuing the loop"), and
once *after* an earlier version of this rule permitted narration
followed by a tool call in the same turn — the permission to narrate at
all was itself the opening through which the trailing tool call got
dropped. The harness ends a turn whenever it returns with no tool call
attached; describing an intent to continue does not itself cause another
turn to happen, and relying on the model to remember to append a call
after narrating has already failed once in practice. The safe version of
this rule has no exception: do not write "here's my progress so far" or
any similar interim status update mid-traversal, full stop. Call `next`,
`submit`, or `status` directly instead, and let that call's own output
carry whatever state needs conveying. In practice this should never be a
hard constraint to satisfy: the traversal loop always has a next
mechanical action available (`next` for the next problem, `submit` for a
candidate flag, `status` as a fallback check-in), so there is no
legitimate reason to narrate instead of calling one of them while work
remains.

**Note on hard API-level aborts.** The tool-call discipline above
covers turns where the model itself chooses (or fails to choose) a
next action — it cannot cover a hard crash between a tool's execution
and the next API call, since no turn ever happens for the model to
apply the rule to. This has occurred for real: `vision_analyze`
completed successfully, but the image it returned failed to decode on
the provider's *next* call, which some Hermes versions treat as a
non-retryable error and abort the session on — with no narration, no
missed tool call, nothing the model could have done differently in
that turn. Two things reduce this, neither of which is this skill's
job to enforce directly: (1) the file-validation standing rule in
AGENTS.md, which catches a bad image before `vision_analyze` is ever
called on it; (2) the underlying Hermes harness recognizing this error
class and recovering (stripping the bad content and retrying) instead
of aborting — track whether your Hermes version does this before
assuming a sweep will survive a corrupted-image problem unattended.
If a sweep is found to have aborted this way regardless, resuming with
`ctf_traversal.py status` picks up exactly where it left off — no
traversal state is lost, only the in-flight turn.

**No artificial restriction on autonomous submission.** The guardrail
(attempt cap, mandatory delay) lives entirely inside the submit script
itself and applies identically regardless of what calls it — a human, a
single-problem session, or this traversal engine running many problems
in sequence. An earlier draft of this design added a second, redundant
restriction reasoning from a concern that doesn't actually apply to
genuine derivation work; it was withdrawn once that was pointed out.

## On flag submission

`ksnctf-submit`'s guardrail (hard attempt cap, mandatory delay between
attempts) is fully enforced inside that skill itself, regardless of what
calls it — there's no need for a second, separate restriction here. Once
you have a derived, self-verified flag candidate, submit it via
`ksnctf-submit` (directly, or through `ctf_traversal.py submit` if
running a full traversal).

## What's genuinely untested here

This script routes based on what the platform's own `--fetch` script
reports — validating its routing logic means validating it against a
few real, known examples from whatever platform is currently active.
Today that's ksnctf: see `ksnctf-fetch/SKILL.md`'s own "What's
genuinely untested here" for the specific known problems (one of each
delivery mode) to check this against, and `ksnctf-discover/SKILL.md`
for the discovery-side spot-checks. Not repeated here, to avoid the
same facts drifting out of sync across files — when a future platform
is added, its own `<platform>-fetch`/`<platform>-discover` skills are
where its concrete validation examples belong, not this file.

`ctf_traversal.py` is entirely new and entirely untested end-to-end.
Validate in stages, not all at once:
1. `init` — confirm it reports the expected total problem count for
   whatever platform's `--discover` script you're using, and a
   sensible `scope_reachable` value.
2. `next` — call it a few times in a row and confirm it walks through
   problems in the discovery's natural order, correctly marks
   scope-dependent problems as `skipped_unreachable` given the
   confirmed-unreachable scope host, and returns a real, workable
   downloadable-file problem with its fetch result attached.
3. `submit` — against a known-answer test problem (see the active
   platform's own `<platform>-submit` skill for one — e.g.
   `ksnctf-submit/SKILL.md` documents ksnctf's), confirm it correctly
   marks the problem `solved` in state afterward — check via `status`,
   not just the immediate output.
4. `status` — confirm the summary counts actually match what steps 2-3
   just did.
