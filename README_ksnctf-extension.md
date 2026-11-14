# ksnctf extension — implementation on top of the CTF harness

The first platform wired into
[README_CTF_harness.md](README_CTF_harness.md)'s platform-blind traversal
engine. For the backstory of how both the shared harness and this
platform's own pieces were hardened, see
[CHANGELOG_CTF-harness.md](CHANGELOG_CTF-harness.md) — this file states
only where the ksnctf integration stands now.

## What this solves

On the surface, this extension solves ksnctf: acquire each problem,
solve it with whatever technique it calls for, and submit the flag,
across the platform's entire problem set, unattended. The real objective
one level up is different: prove the general CTF harness —
`ctf_traversal.py`, the watchdog/operator layer, the submission-guardrail
pattern — against a problem set broad enough to matter, while every
attempt still carries a built-in correctness oracle (see
"Why this extension exists" below). ksnctf fits that purpose well
because, despite being one platform, its own problems span a genuinely
broad range of security-problem categories — binary exploitation and
reverse engineering, web application attacks, cryptography, and direct
SSH/network access — without losing the one property that makes CTF the
place to harden a harness first: a definitive, immediate correct/
incorrect answer on every submission. Solving ksnctf end-to-end
therefore exercises the harness against nearly the same breadth of
technique categories that a harder, oracle-free domain like range
assessment will need later, before the harness has to carry that harder
domain without an oracle to catch its own bugs:

```
ctf_traversal.py drives this loop across every ksnctf problem:

   next pending/in-progress problem
                     │
                     ▼
        ┌─────────────────────────┐
        │ 1. Acquire & classify   │   ksnctf-fetch: fetch the problem,
        │                         │   detect its mode (file / web app /
        │                         │   SSH / text)
        └────────────┬────────────┘
                      ▼
        ┌─────────────────────────┐
        │ 2. Solve                │   binary-static-analysis /
        │                         │   windows-binary-analysis / the
        │                         │   agent's own general tools, per mode
        └────────────┬────────────┘
                      ▼
        ┌─────────────────────────┐
        │ 3. Verify, then submit  │   independently-verified flag,
        │                         │   ksnctf-submit's guarded submission
        └────────────┬────────────┘
                      ▼
          solved / exhausted / still in progress
```

## Why this extension exists

The CTF harness is deliberately platform-blind: `ctf_traversal.py` and
the watchdog/operator layer know nothing about any specific site, by
design. That design only pays off once it's actually wired to a real
platform, against a real acquisition flow and a real, enforced
submission endpoint rather than a synthetic one — this extension is that
first wiring. Everything the harness's "platform-specific skills" layer
calls for — acquisition, discovery, and guarded submission — is
implemented here specifically for ksnctf.

