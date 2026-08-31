"""
ctf_traversal.py — platform-blind traversal engine: discovers the full
problem set, hands problems to the agent one at a time in natural order,
skips unworkable ones fast using a cached reachability check, and tracks
solved/skipped/exhausted state across calls.

HONEST ABOUT WHAT THIS IS: this script does not and cannot solve CTF
problems itself — deriving a flag from a decompiled binary requires
genuine reasoning, which is the agent's job, not something a
deterministic script can do. This is orchestration and bookkeeping only:
it removes the tedium of "what should I work on next, is it even
reachable, have I already tried this" from the agent, so the agent's
actual reasoning effort goes toward the puzzle itself.

Platform-blind by design: takes --discover, --submit, and (via
ctf-solver's own --fetch) the fetch script as explicit paths, never
hardcoded. Today these point at ksnctf-discover/ksnctf-fetch/
ksnctf-submit; a future platform means new scripts and updated defaults,
not changes to this file's own logic. The agent sources these paths (and
--scope) from AGENTS.md's current-scope section, same as every other
skill in this project.

Usage:
    python3 ctf_traversal.py init --discover <path> --solver <path> --submit <path> --scope <host>
    python3 ctf_traversal.py next
    python3 ctf_traversal.py submit <problem_id> <candidate_flag>
    python3 ctf_traversal.py submit <problem_id> --candidate-file <path>
    python3 ctf_traversal.py status

CONFIRMED real failure that motivated --candidate-file: a genuinely
correct flag (FLAG_This_is_right_:)) was submitted as a raw shell
argument via the agent's own terminal tool, and the unescaped ')' broke
bash's `eval` parsing before this script ever ran — a real, correct
answer lost purely to shell syntax, unrelated to whether the derivation
was right. --candidate-file sidesteps this entirely: write the exact
candidate to a file via the write_file tool (which never touches a
shell), then pass the file's path — a plain filename is virtually
always shell-safe regardless of what characters the candidate itself
contains. This script's own internal call to the underlying submit
script was never actually at risk — it already used Python's list-form
subprocess.run, which bypasses the shell entirely; the exposure was
specifically in how an agent invokes THIS script's own CLI via a raw
shell command.

State persists in /workspace/.ctf_traversal_state.json, so it survives
across sessions and separate script invocations — `init` needs to run
once per traversal campaign, not once per call.
"""
import sys
import os
import re
import json
import socket
import time
import subprocess
import argparse
import fcntl
import contextlib

STATE_PATH = "/workspace/.ctf_traversal_state.json"
STATE_LOCK_PATH = STATE_PATH + ".lock"

# Fast, lightweight reachability check — a raw TCP connect, not a full
# HTTP request. This is the "fast and global" requirement: checked ONCE
# during init (or lazily on first need) and cached, not rediscovered
# slowly via a full-timeout hang for every problem that happens to
# depend on the same host. Port 80 is used as a general connectivity
# proxy — a coarse signal, not an exhaustive check of every specific
# port a given problem might actually use (e.g. a nonstandard SSH port).
REACHABILITY_CHECK_PORT = 80
REACHABILITY_CHECK_TIMEOUT = 4.0


def check_host_reachable(host: str) -> bool:
    try:
        with socket.create_connection((host, REACHABILITY_CHECK_PORT), timeout=REACHABILITY_CHECK_TIMEOUT):
            return True
    except OSError:
        return False


@contextlib.contextmanager
def state_lock():
    """Exclusive lock spanning an entire load-modify-save cycle.

    load_state()/save_state() do a plain full-file read and a plain full-file
    overwrite, with no locking of their own — safe in isolation, but not
    when two processes can each run load -> mutate -> save concurrently.
    Whichever one calls save_state() last wins outright, silently discarding
    everything the other changed in between its own load and save. This is
    not hypothetical: confirmed directly against real state, twice — two
    problems correctly marked in_progress by one session were found
    reverted back to their original pending default after a separate,
    long-running session (over an hour, not unusual for a hard problem)
    finished and wrote back its own now-stale in-memory snapshot.

    The fix is a lock around the *whole* read-modify-write span each
    subcommand performs, not just around the individual open() calls
    inside load_state()/save_state() — the race is about time elapsing
    between load and save, not about either file operation itself being
    unsafe. Every subcommand that both loads and saves state (init, next,
    submit) wraps its full body in `with state_lock():`.

    A separate lock file, not the state file itself, avoids fighting over
    one file descriptor's read/write mode across calls that both read and
    write the same path; flock() blocks (waits) rather than failing
    immediately, so a second process just queues behind the first rather
    than erroring out.
    """
    with open(STATE_LOCK_PATH, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def load_state() -> dict:
    if not os.path.isfile(STATE_PATH):
        print(json.dumps({
            "error": "No traversal state found — run `init` first.",
        }, indent=2))
        sys.exit(1)
    with open(STATE_PATH) as f:
        return json.load(f)


def save_state(state: dict):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def run_script(script_path: str, args: list) -> dict:
    result = subprocess.run(
        ["python3", script_path] + args,
        capture_output=True, text=True,
    )
    if result.returncode != 0 and not result.stdout.strip():
        return {"_error": True, "stderr": result.stderr}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"_error": True, "reason": "non-JSON output", "stdout": result.stdout}


