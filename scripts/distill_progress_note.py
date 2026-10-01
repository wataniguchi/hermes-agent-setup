#!/usr/bin/env python3
"""Watchdog-side progress-note distillation.

Called by ctf-sweep-watchdog.sh after every session ends (normal exit,
crash, or interrupt) — this is the one mechanism in the project that does
not depend on the agent choosing to write anything down. Every other fix
(the cadence reminder, the submit gate, the fuzzy-match blocker heading)
only fires on the agent's own next/status/submit calls, or on a session
ending with a trailing tool call; a session that avoids ctf_traversal.py
entirely, or ends on a prose-only turn, is unreachable by any of them.
This has happened for real, repeatedly, and real verified work has been
lost each time (most recently session 62f6f5's cross-verified ZipCrypto
decoder, sitting entirely in a live session's own context with no second
note update since the one the hard gate forced near its start).

Deliberately not trusted at face value even though the distillation
model (gpt-oss:120b-96k) was chosen specifically for document-processing
trustworthiness over raw CTF-solving skill: a distilled summary is still
a self-report, and a *different*, cheaper model already used elsewhere
in this project's own delegation role (gemma4:e4b) has been caught
fabricating a result once already, in a structurally similar "report on
what you did" role (session 8bc6a9, a delegated subagent task) -- the
general risk that any model asked to summarize its own or another
session's work can produce a plausible, unverified claim rather than an
accurate one. The distillation prompt requires citing
concrete evidence — file paths, quoted output — for every claim, and any
cited path that doesn't actually exist on disk is treated as a
fabrication signal strong enough to discard the whole distillation
rather than append it. What survives that check still only gets appended
under a clearly machine-generated heading, kept entirely separate from
the agent's own "## Proven" section, so nothing here is ever mistaken
for something a session actually verified itself.

Usage:
    distill_progress_note.py <session_id> <workspace_dir> <model>
                              <ollama_url> <timeout_seconds> <tail_chars>

Exit codes: 0 = distillation appended; 1 = skipped for an ordinary reason
(no held problem, no transcript, model call failed); 2 = discarded after
a failed evidence check (the interesting, worth-logging case).
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request


def find_held_problem(state):
    """Mirrors ctf_traversal.py's own _find_held_problem: the in_progress
    problem most recently handed out, identified by the highest
    last_returned_at. Returns (None, None) if there is none.
    """
    held_id, held_ts = None, -1
    for pid, info in state.get("problems", {}).items():
        if info.get("status") != "in_progress":
            continue
        ts = info.get("last_returned_at")
        if ts is not None and ts > held_ts:
            held_id, held_ts = pid, ts
    return held_id, held_ts


def build_prompt(problem_id, note_text, transcript_tail):
    return f"""You are distilling a CTF-solving session's transcript into a short, \
factual addition to a progress note for problem {problem_id}. Another \
session will read what you write and may trust it without re-checking — \
so every claim you make must be something you can point to directly in \
the transcript below: an exact file path, a command that was run, or a \
specific piece of output. Do not infer, generalize, or round up from \
partial evidence.

Rules, follow exactly:
- Every fact you state must cite a specific file path or quote specific \
output from the transcript. If you cannot cite evidence for a claim, \
omit the claim entirely rather than soften it.
- If the session made no real progress worth recording — crashed \
immediately, repeated earlier work with no new result, or produced \
nothing verifiable — say so in one short line and stop. A short, honest \
"nothing new to add" is correct output, not a failure.
- Never use the words "block", "blocked", or "suspect" in any heading \
you write, including sub-headings — those words are reserved for the \
session's own deliberate blocker declarations elsewhere in this note, \
and must not be triggered by your summary.
- Do not use the heading "## Proven" — that heading is reserved for \
facts the agent itself verified and wrote down; use "### Session notes" \
for your own sub-headings instead.
- Keep this to a few short bullet points. This is a note for a future \
session to act on, not a transcript recap.

