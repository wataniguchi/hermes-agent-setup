---
name: ksnctf-submit
description: Submit a candidate flag to ksnctf's real checking API, with a hard guardrail against brute-forcing
version: 1.0.0
metadata:
  hermes:
    tags: [security, ctf, verification]
    category: security
---

## What this is, and what it is not

This submits a candidate flag to ksnctf's actual `/api/submit` endpoint
for authoritative confirmation — the platform's own accept/reject
response is stronger evidence than any amount of self-verification.

**This is not a tool for guessing.** It has a hard, code-enforced limit
of 5 attempts per problem, ever, with a mandatory 60-second gap between
attempts. These are not soft warnings — the script itself refuses to
make the network call once either limit is hit. There is no way to
argue, rephrase, or retry your way past this from within a
conversation; resetting requires a human to directly edit or clear
`/workspace/.ksnctf_submission_log.json`.

**Only submit a flag you have actually derived and verified**, per the
standing rules in `AGENTS.md` — a claimed transform run and its output
checked, a function actually decompiled and read, not a plausible guess.
If you're not confident in a derivation, the right move is to keep
working the actual problem, not to spend one of five limited attempts
hoping it's close enough.

## Important scoping note — this is not the same thing as attack-scope

This guardrail is specific to *this checking mechanism*. It has nothing
to do with, and does not restrict, legitimate enumeration or brute-force
techniques against a challenge's own in-scope attack surface — e.g., a
blind-SQLi length-enumeration attack against a vulnerable login form is
sometimes the *intended* solving technique for a challenge, and remains
fully permitted under the scope rules in `AGENTS.md`. This skill only
governs how many times you're allowed to ask ksnctf's own flag-checker
"is this the answer?" — not what you do to derive the answer in the
first place.

## Usage

```
python3 .../submit_flag.py submit <problem_id> <candidate_flag>
```

`problem_id` is the numeric ID from the problem's own URL
(`ksnctf.sweetduet.info/problem/<N>` → `problem_id` is `N`), not the
problem's title or URL itself.

Output is JSON. A refused attempt (bad flag shape, cap reached, too soon
since the last attempt) has `"submitted": false` and a `reason` — read
it, it tells you exactly why and what to do instead. A real attempt has
`"submitted": true`, the actual `"result"` from ksnctf (`true`/`false`),
and how many attempts remain for that problem.

## Confirmed mechanism (verified directly, not assumed)

Real endpoint, confirmed by fetching ksnctf's own `problem.js` directly
and reading the actual `fetch()` call in it:

```
POST https://ksnctf.sweetduet.info/api/submit
Content-Type: application/json
Body: {"id": <problem_number:int>, "flag": "<candidate:string>"}
Response: {"result": true|false, ...}
```

No login is required — Twitter login elsewhere on the site is confirmed
to gate only ranking/leaderboard participation, not this check.

## What's genuinely untested here

The guardrail logic (format check, cap, delay, audit log) is
straightforward and has been reasoned through carefully, but the actual
live request against ksnctf's real endpoint has not been dry-run tested.
Validate against the known test problem before trusting this for real
challenge work: `problem_id=1`, known correct flag
`FLAG_SRORGLnTh2Q5fTwu` (given directly on the problem page for exactly
this purpose). Confirm the response comes back `"result": true`, and
separately confirm an obviously-wrong candidate for the same problem
comes back `"result": false` — both against a problem with a
known-in-advance answer, so there's no ambiguity about whether the
script itself is working correctly.

Also worth confirming the network-error path actually behaves as
designed, not just reasoned through: temporarily point `SUBMIT_URL` at
an unreachable address (or run this while offline) and confirm the
output shows `"reason": "network_unreachable"` with the attempt log
genuinely untouched afterward — not a crash, and not a silently consumed
attempt.
