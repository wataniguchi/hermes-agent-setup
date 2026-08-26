"""
submit_flag.py — submit a candidate flag to ksnctf's real flag-checking
API, with a hard, code-enforced guardrail against using this as a
brute-force oracle.

The submission mechanism was confirmed by inspecting ksnctf's actual
problem.js directly (see CTF_GENERALIZATION_DESIGN.md for the full
verification trail) — not guessed. Real endpoint:

    POST https://ksnctf.sweetduet.info/api/submit
    Content-Type: application/json
    Body: {"id": <problem_number:int>, "flag": "<candidate:string>"}
    Response: {"result": true|false, ...}

No login/auth is required — Twitter login (seen elsewhere on the site)
is confirmed to gate only ranking participation, not this check.

This targets ksnctf specifically. Unlike scope (a configurable value
read from AGENTS.md), the actual submission mechanism is inherently
platform-specific — a different platform would need its own equivalent
script. The guardrail logic below (format check, attempt cap, minimum
delay, persistent audit log) is written to be easy to copy into a future
platform's own submit script, even though the request-construction
itself isn't.

Usage:
    python3 submit_flag.py submit <problem_id> <candidate_flag>

Hard limits, enforced in code, not just documented in this docstring:
- The candidate must match the confirmed real flag shape before any
  network call happens at all.
- At most MAX_ATTEMPTS_PER_PROBLEM submissions per problem_id, ever,
  tracked in a persistent local audit log that survives across sessions
  (it lives in /workspace, which is host-persisted).
- At least MIN_SECONDS_BETWEEN_ATTEMPTS between successive attempts for
  the same problem_id.
- Once the cap is reached, ALL further attempts for that problem are
  refused. Resetting requires a human to edit or clear the log file
  directly — there is no code path that lets an agent talk its way past
  this by rephrasing, retrying, or any other means.
- A network/connectivity failure (unreachable host, timeout, malformed
  response) is NEVER counted as an attempt — it's cleanly distinguished
  from a real wrong-flag result and reported with its own distinct
  status, rather than either crashing or silently consuming one of the
  5 guarded attempts.
"""
import sys
import os
import re
import json
import time
import requests

SUBMIT_URL = "https://ksnctf.sweetduet.info/api/submit"
LOG_PATH = "/workspace/.ksnctf_submission_log.json"

# CONFIRMED BUG, found via real use: the original {10,30} bound was
# built from an outside solver's rough blog-post note ("about 21
# characters") treated as a hard floor. It silently rejected a real,
# confirmed-correct flag (FLAG_SHEBANG, 7 characters) — and the agent,
# trusting the tool's rejection over its own correct reasoning,
# concluded its own derivation was wrong and spent several real,
# guarded attempts guessing variations instead. Loosened to just
# confirm the FLAG_ prefix and alphanumeric body — this check exists to
# catch obviously malformed candidates (empty, wrong prefix, garbage
# characters), not to assume a specific length range that real evidence
# has now directly contradicted.
# SECOND CONFIRMED BUG in this same check, found via real use: the
# {1,40} alphanumeric-only fix above still assumed flag composition
# beyond the FLAG_ prefix — and a real, correct flag (FLAG_Q20_5926535897)
# contains an underscore, which [A-Za-z0-9] doesn't allow, and was
# silently rejected the same way FLAG_SHEBANG was before it. Two
# separate real failures from inferring a strict rule off a small
# sample is enough evidence that composition genuinely isn't
# predictable. Dropped the character-class restriction entirely — the
# only thing confirmed true without exception across every real example
# so far is the FLAG_ prefix itself. A generous length cap remains
# purely as a backstop against genuinely malformed input (empty, or
# absurdly long), not as a claim about what a real flag looks like.
FLAG_SHAPE = re.compile(r"^FLAG_.{1,100}$")

MAX_ATTEMPTS_PER_PROBLEM = 5
MIN_SECONDS_BETWEEN_ATTEMPTS = 60


def load_log() -> dict:
    if not os.path.isfile(LOG_PATH):
        return {}
    with open(LOG_PATH) as f:
        return json.load(f)


def save_log(log: dict):
    with open(LOG_PATH, "w") as f:
        json.dump(log, f, indent=2)


