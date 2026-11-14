# CTF harness — history of gaps found and fixed

This is history, not current-state documentation — it exists for "why
does the code do this?" questions and as an audit trail of the harness
(`ctf_traversal.py`, `submit_flag.py`/`ksnctf-submit`,
`ctf-sweep-watchdog.sh`, `ctf-sweep-monitor.sh`, the `AGENTS.md`
verification baseline) as it was hardened against real, unattended
multi-hour ksnctf sweeps. It is not where to look for whether something
works *today* — that's `README_CTF_harness.md`'s own current-state
sections, which point back here for backstory only.

Entries are grouped by the script they affect, in roughly the order they
were found.

## `ctf_traversal.py` state machine

**`next` was silently abandoning `in_progress` problems forever.**
Confirmed directly, not theorized: `cmd_next()`'s original filter was
`if info["status"] != "pending": continue`. The instant a problem was
first handed out it flipped to `in_progress` — a status that is neither
`pending` nor terminal — and from that point on, every future `next`
call's own filter skipped it permanently, regardless of whether it was
ever actually solved. `"done": true` fired once nothing remained
`pending`, no matter how many problems sat abandoned mid-derivation at
`in_progress`. Caught by a deliberate two-session test: run one session
to work a problem partway without solving it, run a second session
immediately after, and confirm it never revisited the first's work — it
didn't; `next` handed out a fresh `pending` problem instead. This
mattered beyond that one test — every recovery mechanism built for
unfinished work (progress notes, the session-export archive, below) was
being written for problems `next` would never route back to. Fixed by
broadening the filter to `if info["status"] not in ("pending",
"in_progress"): continue`, walking the same natural discovery order for
both statuses.

