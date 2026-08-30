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
claim that every kind of ksnctf challenge is fully automated end-to-end
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

`next` returns the next `pending` problem, having already fetched and
classified it via `--solver` (`ctf_solver.py`). If that classification
needs the scope host and the cached check found it unreachable, the
problem is marked `skipped_unreachable` immediately — no network call,
no waiting, no rediscovering the same fact slowly for every affected
problem.

`submit` calls the platform's real submit script and updates state:
`result: true` → marked `solved`; a hard-cap refusal → marked
`exhausted`; anything else (wrong flag, network error) → left
`in_progress`, since both remain legitimately retryable.

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

This script has been written but not yet dry-run tested against a real
challenge end-to-end. Validate against all three known ksnctf examples
before trusting it:
- `https://ksnctf.sweetduet.info/problem/11` (Riddle) — should report
  `downloadable_file`, detect the PE correctly, and recommend
  `ghidra-inventory`/`ghidra-decompile`.
- `https://ksnctf.sweetduet.info/problem/35` (Simple Auth II) — should
  report `embedded_web_app` with the correct in-scope target.
- `https://ksnctf.sweetduet.info/problem/13` (Proverb) — should report
  `direct_ssh_access` with correct connection details.

If any of these don't match what `ksnctf-fetch` and
`binary-static-analysis` already independently produce on their own
(both already validated separately), the bug is almost certainly in this
script's own orchestration logic, not in the underlying tools it calls.

`ctf_traversal.py` is entirely new and entirely untested end-to-end.
Validate in stages, not all at once:
1. `init` — confirm it reports 41 (or however many currently exist)
   total problems and a sensible `scope_reachable` value.
2. `next` — call it a few times in a row and confirm it walks through
   problems in the discovery's natural order, correctly marks
   `embedded_web_app`/`direct_ssh_access`-only problems as
   `skipped_unreachable` given the confirmed-unreachable scope host, and
   returns a real, workable `downloadable_file` problem (e.g. Riddle)
   with its fetch result attached.
3. `submit` — against the same known test problem used to validate
   `ksnctf-submit` directly (`problem_id=1`,
   `FLAG_SRORGLnTh2Q5fTwu`), confirm it correctly marks the problem
   `solved` in state afterward — check via `status`, not just the
   immediate output.
4. `status` — confirm the summary counts actually match what steps 2-3
   just did.