def check_guardrail(problem_id: str, log: dict):
    """Returns an error message (string) if submission should be
    refused, or None if it's allowed to proceed."""
    attempts = log.get(problem_id, [])

    if len(attempts) >= MAX_ATTEMPTS_PER_PROBLEM:
        return (
            f"REFUSED: {len(attempts)} attempts already logged for "
            f"problem {problem_id} — hard cap of "
            f"{MAX_ATTEMPTS_PER_PROBLEM} reached. This is not a soft "
            f"warning: resetting requires a human to edit or clear "
            f"{LOG_PATH} directly. If your derivation is likely correct, "
            "the right move is to re-verify your method against the "
            "actual challenge, not submit another guess."
        )

    if attempts:
        elapsed = time.time() - attempts[-1]["timestamp"]
        if elapsed < MIN_SECONDS_BETWEEN_ATTEMPTS:
            wait = MIN_SECONDS_BETWEEN_ATTEMPTS - elapsed
            return (
                f"REFUSED: only {elapsed:.0f}s since the last attempt for "
                f"problem {problem_id} — minimum "
                f"{MIN_SECONDS_BETWEEN_ATTEMPTS}s between attempts is "
                f"enforced. Wait {wait:.0f}s more before retrying."
            )

    return None


def cmd_submit(problem_id: str, candidate: str):
    if not FLAG_SHAPE.match(candidate):
        print(json.dumps({
            "submitted": False,
            "reason": (
                f"Candidate {candidate!r} does not match the confirmed "
                "real flag shape (must start with FLAG_) "
                "— refused before any network call was made. This "
                "usually means the derivation produced something "
                "malformed, not that the shape check itself is wrong."
            ),
        }, indent=2))
        sys.exit(1)

    try:
        int(problem_id)
    except ValueError:
        print(json.dumps({
            "submitted": False,
            "reason": f"problem_id {problem_id!r} is not a valid integer "
                      "— ksnctf's API expects the numeric problem ID, "
                      "the same number used in its own /problem/<N> URLs.",
        }, indent=2))
        sys.exit(1)

    log = load_log()
    refusal = check_guardrail(problem_id, log)
    if refusal:
        print(json.dumps({"submitted": False, "reason": refusal}, indent=2))
        sys.exit(1)

    # CONFIRMED design requirement: a network/reachability failure must
    # never be counted the same as a wrong-flag result — it isn't
    # evidence about the candidate at all, just a fact about connectivity
    # right now. Before this fix, an unhandled exception here happened to
    # avoid touching the attempt log (the log-append line simply never
    # ran) — correct by accident, not by design, and it surfaced as an
    # ugly crash rather than a clean, distinct status. Fixed properly.
    try:
        resp = requests.post(
            SUBMIT_URL,
            json={"id": int(problem_id), "flag": candidate},
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as e:
        print(json.dumps({
            "submitted": False,
            "reason": "network_unreachable",
            "detail": str(e),
            "note": (
                "NOT counted as an attempt — the guardrail's attempt log "
                "is untouched. A network failure is not evidence about "
                "whether the candidate flag is correct; retry once "
                "connectivity is confirmed restored. This does not "
                "consume any of the 5 allowed attempts for this problem."
            ),
        }, indent=2))
        sys.exit(1)
    except (ValueError, json.JSONDecodeError) as e:
        print(json.dumps({
            "submitted": False,
            "reason": "invalid_response",
            "detail": str(e),
            "note": (
                "The server responded, but not with valid JSON — also "
                "NOT counted as an attempt. This could mean the endpoint "
                "or its response format has changed; worth investigating "
                "directly rather than assuming it's a transient issue."
            ),
        }, indent=2))
        sys.exit(1)

    log.setdefault(problem_id, []).append({
        "timestamp": time.time(),
        "candidate": candidate,
        "result": data.get("result"),
    })
    save_log(log)

    attempts_used = len(log[problem_id])
    print(json.dumps({
        "submitted": True,
        "result": data.get("result"),
        "attempts_used": attempts_used,
        "attempts_remaining": MAX_ATTEMPTS_PER_PROBLEM - attempts_used,
    }, indent=2))


def main():
    if len(sys.argv) != 4 or sys.argv[1] != "submit":
        print(__doc__)
        sys.exit(1)
    cmd_submit(sys.argv[2], sys.argv[3])


if __name__ == "__main__":
    main()