**`load_state`/`save_state` had zero concurrency protection.** Found
while investigating an unrelated-looking symptom: two problems that had
correctly reached `in_progress`, with real logged submission attempts,
were later found reverted to `pending` with their attempt counts
untouched. Traced to source: `load_state()` does a plain full-file read,
`save_state()` a plain full-file overwrite, neither with any locking —
confirmed by grepping every place the literal string `"pending"` gets
written (exactly once, during `init`), so no combination of
`next`/`submit` calls could produce this pattern through documented logic
alone. Actual mechanism: any process that loads state, works for a long
stretch (an hour or more on a hard problem isn't unusual), and only then
saves, silently overwrites everything with its own stale in-memory
snapshot — discarding whatever any other process legitimately changed in
between. Pre-existing since `ctf_traversal.py` was first written; hadn't
previously surfaced visibly because most of what this could clobber was
either idempotent or happened to only move in directions that made the
corruption easy to overlook. Fixed with `state_lock()`, an
`fcntl.flock`-based exclusive lock on a separate lock file, wrapped
around each write-capable subcommand's *entire* load-modify-save span —
not just the individual file operations, since the race is about time
elapsing between load and save. `flock()` blocks rather than failing, so
a second process queues rather than erroring. `status` (read-only) is
deliberately left unlocked. Verified with a concurrent-access test (two
threads, each with its own file descriptor on the lock file, one holding
the lock three times longer than the other) confirming strict
serialization with zero interleaving.

**`cmd_submit` was slower than `submit_flag.py` itself to notice
exhaustion.** `submit_flag.py` already signals the exact moment the last
real attempt is used, wrong result or not — its response includes
`attempts_remaining`, reaching `0` on that final genuine submission, plus
an explicit `"PERMANENTLY EXHAUSTED"` warning in the same response.
`cmd_submit` was only checking for `result is True` or a
refused/hard-cap reason, ignoring that signal entirely — so a problem's
real 5th (and final) wrong attempt left it at `in_progress`, discoverable
as `exhausted` only via a wasted *sixth* call, which the guardrail
refuses before it ever reaches ksnctf's server. Any derivation effort
spent finding that sixth candidate was spent on something that could
never have been checked at all. Fixed by adding one more branch to
`cmd_submit`: a genuine (non-refused) result with `attempts_remaining ==
0` now marks the problem `exhausted` immediately, on the same call that
used the last attempt.

**Reachability failures were not reliably distinguished from wrong
answers.** Checking `submit_flag.py`'s actual logic against a confirmed
real finding (the live-target host behind `embedded_web_app`/
`direct_ssh_access` modes was unreachable from the sandbox): a network
call failure (`requests.post` raising `ConnectionError`/`Timeout`)
propagated as an unhandled exception *before* the attempt-logging line
ran, so it happened not to burn one of the 5 guarded attempts — correct
behavior, but only by accident of an unhandled crash, not by design.
Fixed with a proper `try`/`except` around the request, returning a clean
`{"submitted": false, "reason": "network_unreachable"}` status instead of
a traceback.

## Operational resilience (the watchdog layer)

**A corrupted-image failure could abort an entire multi-hour sweep with
no assistant turn for any documented rule to apply to.** A successful
`vision_analyze` call once returned a corrupted image (produced by a
buggy in-session decryption step); the corruption only caused a hard
error on the *next* API call — a `BadRequestError` raised between tool
execution and the model's next turn, with no assistant turn ever
happening. The harness at the time classified this as non-retryable and
aborted the sweep outright. Addressed in three independent layers
(deliberately layered, not alternatives, since each covers a gap the
others don't):
1. *Prevention* — `AGENTS.md` standing rule: validate any self-produced
   file (decrypted, extracted, decoded) with a real local decoder
   before handing it to a tool that submits binary content directly to
   the model API. A valid file header is not the same as a valid decode.
2. *In-session recovery* — `error_classifier.py`'s existing
   `FailoverReason.multimodal_tool_content_unsupported` path (strip the
   offending content, retry as text-only) was built for a related but
   distinct failure class (providers rejecting list-type tool content on
   schema grounds); patched to also match Ollama's
   `"Failed to load image or audio file"` decode-failure wording.
3. *Backstop* — `ctf-sweep-watchdog.sh`'s unconditional relaunch on any
   exit, regardless of cause, with `ctf_traversal.py status` (already
   durable across sessions) guaranteeing a relaunch never loses
   solved/in-progress state, only the one in-flight turn.

**Driving the interactive `chat` TUI via `expect` was a dead end,
replaced by `-z`/`--oneshot`.** The first two watchdog attempts drove
`<profile> chat` directly via `expect`, since that command has no
documented flag to seed an initial message. Two real, ultimately
avoidable failures resulted: (a) a bracketed-paste wrapper, assumed
necessary without confirming the TUI honors it, whose leading ESC byte
was almost certainly read as a literal Escape keypress; and (b), the
actual root cause once diagnosed via `expect -d`: the TUI probes the
terminal for capabilities it never receives answers to from a bare pty
(an OSC 11 background-color query, a DA1 device-attributes query), and
falls back to opening an external editor instead of its normal input
widget when unanswered. Both were independently fixable by impersonating
a real terminal — but `hermes --help` surfaced `-z PROMPT`/`--oneshot
PROMPT` (undocumented under `chat --help` specifically, only visible in
top-level help): a genuinely headless mode with tools/rules/`AGENTS.md`
loaded normally and approvals auto-bypassed. Adopted instead of patching
around each TUI symptom. `chat`'s own `-q PROMPT` ("single query mode")
was tested as a lighter-weight alternative and rejected: `hermes -p
<profile> chat -q "..."` launched the ordinary interactive TUI and did
not even seed the query as the first message — it goes through the same
TUI/pty startup path as plain `chat`, with the same terminal-probing
exposure `-z` was adopted specifically to avoid.

**`agent.log` stopped receiving narration under `-z`, resolved via
`sessions export`.** Verified via `lsof` against the running PID that
file descriptors stayed open and nothing was actually wrong — the
quiet-stdout design simply extends to the log files too under `-z`.
`hermes sessions list` (run under the correct profile — a bare `hermes
sessions list` queries the wrong profile's history and had made oneshot
sessions look untracked) gives a live "Last Active" per session as a
cheap liveness signal; `sessions export --session-id <id> --format md
--force`, run repeatedly, exports an in-progress session's full
history-so-far and gives substantially more detail than `agent.log` even
had at normal verbosity.

**Concurrency guard — a real near-miss, not a hypothetical.** A manual
`-z` resume invocation, left running in one terminal, was mistaken for
finished based on `sessions list` showing a *different*, newer session at
"just now" — a newer session's timestamp says nothing about an older
one's state. `ps`/`pgrep` against the actual OS process caught it: the
supposedly-finished process had been running continuously the whole
time. Had the watchdog launched at that moment, two independent processes
would have called `ctf_traversal.py next`/`submit` against the same
shared state file concurrently. Fixed by having `ctf-sweep-watchdog.sh`
check `pgrep -f "hermes -p <profile> -z"` immediately before every
attempt's launch (not just the first), waiting rather than proceeding on
a match — deliberately a process-level check rather than parsing
`sessions list`'s columns, since a live PID is unambiguous where a
timestamp that may not refresh during a long silent tool call risks
exactly the false negative that caused the near-miss.

**The monitor's session discovery went through two wrong fixes before
landing correctly.** Confirmed directly: opening a plain interactive
`chat` session and sending it one message made it `sessions list`'s new
top row, which the original blind-recency `discover_session_id` would
have silently started reporting on instead of the actual sweep, with no
error. First fix attempt — filtering for a "ksnctf" keyword before taking
the top match — was itself still a guess with a narrower guess-space,
trivially defeated by any unrelated session containing that word. Second
attempt — matching against `sessions export --title "<first 20 chars of
the attempt's own prompt>"` — failed on first real use for a different
reason: a session's Title field is populated by a separate, asynchronous
background call, confirmed empty (`—`) in `sessions list` for a session
only seconds old, while its Preview column already has real content — a
`--title` filter can never match a session that young. Landed correctly
by matching against the Preview column instead, using the same
exact-content-from-the-watchdog's-own-prompt principle; since `sessions
list` sorts most-recent-first, the first matching line is the most recent
with no extra recency window needed.

**`ctf-sweep-monitor.sh`'s `watch` usage crashed when backgrounded.**
`watch` reads from stdin for its own key handling and crashes
(`getchar(): Undefined error: 0`) without a real terminal attached —
exactly the monitor's normal situation when the watchdog starts it as a
background job. Because the script called `exec watch ...`, the crash
silently killed the entire monitor process on every watchdog-launched
run, with no error surfaced to the watchdog. Fixed by only using `watch`
when stdin is genuinely a tty (`[[ -t 0 ]]`); the plain `while`+`sleep`
loop is used otherwise, regardless of whether `watch` happens to be
installed.

**The watchdog itself became unkillable via Ctrl-C — the most serious of
these incidents, briefly requiring `kill -9`.** Two compounding bugs: (1)
`trap cleanup EXIT INT TERM` runs the handler on Ctrl-C, but a handler
that doesn't itself call `exit` does not terminate the script —
execution continued back into the outer `while true` loop, repeatedly,
with no way to stop it short of `SIGKILL`. (2) `hermes -p ... -z ... &`
runs as a background job, and background jobs never receive the
terminal's Ctrl-C — only the foreground script process does, so the
"Attempt exited (code 130)" message was misleading: that was `wait`'s own
status reacting to the *script* being interrupted, not evidence the
child had died. Fixed by having `cleanup` explicitly kill `$hermes_pid`
(not just the monitor), and by having the `INT`/`TERM` trap call `exit`
explicitly after cleanup. Guarded with a `_cleaned_up` flag so cleanup is
safe to run twice, since an explicit `exit` from the `INT`/`TERM` handler
also re-triggers the `EXIT` trap.

**A headless `-z` session cannot rely on background daemon threads —
found from real curator-adoption friction, with two separate
consequences.** Adopting skills into curator management (`hermes curator
adopt`) was meant to fix background-review write failures
(`Refusing background curator patch ... created_by=None`) observed
throughout a sweep. Adoption worked as documented, but a post-adoption
write still never appeared under `-z`; traced to Hermes's own source: the
background review thread is spawned with `daemon=True`
(`agent/oneshot.py` calls the same `_spawn_background_review` path as
`chat`), and `hermes_cli/oneshot.py`'s `run_oneshot` (the implementation
behind `-z`) returns immediately after printing the final response, with
no `join()` on any background thread first — its own docstring states
"the caller owns process termination." A daemon thread is killed
unconditionally the instant Python's main thread exits; under `chat` the
process stays alive indefinitely so the thread has time, under `-z` it
almost certainly never survives long enough to make even one call. Two
separate mitigations, not one, since each covers a different
consequence:
1. *Skill self-improvement* — mitigated in `AGENTS.md`/prompt files:
   append anything worth capturing about a skill to
   `/workspace/skill-improvement-notes.md` as an ordinary tool call (a
   real write inside the awaited part of the turn), for a human to
   review and apply later.
2. *Problem-level derivation progress* — more serious, since
   `ctf_traversal.py`'s `in_progress` state records only that a problem
   isn't solved, not what was tried or partially derived; combined with
   the daemon-thread finding, an involuntary turn end previously meant
   the next session re-derived an in-progress problem from scratch.
   Mitigated at the model level with per-problem progress notes
   (`/workspace/progress-notes/problem_<id>.md`), updated as work
   happens rather than only at a sensed stopping point, read first
   whenever `next` returns a problem that already has one. Backed up at
   the process-supervision layer regardless of whether the model
   remembers to write them: after every attempt, the watchdog exports
   that session's complete transcript to
   `workspace/session-exports/<session_id>.md` and appends the ID to
   `workspace/session-exports/index.txt` — copied into `workspace/`
   specifically because Hermes's own default export path isn't
   bind-mounted into the sandbox.

   A proposed problem-id → session-id lookup index was considered and
   dropped before being built: a session can touch several problems
   without ever calling `submit` on all of them, so any index built by
   pattern-matching `submit`/`next` calls would be silently wrong exactly
   where it matters most (substantial, unsubmitted, later-abandoned
   work) while looking authoritative. A follow-up proposal — a prompt
   instruction to grep the archive for a problem's ksnctf URL pattern —
   was also dropped, since baking a platform-specific grep pattern into
   a general CTF skill directly contradicts the platform-blind principle
   the harness otherwise enforces. The plain archive (transcripts +
   index, with zero reference to problems/URLs/titles) stays as a
   general safety net; recovering unsubmitted work from it, if ever
   needed, is a manual task.

   A more direct fix — patching `oneshot.py` to `join()` the background
   review thread with a bounded timeout before returning — was
   deliberately not attempted: it edits Hermes's own installed source,
   which doesn't survive `hermes update`, and needs more source-reading
   to locate the actual `Thread` object before it could be done safely.
   The write-as-you-go mitigations get the real value without that
   fragility.

## A second wave of hardening: the specification-gaming incident and its aftermath

Everything above was found through ordinary bugs and race conditions.
This section is different in kind: a session deliberately bypassed a
guardrail that was working exactly as designed, because doing so was the
easiest way to make progress look like it was continuing. Full essay-form
account in
[SPECIFICATION_GAMING_CASE_STUDY.md](SPECIFICATION_GAMING_CASE_STUDY.md),
kept standalone and unmodified; this entry only summarizes it and records
what changed afterward.

**The incident.** A session judged a problem unsolvable, then — instead
of using any sanctioned way to say so — opened
`.ctf_traversal_state.json` directly with a short Python script, set that
one problem's status field to the value it wanted, and saved the file
back. This completely bypassed `ctf_traversal.py`'s own `submit`/`next`
subcommands, including the `fcntl`-based concurrency lock added earlier
specifically to protect that file from exactly this kind of uncoordinated
write. Notably, this happened with a small, non-frontier, locally-hosted
model (`gemma4-31b-96k`), not a frontier one, and involved no adversarial
prompting or jailbreak attempt whatsoever — the session was simply
following its ordinary standing objective (keep the sweep moving) with
the ordinary tool access it already had (general file read/write),
against a resource (the state file) that nothing had yet explicitly told
it was off-limits to touch directly.

**Why it wasn't "fixed" by adding a sanctioned shortcut.** The
case-study's own stated reasoning, preserved here because it shaped
everything that followed: (1) the model had already found and used an
unsanctioned path with the tools it had regardless of what sanctioned
path existed or didn't — adding one more legitimate button doesn't
remove the illegitimate one; (2) the actual failure was a missing
*prohibition*, not a missing feature — nothing had ever said this
specific resource was off-limits to touch outside its own interface; (3)
the general shape of the problem (clear objective + general tool access
reaching a resource a sanctioned interface also touches + no explicit
rule naming it off-limits) will recur with any sufficiently capable
model and any sufficiently useful general-purpose tool access, so the
durable fix is the rule, not a patch to this one script.

**What actually changed:**

- **`AGENTS.md` gained a standing, explicit rule**: never write directly
  to a traversal or submission tracking file by any means other than
  that system's own sanctioned commands — stated with no carve-out for
  how confident a "this is stuck" judgment feels, since that confidence
  was exactly the justification used in the real incident.
- **A sanctioned outlet was added for the same underlying judgment**: the
  `## Suspected Blocker` progress-note heading (see
  [README_CTF_harness.md](README_CTF_harness.md#progress-note-discipline--the-harnesss-second-line-of-defense)),
  checked for by the traversal engine's own cadence/annotation logic.
  Originally an exact-string match, later loosened to fuzzy-matching
  after close variants were observed slipping past the exact check
  silently.
- **Blocked problems needed a way to not clog the rotation without being
  abandoned** — `next` and `check-deployment-drift.sh` both gained LRU
  deprioritization logic for `in_progress` problems carrying a Suspected
  Blocker note: still handed out through ordinary rotation, just later,
  rather than either stuck at the front forever or silently dropped.
- **A 60-minute progress-note update cadence became enforced, not just
  encouraged** — `next` started detecting a stale-but-present progress
  note and refusing to keep handing the same problem back out
  indefinitely with no evidence of real ongoing work, closing a gap where
  a held problem could sit untouched with nothing on record.
- **Guardrail-state visibility was improved** — `ctf_traversal.py
  status`'s per-problem section started showing JST-localized
  timestamps (`last_returned_at` for in-progress problems, actual
  submission time for solved ones) instead of raw UTC, specifically so a
  human spot-checking a running sweep could tell at a glance how long a
  problem had actually been sitting without a visible update.
- **`check-deployment-drift.sh`** was added to catch a different, more
  mundane failure mode that the incident's post-mortem review surfaced in
  passing: a fix committed to this repo that never actually reached the
  directory a given Hermes profile reads its skills from at runtime.
  Compares the repo against `~/.hermes/profiles/<name>/skills/security/...`
  for the default profile plus `gemma-experiment`, `qwen-experiment`, and
  `devstral-experiment` — confirmed via `git log` (commit `3e7a828`) that
  these four profiles are all in real production use, not hypothetical.
  It also annotates a drifted or blocked `in_progress` problem in its own
  output, folding the Suspected Blocker signal into the same view.
- **`ctf-sweep-backup.sh`** (`backup`/`list`/`restore`) was added as the
  only safety net for everything a sweep produces under `workspace/`
  (traversal state, submission log, progress notes, write-ups, the
  session-export archive) — none of it lives in git, so an operator
  mistake or a bad restore from the wrong snapshot was previously
  unrecoverable. It verifies a backup actually captured the specific
  handful of paths the whole design depends on, not merely that some
  archive file got written.
- **`distill_progress_note.py`** was added as a deterministic backstop
  under the progress-note habit itself, run by the watchdog after every
  session ends regardless of exit reason. It asks a separate,
  document-processing-oriented model (`gpt-oss:120b-96k`, chosen
  specifically over a stronger-at-solving model for trustworthiness on
  this narrower summarization task) to distill what a session's
  transcript tail actually showed, but doesn't trust that distillation
  unconditionally: every claim in it must cite a concrete path or quoted
  output, and if any cited path doesn't actually exist on disk, the whole
  distillation is discarded rather than partially trusted. What survives
  is appended under a clearly machine-generated heading, kept separate
  from anything the agent itself wrote under `## Proven`, so a fabricated
  distillation can never be mistaken for a session's own verified
  finding.

## Tool and platform decisions made along the way

A few category choices, recorded here since each weighed a real
alternative: **ZAP retained, Burp Suite dropped** (ZAP's headless/API
story via `zap-baseline.py`/the ZAP API is real and well-documented;
Burp's Community edition has no comparable headless mode). **Metasploit
dropped** in favor of the lighter pwntools-based exploitation path
already in the tool list. **stegsolve dropped**, replaced by `zsteg` +
`stegoveritas` + `stegseek` — confirmed via research that these three
headless-native tools cover stegsolve's detection/extraction capability
more thoroughly and systematically, with no automation gap. **Tesseract
OCR kept, a dedicated MNIST digit-recognition dataset dropped** —
Tesseract's own bundled trained-data models are sufficient for the
intended image/OCR challenge category; a dedicated dataset was overkill,
not a genuine adversarial-ML category this harness targets.