=== Current progress note (for context only — do not repeat it) ===
{note_text[-4000:] if note_text else "(no note exists yet for this problem)"}

=== Transcript tail (most recent activity) ===
{transcript_tail}

=== Your output (bullet points only, citing evidence per the rules above) ===
"""


def call_ollama(url, model, prompt, timeout_seconds):
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body.get("response", "").strip()


def cited_paths(text):
    """Every /workspace/... -looking token mentioned in the distilled
    text — the concrete, checkable claims we can actually verify.
    Trailing punctuation (a sentence's period, a comma before "and",
    a closing paren) is stripped first -- confirmed as a real bug during
    testing: an unstripped trailing "." or "," turns a genuinely valid
    citation into a path that can never match os.path.exists, which
    would wrongly flag a correct distillation as fabricated.
    """
    raw = re.findall(r"/workspace/[^\s`'\")]+", text)
    return {p.rstrip(".,;:!?)") for p in raw}


def validate_cited_paths(text, workspace_dir):
    """Returns (ok, bad_paths). A cited path is checked by substituting
    the real host workspace_dir for the /workspace container-internal
    prefix the agent's own transcript always uses.
    """
    bad = []
    for p in cited_paths(text):
        host_path = p.replace("/workspace", workspace_dir, 1)
        if not os.path.exists(host_path):
            bad.append(p)
    return (len(bad) == 0, bad)


def main():
    if len(sys.argv) != 7:
        print(f"usage: {sys.argv[0]} <session_id> <workspace_dir> <model> "
              f"<ollama_url> <timeout_seconds> <tail_chars>", file=sys.stderr)
        return 1

    session_id, workspace_dir, model, ollama_url = sys.argv[1:5]
    timeout_seconds = float(sys.argv[5])
    tail_chars = int(sys.argv[6])

    state_path = os.path.join(workspace_dir, ".ctf_traversal_state.json")
    if not os.path.isfile(state_path):
        print(f"distill: no traversal state at {state_path} — skipping.", file=sys.stderr)
        return 1
    with open(state_path) as f:
        state = json.load(f)

    held_id, held_ts = find_held_problem(state)
    if held_id is None:
        print("distill: no in_progress problem found — nothing to distill against.", file=sys.stderr)
        return 1

    transcript_path = os.path.join(workspace_dir, "session-exports", f"{session_id}.md")
    if not os.path.isfile(transcript_path):
        print(f"distill: no archived transcript at {transcript_path} — skipping.", file=sys.stderr)
        return 1
    with open(transcript_path, encoding="utf-8", errors="replace") as f:
        transcript = f.read()
    transcript_tail = transcript[-tail_chars:]

    note_path = os.path.join(workspace_dir, "progress-notes", f"problem_{held_id}.md")
    note_text = ""
    if os.path.isfile(note_path):
        with open(note_path, encoding="utf-8", errors="replace") as f:
            note_text = f.read()

    prompt = build_prompt(held_id, note_text, transcript_tail)

    try:
        distilled = call_ollama(ollama_url, model, prompt, timeout_seconds)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"distill: model call failed ({e}) — skipping, sweep continues.", file=sys.stderr)
        return 1

    if not distilled:
        print("distill: model returned empty output — skipping.", file=sys.stderr)
        return 1

    ok, bad_paths = validate_cited_paths(distilled, workspace_dir)
    if not ok:
        print(
            f"distill: DISCARDED — cited path(s) that don't exist on disk "
            f"(likely fabrication): {bad_paths}. Not appending to "
            f"{note_path}.",
            file=sys.stderr,
        )
        return 2

    section = (
        f"\n\n## Auto-distilled from session {session_id} "
        f"(machine-generated by {model}, unverified by any agent)\n"
        f"{distilled}\n"
    )
    os.makedirs(os.path.dirname(note_path), exist_ok=True)
    with open(note_path, "a", encoding="utf-8") as f:
        f.write(section)

    print(f"distill: appended {len(distilled)} chars to {note_path} (problem {held_id}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
