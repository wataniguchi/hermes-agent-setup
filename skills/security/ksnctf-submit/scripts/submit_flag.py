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
    python3 submit_flag.py submit <problem_id> --candidate-file <path>

CONFIRMED real failure that motivated --candidate-file: a genuinely
correct flag (FLAG_This_is_right_:)) was submitted as a raw shell
argument via the agent's own terminal tool, and the unescaped ')' broke
bash's `eval` parsing before this script ever ran at all — a real
answer lost purely to shell syntax, unrelated to whether the derivation
was right. --candidate-file sidesteps this entirely: write the exact
candidate to a file via the write_file tool (which never touches a
shell), then pass the file's path — a plain filename is virtually
always shell-safe regardless of what characters the candidate itself
contains.

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
- Resubmitting the exact same candidate already confirmed wrong for a
  problem is refused unconditionally, before any network call — this
  has happened for real (the same wrong guess submitted twice, wasting
  a guarded attempt for zero new information) and there is no
  circumstance where it's the right move.
- A real wrong result at attempt 3 or 4 comes back with an escalating
  strategy_warning field urging reconsideration of the whole approach,
  not just another guess — a flat "wrong" result was confirmed, via
  actual use, to give no signal beyond "try again," which directly
  contributed to real guessing spirals (one problem burned all 5
  attempts on unrelated words before this existed).