ksnctf itself, rather than some other CTF platform, was chosen for this
first wiring because it's publicly available, permanently up (no
competition window to schedule around), and requires no authentication
to acquire problems or check a submitted flag — minimizing everything
about the integration that isn't the harness itself — on top of the
category breadth described above. See
[README_CTF_harness.md](README_CTF_harness.md#why-ctf-first-and-why-ksnctf)
for why CTF platforms generally, and this harness's crawl-walk-run
sequence, come before a domain like range assessment that has no
built-in oracle.

## Architecture: what this extension adds to the harness

Of the CTF harness's four layers, this extension supplies exactly one:
the platform-specific acquisition/discovery/guarded-submission skill set
for ksnctf — `ksnctf-fetch`, `ksnctf-discover`, and `ksnctf-submit`. The
traversal engine and the operator/watchdog layer are entirely the
harness's own, used here unmodified: `ctf-sweep-watchdog.sh` drives
`ctf_traversal.py`'s `init`/`next`/`submit` loop against
`ksnctf-discover`/`ksnctf-submit` as its `--discover`/`--submit`
scripts, with no ksnctf-specific code of its own. `ctf-solver` — also
shared, the harness's top-level orchestrator — is the entry point for
"solve this ksnctf challenge": it calls `ksnctf-fetch` to acquire a
problem, routes by whichever delivery mode(s) it reports, hands binary
targets to the platform-agnostic `binary-static-analysis` skill (or
`windows-binary-analysis` when genuine dynamic execution is required),
and — once a candidate flag has been independently derived and verified
— calls `ksnctf-submit`, whose guardrail is enforced inside that one
script regardless of what calls it. All four skills named here
(`ksnctf-fetch`, `ksnctf-discover`, `ksnctf-submit`, `ctf-solver`) are
implemented and in real use, each under `skills/security/<name>/` with
its own `SKILL.md`.

## `ksnctf-fetch` — acquisition and mode classification

A plain Python script (`requests`-based, matching every other skill
script in this project — a structured, testable script rather than
Hermes's own general browsing behavior, for something that needs to
behave consistently every time):

```
python3 ksnctf_fetch.py --scope <allowed_host> fetch <problem_url>
```

`--scope` is required for any problem that might involve a live-target
mode; the agent sources it from `AGENTS.md`, never hardcoding or
auto-deriving it. Output is structured JSON: title, point value, release
date, which delivery mode(s) matched, downloaded file paths (if any), the
extracted web-app target URL if any (checked against `--scope`, flagged
if it doesn't match), and extracted SSH connection details or raw matched
text if any.

**Classification runs all detectors and reports every match — it never
forces a single guess**, since a problem can plausibly combine modes (a
downloadable file *and* a web component), and picking one incorrectly
would silently hide the other:

| Mode | Heuristic |
|---|---|
| Downloadable file | `<a href="...">` links matching a broad set of confirmed real extensions (`.exe`, `.zip`, `.pcap`, `.docx`, `.apk`, `.cpp`, `.html`, generally anything not an obvious internal navigation link) |
| Embedded web application | `<form>` elements, login fields, or a URL pointing at the confirmed live-target host; may co-occur with a downloadable "source" file for the same problem |
| Direct SSH access | A precise regex against the confirmed consistent template (`ssh <user>@<host> -p <port>` followed by `Password: <pw>`); falls back to surfacing the raw matched region if the regex doesn't match, rather than guessing at a looser pattern |

A fourth category — the answer embedded directly in the problem's own
description text (a cipher, a riddle), with no file/web-target/SSH access
at all — is deliberately left to the agent's own general reasoning rather
than a fixed detector.

**Scope enforcement is a deliberate two-layer design.** `ksnctf-fetch`'s
job is acquisition only — it receives `--scope` as an input and checks
the extracted web-app URL against it, but doesn't derive or guess scope
from page content itself. Actually *enforcing* scope against tool
invocations (`nikto`, `sqlmap`, `dirb`, raw `requests` calls) is the
responsibility of whichever web-application-attack skill runs against a
target — it receives the same explicit scope parameter and refuses to
run any scan against a host that doesn't match it.

## `ksnctf-discover` — building the full problem list

```
python3 ksnctf_discover.py list
```

Outputs a JSON array of `{id, title, url}` in the platform's own natural
order (the homepage's own listing order) — no engineered sort. This feeds
`ctf_traversal.py init` as its `--discover` script.

## `ksnctf-submit` — guarded flag submission

Implements the generic guardrail pattern from
[README_CTF_harness.md](README_CTF_harness.md#flagresult-submission-guardrail-pattern)
against ksnctf's own confirmed submission endpoint:

```
python3 submit_flag.py --scope <allowed_host> <problem_id> <candidate_flag>
python3 submit_flag.py --scope <allowed_host> <problem_id> --candidate-file <path>
```

- **Local shape pre-check**: `^FLAG_.{1,100}$` — deliberately loose, and
  deliberately *not* a guess about length range or character
  composition, after two separate real submissions were wrongly rejected
  by earlier, tighter guesses (a `{10,30}`-length guess rejected a
  genuine 7-character flag; a subsequent alphanumeric-only guess
  rejected a genuine flag containing an underscore). See
  [CHANGELOG_CTF-harness.md](CHANGELOG_CTF-harness.md) for both
  incidents — the only invariant now checked is the one actually
  confirmed never to vary: the `FLAG_` prefix itself.
- **`MAX_ATTEMPTS_PER_PROBLEM = 5`**, **`MIN_SECONDS_BETWEEN_ATTEMPTS =
  60`**, both enforced from a persistent per-problem audit log, not just
  in-memory.
- **Exact-duplicate candidate resubmission is refused before any network
  call** — the same already-rejected string submitted twice costs nothing
  but also teaches nothing new.
- **Escalating strategy warnings**, not a flat "wrong" every time: a
  wrong result on attempt 3 includes a prompt to reconsider the approach
  itself, not just the guess; attempt 4 adds an explicit "one attempt
  left, then this problem is permanently locked" warning; the 5th and
  final attempt's result carries an explicit "now permanently exhausted"
  message (added after review found the original two-tier version left
  the single most consequential attempt — the last one — with no warning
  at all).
- **`--candidate-file`**, as an alternative to the positional
  `candidate` argument (mutually exclusive with it): write the candidate
  to a file first via the normal file-write tool, then pass its path.
  Added after a genuinely correct flag containing `)` broke bash's
  `eval`-based argument parsing before the script itself ever ran — this
  sidesteps shell-escaping entirely for any candidate containing
  shell-special characters.
- **A network/connectivity failure is never counted as an attempt**,
  cleanly distinguished from a genuine wrong-flag result from the
  platform.
- **Hard stop once the cap is hit** — resettable only by an explicit
  human edit to the audit log, not by any in-band command.

This guardrail targets *submission to ksnctf's own checker* specifically;
it does not apply to legitimate enumeration against a challenge's own
in-scope attack surface (e.g. a blind-SQLi length-enumeration attack
against a vulnerable login form, when that's the intended solving
technique for that challenge) — that's covered by the site's own
"attack `ctfq.u1tramarine.blue`" allowance, not this guardrail.

## `ctf-solver` — routing a fetched ksnctf problem

1. **Acquire** — calls `ksnctf-fetch` with the scope read from
   `AGENTS.md`.
2. **Route by detected mode(s)**:
   - **Downloadable file**: for a recognized binary format (PE/ELF/
     Mach-O, native or .NET), `binary-static-analysis` handles it
     entirely in the Docker sandbox — Ghidra inventory/decompile or the
     .NET decompile path — with no Proxmox round-trip. Dynamic execution,
     when actually needed, goes through `windows-binary-analysis`'s
     VM-dependent operations instead. For a non-binary file type
     (`.pcap`, `.docx`, `.apk`, `.zip`, etc.), the relevant raw tool is
     available in the sandbox, but using it is agent-driven work using
     general tool access, not a dedicated skill.
   - **Embedded web app / direct SSH access**: reports the confirmed
     target/credentials, flagged as `postponed` while `ctfq.u1tramarine.blue`
     remains unreachable from the sandbox — distinct from "no skill
     exists," since even a fully built skill couldn't act against an
     unreachable host.
3. **Verify, then submit** — a flag is never reported without independent
   derivation/execution, per `AGENTS.md`'s standing rules. There is no
   separate "never auto-submit" layer on top of `ksnctf-submit`'s own
   guardrail: the guardrail already gives a human, this script, and a
   fully autonomous multi-problem engine the same protection
   automatically, so a second restriction would be redundant.

**Given current scope** (live-target host unreachable), priority is the
`downloadable_file` path — the one mode with genuinely complete tooling
and the one requiring no access to the affected host at all. The other
two modes are postponed pending connectivity, not abandoned.

## Additional items

### Confirmed facts about ksnctf itself

- **Scope**: `ctfq.u1tramarine.blue` is the only host this project is
  permitted to attack — confirmed directly from the site's own stated
  policy and independently verified as the actual host behind real SSH
  and web-app problem examples. The main site used for acquisition,
  discovery, and submission, `ksnctf.sweetduet.info`, is a separate host
  and not itself an attack target. This value lives in `AGENTS.md`'s
  current-scope section, never hardcoded into a script.
- **Full problem list is statically discoverable**: the homepage's
  `<a class="dropdown-item" href="/problem/N">N: Title</a>` links cover
  all problems with no JavaScript/API layer needed — confirmed via real
  HTML inspection, not assumed.
- **Submission endpoint and body, confirmed by direct inspection of the
  site's own `problem.js`** (not inferred from the page's visible
  "Correct"/"Wrong" UI): `POST https://ksnctf.sweetduet.info/api/submit`,
  JSON body `{"id": <problem_id:int>, "flag": "<candidate:str>"}`,
  response `{"result": true|false}`. No session/auth token is required to
  check a flag.
- **No OAuth/Twitter login is required** to check a flag — Twitter login
  gates only ranking/leaderboard participation, a cosmetic feature, not
  the answer-checking mechanism.
- **Known confirmed test case**: problem id `1`, flag
  `FLAG_SRORGLnTh2Q5fTwu` — a real, previously-solved problem used to
  validate the submission path itself (endpoint, body shape, response
  parsing) without touching the attempt budget of any problem still
  genuinely in progress.
- **Live-target reachability, as of the last check**: `ksnctf.sweetduet.info`
  (acquisition/discovery/submission) is reachable from the sandbox.
  `ctfq.u1tramarine.blue` (the live-target host behind `embedded_web_app`
  and `direct_ssh_access` modes) was not — worth periodically re-checking
  rather than treated as permanent, since nothing about the host suggests
  it's deliberately unreachable long-term.

### What's confirmed working vs. what's still genuinely untested

**Confirmed working, in production use**: the shared harness pieces this
extension sits on (`ctf_traversal.py`'s state machine, the watchdog/
monitor/backup/distillation layer) have run real, multi-day autonomous
ksnctf sweeps — submitting, advancing, and surviving crashes throughout.
`ksnctf-submit`'s full guardrail (shape check, attempt cap, delay,
duplicate refusal, escalating warnings, audit log) is live and has been
exercised against real problems, including the two real flag-shape
false-rejections that shaped its current, deliberately loose regex. The
submission endpoint and body shape are confirmed directly from the
site's own client code, not inferred from UI behavior.

**Still genuinely untested, or a known limitation, right now**:

- **A "repeated-prefix" guardrail** is referenced by name in one commit
  message from this project's own history, but no corresponding check
  distinct from the exact-duplicate-candidate refusal above was found on
  inspection of `submit_flag.py`. Treat this as either folded into that
  duplicate check, or not actually present — not as a confirmed separate
  mechanism.
- **Reachability of `ctfq.u1tramarine.blue`** should be rechecked
  periodically rather than assumed permanently blocked — the
  `embedded_web_app`/`direct_ssh_access` paths are postponed, not
  abandoned, and nothing here fails fast the moment it becomes reachable
  again without an explicit recheck.
- **Non-binary downloadable file types** (`.pcap`, `.docx`, `.apk`,
  `.zip`, etc.) have the raw tooling available in the sandbox but no
  dedicated skill automating the workflow — this is genuinely manual,
  agent-driven work today, not a gap in an otherwise-automated path.
