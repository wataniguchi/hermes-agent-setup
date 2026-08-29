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

**Every turn while a traversal is active must end with a tool call, never
a prose-only status update.** This has happened for real: a session gave
a well-formed progress summary ("4 solved, 3 in progress, continuing the
loop") as plain text with no attached tool call, and the session ended
there — silently, despite a standing instruction to keep going. The
harness ends a turn whenever it returns with no tool call attached;
narration describing intent to continue does not itself cause another
turn to happen. If you want to report interim progress, do so, but that
text must be followed in the *same* turn by a call to `next`, `submit`,
or at minimum `status` — never let a turn end on narration alone while
`status` still shows any `pending` or `in_progress` problems. In practice
this should never be a hard constraint to satisfy: the traversal loop
always has a next mechanical action available (`next` for the next
problem, `submit` for a candidate flag, `status` as a fallback check-in),
so there is no legitimate reason for a turn to end without one while work
remains.

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