"""
import sys
import os
import re
import json
import time
import argparse
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


def check_guardrail(problem_id: str, candidate: str, log: dict):
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

    # CONFIRMED real failure, found via actual use: the exact same
    # already-rejected candidate was resubmitted for no new reason,
    # burning a guarded attempt for zero new information. No
    # circumstance justifies this — refuse unconditionally and for
    # free, before any network call, rather than rely on the caller
    # remembering to check its own history first.
    for prior in attempts:
        if prior["candidate"] == candidate and prior["result"] is False:
            return (
                f"REFUSED: {candidate!r} was already submitted for "
                f"problem {problem_id} at "
                f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(prior['timestamp']))} "
                "and confirmed WRONG. Resubmitting the exact same "
                "candidate gives zero new information and wastes a "
                "guarded attempt. Derive something genuinely different, "
                "or reconsider your approach entirely — do not resubmit "
                "this value."
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

    # CONFIRMED real failure, found via actual use: a genuinely correct
    # derivation (FLAG_aSiuJHSLfzoQkszD) was submitted as
    # FLAG_FLAG_aSiuJHSLfzoQkszD — the FLAG_ prefix concatenated onto an
    # answer that already had one, rather than replacing it. This burned
    # a real guarded attempt on a trivial formatting slip, not a wrong
    # derivation — worth catching locally before it ever reaches the
    # network, same as the shape check above.
    if candidate.startswith("FLAG_FLAG_"):
        corrected = candidate
        while corrected.startswith("FLAG_FLAG_"):
            corrected = corrected[len("FLAG_"):]
        print(json.dumps({
            "submitted": False,
            "reason": (
                f"Candidate {candidate!r} has the FLAG_ prefix repeated "
                "— refused before any network call. This is almost "
                "certainly a formatting error, not a different answer: "
                "your derivation likely already produced "
                f"{corrected!r}, and FLAG_ got prepended a second time "
                "on top of it. Submit the corrected version directly — "
                "do not treat this as a wrong derivation requiring a "
                "new approach."
            ),
            "corrected_candidate": corrected,
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
    refusal = check_guardrail(problem_id, candidate, log)
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
    result_obj = {
        "submitted": True,
        "result": data.get("result"),
        "attempts_used": attempts_used,
        "attempts_remaining": MAX_ATTEMPTS_PER_PROBLEM - attempts_used,
    }

    # CONFIRMED real failure pattern, found via actual use: a flat
    # "result: false" gives no signal beyond "try again" — and in
    # practice this led directly to guessing spirals (one problem burned
    # all 5 attempts on unrelated words; another burned 4 before being
    # caught manually). A wrong result at attempt 3+ is real evidence
    # the current APPROACH is likely flawed, not just this specific
    # guess — escalate the message accordingly rather than reporting it
    # identically to a first wrong attempt.
    if data.get("result") is False:
        if attempts_used == 3:
            result_obj["strategy_warning"] = (
                "This is your 3rd wrong attempt on this problem. That's "
                "real evidence your current APPROACH may be flawed, not "
                "just this specific guess. STOP before submitting again: "
                "what specific, traced evidence supports your method — "
                "not just this candidate, the whole approach? If you're "
                "choosing candidates based on theme, vibe, or a plausible-"
                "sounding word rather than something you actually derived "
                "from the challenge's real content, that is the problem. "
                "Go find the real mechanism, or search for a public "
                "writeup, before trying again."
            )
        elif attempts_used == 4:
            result_obj["strategy_warning"] = (
                "This is your 4th wrong attempt — only 1 remains before "
                "this problem is PERMANENTLY locked (a human must reset "
                "it; you cannot). Do not spend it on another guess. If "
                "you do not have a rigorously derived, verified answer "
                "right now, do not submit again — research a public "
                "writeup for this specific problem, or move on to a "
                "different problem and come back once you actually have "
                "one."
            )
        elif attempts_used >= MAX_ATTEMPTS_PER_PROBLEM:
            # CONFIRMED GAP: the two branches above stopped at attempt 4,
            # leaving the actual final attempt — the one where a wrong
            # result means permanent exhaustion — with no warning message
            # at all, despite being the single most important moment for
            # one to appear.
            result_obj["strategy_warning"] = (
                f"This was your {attempts_used}th and FINAL attempt for "
                "this problem. It is now PERMANENTLY EXHAUSTED — no "
                "further submissions will be accepted, and only a human "
                "editing the log file directly can reset it. Move on to "
                "a different problem. Do not try to argue, rephrase, or "
                "find a way around this — there isn't one."
            )

    print(json.dumps(result_obj, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_submit = sub.add_parser("submit")
    p_submit.add_argument(
        "problem_id",
        help="The numeric ksnctf problem ID (from its /problem/<N> URL).",
    )
    p_submit.add_argument(
        "candidate", nargs="?", default=None,
        help="The candidate flag directly, as a plain argument. Omit "
             "this and use --candidate-file instead if the candidate "
             "might contain shell-special characters (parentheses, "
             "quotes, colons followed by parens, etc.) — confirmed real "
             "failure: a genuinely correct flag containing ')' broke "
             "bash's eval parsing before this script ever ran at all.",
    )
    p_submit.add_argument(
        "--candidate-file", default=None,
        help="Path to a file — written via the write_file tool, never a "
             "shell command — whose exact content (leading/trailing "
             "whitespace stripped) is the candidate flag. Use this "
             "instead of the positional candidate whenever there is any "
             "chance the candidate contains characters a shell would "
             "misinterpret. A plain file path is virtually always "
             "shell-safe regardless of what the candidate itself "
             "contains.",
    )

    args = parser.parse_args()

    if args.command != "submit":
        return

    if args.candidate_file:
        if args.candidate is not None:
            print(json.dumps({
                "submitted": False,
                "reason": "Provide either a positional candidate or "
                          "--candidate-file, not both.",
            }, indent=2))
            sys.exit(1)
        try:
            with open(args.candidate_file) as f:
                candidate = f.read().strip()
        except OSError as e:
            print(json.dumps({
                "submitted": False,
                "reason": f"Could not read --candidate-file "
                          f"{args.candidate_file!r}: {e}",
            }, indent=2))
            sys.exit(1)
    elif args.candidate is not None:
        candidate = args.candidate
    else:
        print(json.dumps({
            "submitted": False,
            "reason": "Provide either a positional candidate or "
                      "--candidate-file.",
        }, indent=2))
        sys.exit(1)

    cmd_submit(args.problem_id, candidate)


if __name__ == "__main__":
    main()