def cmd_init(discover_script: str, solver_script: str, submit_script: str, scope: str):
    # CONFIRMED BUG, found via real use: init previously accepted any
    # path string for --discover/--solver/--submit with zero validation,
    # writing it straight into persistent state. A single wrong path
    # given here (e.g. pointing --solver at ksnctf-fetch's own script,
    # which only has a "fetch" subcommand, not the "solve" subcommand
    # next() actually calls) silently cascaded into every subsequent
    # problem being marked needs_manual_review, with no clear signal
    # pointing back at the actual root cause until someone dug in
    # manually. Validate up front instead.
    for label, path in [("discover", discover_script), ("solver", solver_script), ("submit", submit_script)]:
        if not os.path.isfile(path):
            print(json.dumps({
                "initialized": False,
                "reason": f"--{label} path does not exist: {path!r}. "
                          "Not writing this into state — fix the path "
                          "and re-run init.",
            }, indent=2))
            sys.exit(1)

    # Smoke-test the solver specifically, since it's the one whose wrong
    # value caused real, silent, cascading damage — confirm it actually
    # understands the "solve" subcommand next() depends on, using a
    # deliberately invalid dummy call that should fail CLEANLY (a usage
    # error) rather than with an "unrecognized command" style failure
    # that would indicate the wrong script was given entirely.
    smoke_test = subprocess.run(
        ["python3", solver_script, "solve", "--help"],
        capture_output=True, text=True,
    )
    combined_output = (smoke_test.stdout + smoke_test.stderr).lower()
    # NOT a naive "does 'solve' appear in the output" check — argparse's
    # own rejection message for an invalid subcommand echoes back
    # whatever was attempted (e.g. "invalid choice: 'solve'"), so a
    # substring check for "solve" would incorrectly pass even for the
    # wrong script. Check for argparse's specific bad-input signatures
    # instead, which only appear on genuine rejection.
    looks_wrong = smoke_test.returncode != 0 and (
        "invalid choice" in combined_output or "unrecognized" in combined_output
    )
    if looks_wrong:
        print(json.dumps({
            "initialized": False,
            "reason": f"--solver ({solver_script!r}) does not appear to "
                      "support a 'solve' subcommand at all — this looks "
                      "like the wrong script was given. Double-check "
                      "this points at ctf_solver.py (or an equivalent "
                      "for a different platform), not a fetch/discover/"
                      "submit script.",
        }, indent=2))
        sys.exit(1)

    discover_result = run_script(discover_script, ["list"])
    if discover_result.get("_error"):
        print(json.dumps({"initialized": False, "stage": "discover", "error": discover_result}, indent=2))
        sys.exit(1)

    problems_list = discover_result.get("problems", [])
    if not problems_list:
        print(json.dumps({
            "initialized": False,
            "reason": "Discovery returned zero problems — inspect the "
                      "discover script's output directly before proceeding.",
        }, indent=2))
        sys.exit(1)

    scope_reachable = check_host_reachable(scope)

    problems = {}
    for p in problems_list:
        problems[p["id"]] = {
            "title": p.get("title"),
            "url": p.get("url"),
            "status": "pending",  # pending | solved | skipped_unreachable | exhausted | needs_manual_review
        }

    state = {
        "discover_script": discover_script,
        "solver_script": solver_script,
        "submit_script": submit_script,
        "scope": scope,
        "scope_reachable": scope_reachable,
        "scope_checked_at": time.time(),
        "problems": problems,
    }
    with state_lock():
        save_state(state)

    print(json.dumps({
        "initialized": True,
        "total_problems": len(problems),
        "scope": scope,
        "scope_reachable": scope_reachable,
        "note": (
            "scope_reachable is cached from this one check — it will "
            "NOT be re-verified per problem. Re-run `init` later if you "
            "want to re-check whether connectivity has changed."
        ) if not scope_reachable else "Scope host is reachable.",
    }, indent=2))


def needs_scope_host(fetch_result: dict) -> bool:
    modes = fetch_result.get("modes_detected", [])
    return ("embedded_web_app" in modes or "direct_ssh_access" in modes) \
        and "downloadable_file" not in modes


