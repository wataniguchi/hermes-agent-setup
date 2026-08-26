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
"""
import sys
import os
import re
import json
import time
import requests

SUBMIT_URL = "https://ksnctf.sweetduet.info/api/submit"
LOG_PATH = "/workspace/.ksnctf_submission_log.json"

# Confirmed real flag shape: FLAG_ prefix + alphanumeric, ~21 characters
# total (independently confirmed by outside solvers' own write-ups, not
# just this project's own two examples). Deliberately a bit loose on
# exact length given only two confirmed examples exist — this is a
# sanity filter to catch obviously-malformed candidates for free, not a
# strict validator.
FLAG_SHAPE = re.compile(r"^FLAG_[A-Za-z0-9]{10,30}$")

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
                "real flag shape (FLAG_ + 10-30 alphanumeric characters) "
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

    resp = requests.post(
        SUBMIT_URL,
        json={"id": int(problem_id), "flag": candidate},
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

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
