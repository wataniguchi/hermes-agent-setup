---
name: challenge-fetch
description: Fetch a CTF challenge page, classify its delivery mode, and stage files/targets for analysis
version: 1.0.0
metadata:
  hermes:
    tags: [security, ctf, web, reconnaissance]
    category: security
---

## Before using this: check the current scope

**Read `AGENTS.md`'s "Current CTF challenge scope" section first.** It
states the active platform and the exact host you're allowed to attack
for live-target (web-app or SSH) challenges. Pass that host explicitly
via `--scope` — never guess it, never derive it from the challenge page
yourself. This section is meant to be edited directly when the active
platform changes; the script never hardcodes or auto-parses it.

## Usage

```
python3 .../challenge_fetch.py fetch <problem_url> --scope <allowed_host> [--output-dir <dir>]
```

Fetches the page and reports, as structured JSON:
- **Metadata** — title, point value, release date (free, reliably
  available on every real problem page checked so far).
- **`downloadable_file`** — any file(s) linked from the page, downloaded
  automatically into `--output-dir` (default `/workspace/samples`).
- **`embedded_web_app`** — a live-target URL matching `--scope`, if
  found. If `--scope` isn't given, this detector is skipped entirely
  rather than guessing at a target.
- **`direct_ssh_access`** — connection details (user/host/port/password),
  extracted via a regex matched against the confirmed real ksnctf format.

**Modes are not mutually exclusive** — a challenge can be a downloadable
file *and* have a live web-app component at the same time (confirmed:
this happens in practice, e.g. a vulnerable app with its source code also
linked). Don't assume finding one means the others aren't present; act on
everything the output actually reports.

## What's genuinely untested here

This entire skill was built by grounding the design against real
fetched ksnctf pages (via search results showing rendered page content),
but the actual HTML-parsing logic has not been dry-run against a live
page's raw markup. Before trusting this for real challenge work:

1. Run `fetch` against a known problem with each delivery mode (a
   downloadable-file one like Riddle, a web-app one like Simple Auth II,
   an SSH one like Proverb) and confirm the JSON output matches what's
   actually on the page.
2. Confirm `beautifulsoup4` is actually installed in the sandbox image —
   the script has a cruder regex fallback if it isn't, but that fallback
   is explicitly less reliable and flagged as such in its own output.
3. If any detector misses something real or reports a false positive,
   fix the specific regex/heuristic rather than assuming the whole
   approach needs to be rebuilt — the underlying page structure is
   confirmed reasonably consistent across the problem set.

## The scope rule is a hard constraint, not a suggestion

If `embedded_web_app` reports `"in_scope": true`, that's confirmation the
target matches what `AGENTS.md` currently designates — safe to proceed.
This skill does not currently *enforce* scope beyond that check-and-report
step; the actual enforcement against attack-tool invocations (`nikto`,
`sqlmap`, etc.) is the responsibility of whatever web-application-attack
skill gets built next, which should receive the same `--scope` value
explicitly. Never run an attack tool against a host this skill hasn't
confirmed is in scope.