def cmd_next():
    with state_lock():
        state = load_state()

        # Deliberately includes "in_progress", not just "pending": once a
        # problem is first handed out it flips to in_progress and, before
        # this fix, would never be returned by `next` again regardless of
        # whether it was ever solved — leaving it permanently abandoned the
        # moment any later problem got attempted, and letting `done: true`
        # fire while real, unfinished work sat untouched. Walking the same
        # natural discovery order for both statuses means an in_progress
        # problem is picked up again before any later pending one, giving
        # progress-notes/session-export recovery (see AGENTS.md, ctf-solver
        # SKILL.md) an actual chance to be used rather than being written
        # for a problem `next` would never return to.
        for problem_id, info in state["problems"].items():
            if info["status"] not in ("pending", "in_progress"):
                continue

            fetch_result = run_script(
                state["solver_script"],
                ["solve", info["url"], "--scope", state["scope"]],
            )

            if fetch_result.get("_error"):
                info["status"] = "needs_manual_review"
                save_state(state)
                continue

            if needs_scope_host(fetch_result) and not state["scope_reachable"]:
                # Instant, no network call — consults the cached reachability
                # result from init rather than re-discovering the same
                # unreachability slowly for this problem too.
                info["status"] = "skipped_unreachable"
                save_state(state)
                continue

            info["status"] = "in_progress"
            save_state(state)
            print(json.dumps({
                "problem_id": problem_id,
                "title": info["title"],
                "url": info["url"],
                "fetch_result": fetch_result,
                "reminder": (
                    "This script does not derive flags — that's your job. "
                    "Once you have a genuinely derived, self-verified "
                    "candidate, submit it via: "
                    "python3 ctf_traversal.py submit " + problem_id + " <candidate_flag>"
                    " (or, if the candidate might contain shell-special "
                    "characters like parentheses or quotes, write it to a "
                    "file via write_file and use --candidate-file <path> "
                    "instead — a raw shell argument with unescaped special "
                    "characters has broken a genuinely correct submission "
                    "before)."
                ),
            }, indent=2))
            return

        print(json.dumps({
            "done": True,
            "summary": summarize(state),
        }, indent=2))


def cmd_submit(problem_id: str, candidate: str):
    with state_lock():
        state = load_state()

        if problem_id not in state["problems"]:
            print(json.dumps({
                "submitted": False,
                "reason": f"problem_id {problem_id!r} is not in the current "
                          "traversal's problem set — check for a typo.",
            }, indent=2))
            sys.exit(1)

        result = run_script(state["submit_script"], ["submit", problem_id, candidate])

        if result.get("result") is True:
            state["problems"][problem_id]["status"] = "solved"
            save_state(state)
        elif result.get("reason", "").startswith("REFUSED") and "hard cap" in result.get("reason", ""):
            state["problems"][problem_id]["status"] = "exhausted"
            save_state(state)
        elif result.get("attempts_remaining") == 0:
            # CONFIRMED real gap, found from actual use: a genuine (not
            # refused) submission that used the last of the 5 real
            # attempts and still came back wrong left status as
            # "in_progress" here, even though submit_flag.py's own
            # result already signals total exhaustion via
            # attempts_remaining == 0 (and its own "PERMANENTLY
            # EXHAUSTED" strategy_warning text) — that signal was
            # simply never acted on. The only way to discover
            # exhaustion was a wasted sixth submit call, which the
            # guardrail refuses before it ever reaches the network —
            # meaning any derivation effort spent finding that sixth
            # candidate was spent on something that could never have
            # been checked at all. Acting on the signal immediately
            # avoids that wasted work.
            state["problems"][problem_id]["status"] = "exhausted"
            save_state(state)
        # A plain wrong-flag result with attempts remaining, or a
        # network-error result, leaves status as "in_progress" — both
        # remain legitimately retryable.

        print(json.dumps(result, indent=2))


def summarize(state: dict) -> dict:
    counts = {}
    for info in state["problems"].values():
        counts[info["status"]] = counts.get(info["status"], 0) + 1
    return counts


def cmd_status():
    state = load_state()
    print(json.dumps({
        "scope": state["scope"],
        "scope_reachable": state["scope_reachable"],
        "summary": summarize(state),
        "problems": state["problems"],
    }, indent=2))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init")
    p_init.add_argument("--discover", required=True,
                         help="Path to the platform's discover script")
    p_init.add_argument("--solver", required=True,
                         help="Path to ctf_solver.py (or equivalent) for "
                              "per-problem acquire+classify")
    p_init.add_argument("--submit", required=True,
                         help="Path to the platform's submit script")
    p_init.add_argument("--scope", required=True,
                         help="Allowed attack-target host, sourced from "
                              "AGENTS.md's current scope section")

    sub.add_parser("next")

    p_submit = sub.add_parser("submit")
    p_submit.add_argument("problem_id")
    p_submit.add_argument(
        "candidate", nargs="?", default=None,
        help="The candidate flag directly, as a plain argument. Omit "
             "this and use --candidate-file instead if the candidate "
             "might contain shell-special characters (parentheses, "
             "quotes, etc.) — confirmed real failure: a genuinely "
             "correct flag containing ')' broke bash's eval parsing "
             "before this script ever ran.",
    )
    p_submit.add_argument(
        "--candidate-file", default=None,
        help="Path to a file — written via the write_file tool, never a "
             "shell command — whose exact content (whitespace stripped) "
             "is the candidate flag. Use this instead of the positional "
             "candidate whenever the candidate might contain characters "
             "a shell would misinterpret.",
    )

    sub.add_parser("status")

    args = parser.parse_args()

    if args.command == "init":
        cmd_init(args.discover, args.solver, args.submit, args.scope)
    elif args.command == "next":
        cmd_next()
    elif args.command == "submit":
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
    elif args.command == "status":
        cmd_status()


if __name__ == "__main__":
    main()
