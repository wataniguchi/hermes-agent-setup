# range-assess extension — design decisions & lessons learned

A **curated register** of the design changes and lessons from maturing this
skill against a real 21-host Proxmox range — not a commit mirror. Exact diffs,
mechanical details, and one-off housekeeping live in git history and in the
present-tense `README*` / `SKILL.md` docs; only decisions and lessons likely to
matter for future work survive here.

Each entry carries:

- **Type** — the class of problem.
- **Symptom** — what was observed to go wrong (or the inefficiency), and where it surfaced.
- **Root cause** — why it happened.
- **Fix** — what changed, and in which file(s).
- **Lesson** — the durable takeaway.

Entries are in the order each was found. The numbers are for reference *within
this file only* — they are not stable across revisions, so cite an entry by its
title, not its number, from code comments or other docs.

---

## 1. Background long bridge calls; never delete on an ambiguous error

- **Type:** Clone & network reliability
- **Symptom:** A foreground `/vm/clone`'s client-side execution-timeout fired long before the server-side clone finished. The agent read the timeout as failure, retried the same `new_vmid`, got a real "in progress" rejection, misread *that* as a stale leftover, and deleted its own nearly-finished clone — forcing a third clone from scratch.
- **Root cause:** A blocking bridge call blocks for the operation's full real duration; a client-side timeout says nothing about server-side success. Deleting on an ambiguous error assumed the vmid was junk.
- **Fix:** `range-sweep-init-prompt.txt` + `SKILL.md` — always background `/vm/clone` and `/vm/<vmid>/start` from the first attempt; never delete a vmid on a failed/ambiguous response without first checking whether it exists further along than the error implies.
- **Lesson:** A client-side timeout ≠ a server-side failure. Never take a destructive action (delete/rollback) on ambiguous state without a read-only reality check first.

## 2. Agent-backgrounded jobs outlive the watchdog

- **Type:** Autonomy safety (operational)
- **Symptom:** Ctrl-C on `range-sweep-watchdog.sh` stops only the oneshot `hermes -z` process; a `terminal(background=true)` job the agent launched itself (a backgrounded clone/poll chain) keeps running and keeps calling the bridge. Restarting the bridge doesn't stop it either.
- **Root cause:** Those jobs are owned by Hermes's own long-lived gateway process inside the sandbox container, not by the watchdog, which has no visibility into or reach over them.
- **Fix:** Documented in `range-sweep-watchdog.sh`'s header and interrupted-attempt message, with the real remedy (`docker exec <container> ps -ef`, kill the pid, or `docker restart <container>`). No code-level fix is available from this side.
- **Lesson:** The watchdog can't kill what the agent backgrounded. Anything that must be externally killable has to run as a process the killer actually owns.

## 3. Give the agent a read-only reality check, not inference from mutating calls

- **Type:** Bridge / API design
- **Symptom:** An operator destroyed a working clone directly in Proxmox (out of band). The agent was left with a stale `working_vmid` and no read-only way to check reality — the bridge exposed only mutating calls — so it guessed from an ambiguous `exec` error.
- **Root cause:** A mutating-only API forces the agent to infer state from side-effect errors.
- **Fix:** `proxmox_bridge.py` — new read-only `GET /vm/{vmid}/status` (`{"exists": false}` for a destroyed vmid; deliberately not behind the golden-image guard, since it's harmless). `range_traversal.py` — new `reset-host` that clears a stale `working_vmid` and both scan statuses (leaving recorded findings intact) so `next` re-issues `clone_and_scan`.
- **Lesson:** Always give an autonomous agent a read-only way to observe real state. Never make it infer reality from the error text of a mutating call.

## 4. A clone isn't usable until its golden MAC is in place

- **Type:** Clone & network reliability
- **Symptom:** Clones came up with no IP (and the NIC administratively down); separately, clones started too early (`9110`/`9112`) landed `scan_failed`.
- **Root cause:** Proxmox gives a full clone a fresh random MAC, but this range's dnsmasq reservations and the guests' NetworkManager profiles are keyed to each golden image's original MAC — so a fresh-MAC clone matches no reservation and never configures its NIC. Starting a clone before the MAC is corrected bakes the wrong MAC in.
- **Fix:** `proxmox_bridge.py` `clone_vm()` transplants the template's own `net0`/MAC onto the new vmid before returning. The bridge also refuses `start`/`stop`/`delete`/`rollback` while a clone is in progress, re-applies the MAC with retries, and fails loudly (500) if it can't. Safe under the one-live-clone-per-golden invariant.
- **Lesson:** A clone is not ready the moment Proxmox returns — its identity (MAC → reservation → IP) must be fixed first. Enforce "don't start or probe early" in the bridge, not in prompt wording.

## 5. Code-enforce cross-referencing invariants

- **Type:** State integrity
- **Symptom:** `result_*.json` / `remediation_result_*.json` were named by the finding's **golden** vmid, while the attempt logs key everything by `working_vmid:finding_id` — so matching a human-readable result file to its log entry required memorizing the golden↔working mapping.
- **Root cause:** Both prompts told the agent to write "a file of your own choosing," so it settled on golden vmid as a reasonable-but-wrong default.
- **Fix:** `range_poc.py` / `range_remediate.py` `report` now also write a canonical `result_<working_vmid>_<finding_id>.json` themselves, regardless of what `--result-file` the agent passed; the prompts name that exact path.
- **Lesson:** If an invariant matters for later cross-referencing, guarantee it in code — don't hope the prompt is followed.

## 6. Evidence must be a re-runnable artifact

- **Type:** PoC / remediation rigor
- **Symptom:** `commands_run` entries were sometimes a prose description of a technique ("python3 hashlib brute-force of ~40 candidates") rather than the actual command or script.
- **Root cause:** The prompt accepted a narrative where an independently-runnable artifact was needed.
- **Fix:** The prompts now require any multi-step technique to be saved as a real file in `/workspace` and cited by its literal path, plus a **second** script proving any offline-derived credential actually works against the live target.
- **Lesson:** A claim is only verifiable if another session can open and re-run the exact artifact. A description of what a script would do is not evidence.

## 7. "No remediation channel" is a correct result, not a failure

- **Type:** PoC / remediation rigor; reporting
- **Symptom:** The remediation prompt implied some standing admin channel always exists for a network-scope fix. For the `host9103` finding there was no provisioned credential, no management surface the app exposed, and (then) no guest-exec.
- **Root cause:** The wording ("whatever administrative access your session already has") assumed a channel that may not exist.
- **Fix:** The prompt now states plainly that when no network-reachable remediation exists, `applied: false` + `needs_operator_decision: true` is the correct outcome.
- **Lesson:** "Can't be fixed from here; a human must decide" is a first-class result. This is the origin of the operator-decision axis now surfaced in reporting (see #21).

## 8. SSH transport instead of guest-exec

- **Type:** Bridge / API design (architecture)
- **Symptom:** guest-exec consistently returned "No QEMU guest agent configured."
- **Root cause:** `agent: 1` was absent from every golden image, so Proxmox never created the virtio-serial device the guest agent needs — a config gap, not an intentional limitation.
- **Fix:** Rather than change Proxmox config across every golden image, the sandbox's own SSH keypair was authorized for `root` (Linux) / `Administrator` (Windows) on every target; `range_host_audit.py` and the local-scope prompts switched to SSH, and `range_traversal.py` now threads `--target <ip>` for local-scope findings too. Host-key checking is disabled by design (disposable clones churn keys).
- **Lesson:** When a platform-config fix would have to touch every image, a host-level transport change can be the smaller, more maintainable lever — as long as the privilege model is unchanged (it was: still full root/Administrator).

## 9. A reverification must repeat the original method

- **Type:** PoC / remediation rigor
- **Symptom:** The reverify prompt was identical to a first attempt — no mention of the method/evidence that originally confirmed the finding, and no flag that the audit evidence shown predated the fix.
- **Root cause:** A reverify built that way can only show "I tried something and it failed," not "the specific thing that worked before no longer does" — a materially weaker claim.
- **Fix:** `range_poc.py assign --reverify` renders a `## This is a REVERIFICATION` banner carrying the original `poc_method` / `poc_evidence` and the `remediation_summary` (pulled from state), with an instruction to repeat that exact method and a caveat that other evidence may be stale.
- **Lesson:** "Closed" is only meaningful if the reverify re-runs the original proof. This is exactly what `reverified_closed` rests on (see #21).

## 10. Stale state is inherited silently; finding IDs collide across hosts

- **Type:** State integrity / autonomy safety
- **Symptom:** A trimmed 3-host run silently inherited an old 21-host state file's hosts. Separately, two unrelated hosts both showing `22/tcp/ssh` got the identical `finding_id` — one a benign modern OpenSSH, the other a confirmed root-key login.
- **Root cause:** `init` was additive-only (`setdefault` every field), so a smaller/differently-scoped run merged onto an un-cleared larger state with no signal. `finding_id` hashes only `(scope, port, proto, service)` or `(scope, category, command)` — never the host — so it collides across hosts by design.
- **Fix:** `init` now refuses (listing `stale_hosts`) when the existing state holds hosts outside the current run's set, unless `--fresh`; `--hosts` scopes a run to a subset. The hash scheme was left unchanged (revising it mid-sweep would destabilize IDs for a host's next rescan) but documented. The same collision later bit per-finding context files — `context_path()` was named only by finding_id, so `9102`'s nginx context was overwritten by `9103`'s Apache one — fixed by vmid-scoping that filename too (`.range_context_<vmid>_<finding_id>_<kind>.json`).
- **Lesson:** A cross-host ID match is never "the same finding." When an ID-collision bug appears in one artifact, sweep for *every* per-host artifact and scope all of them by vmid.

## 11. Prompts must close via the state-advancing wrapper

- **Type:** Prompt ↔ state contract
- **Symptom:** All three prompt templates told the agent to close out with the bare `range_poc.py report` / `range_remediate.py report`, which only writes a private log and never advances `range_traversal.py`'s tracked state — so `next`/`status` would treat a reported finding as unresolved forever.
- **Root cause:** The rendered "then call" instruction named the wrong command.
- **Fix:** Rewrote the closing instructions in all three templates to lead with the `range_traversal.py report-poc` / `report-remediate` wrapper (which calls the bare script internally, so validation/logging is unchanged), with the bare script documented only as the standalone-single-host exception.
- **Lesson:** A prompt's final "now call X" must point at the command that advances tracked state. A step that only logs locally is a silent dead end.

## 12. The agent never resolves its own review items

- **Type:** Autonomy safety
- **Symptom:** ~5 h into an unattended run, the standing root-publickey-SSH finding (the access channel the tooling itself depends on) appeared as `risk_accepted` with a `review_log` entry the agent wrote itself — no operator present. Its reasoning was sound and even deferred "final judgment" to the operator while resolving it anyway.
- **Root cause:** `SKILL.md` said "…then `resolve-review` once a human/operator has decided," which the agent couldn't distinguish from "I decided and I'm the human."
- **Fix:** `SKILL.md` + `range-sweep-init-prompt.txt` forbid the agent calling `resolve-review` under any confidence level; it reports `review_needed` / `done_pending_review` and leaves resolution to the operator.
- **Lesson:** A plausible, well-reasoned self-resolution is the hardest failure to catch after the fact. Keep the decision and the authority to record it on opposite sides of the human boundary.

## 13. A flag and its status must never disagree

- **Type:** State integrity / reporting
- **Symptom:** `resolve-review` cleared `needs_operator_decision` only on the `retry` branch, so a finding resolved `risk_accepted` / `closed_manually` stayed flagged — any filter on the flag alone would surface a terminal finding as if still pending, forever.
- **Root cause:** The flag-clear lived inside the `retry`-only branch.
- **Fix:** Moved it to apply unconditionally after `status` is set, since an operator decision is just as final whichever of the three resolutions it lands on.
- **Lesson:** When a boolean flag and a status can contradict, a consumer that filters on the flag will mislead. Keep them consistent at every transition.

## 14. A clean completion must not trigger a relaunch

- **Type:** Autonomy safety
- **Symptom:** `range-sweep-watchdog.sh`'s `while true` relaunched the sweep one second after it finished cleanly; attempt #2 hit a stale-state contradiction and wiped the finished tracker via `init --fresh`, destroying the live record of a completed sweep (the evidence files survived; the state file didn't).
- **Root cause:** The loop couldn't tell "crashed" from "finished" — any exit triggered a relaunch. And `--init` persists across every relaunch of the same watchdog process, so attempt #2 re-ran `init` instead of resuming.
- **Fix:** New read-only `range_traversal.py is-done` (same logic as `next`, but never claims an action or writes state; a `RANGE_WORKSPACE` override makes it callable from outside the sandbox). The watchdog calls it after each attempt and breaks the loop on `done` / `done_pending_review`. `--init`'s cross-relaunch persistence is now documented in its help text.
- **Lesson:** Gate a relaunch on an independent read of the work's own state, never on exit code. A destructive mode flag that silently persists across relaunches will eventually fire on good state.

## 15. Pipeline clones ahead of the agent; reset by snapshot; tear down only on completion

- **Type:** Clone & network reliability / throughput
- **Symptom:** A short assessment feeding a long (Windows) clone left the clone slot idle; a depth-1 pre-clone buffer still under-filled the pipeline (pool utilization peaked ~40%).
- **Root cause:** One clone built on demand can't overlap a long clone with a short assessment, and a single-deep buffer empties whenever the current host finishes fast.
- **Fix:** `range-preclone.py` keeps up to `--max-ready` (default 2) working clones ready ahead of the agent, capacity-gated. `proxmox_bridge.py` takes a disk-only "pristine" snapshot (stopped, never booted, golden MAC applied) usable as a fast whole-host reset. `range-sweep-watchdog.sh` runs teardown only on a confirmed graceful completion, never on Ctrl-C/kill.
- **Lesson:** Overlap clone time with assessment time by pipelining the next host(s); a pristine snapshot resets faster than a re-clone; destructive teardown belongs only to confirmed completion.

## 16. Existence is not exploitability; separate inventory from risk

- **Type:** PoC / remediation rigor
- **Symptom:** A host's `poc_confirmed` set was mostly inventory restated as findings (8 of 10 on one host), and re-confirming inventory dominated the slow PoC→remediate→reverify turns.
- **Root cause:** Every audited item entered the PoC cycle, and `reachable: true` wasn't held to a real impact demonstration.
- **Fix:** `range_host_audit.py` tags each finding `kind=risk|inventory` via one `RISK_CATEGORIES` set; `range_traversal.py` records inventory as `informational` (searchable, never auto-assigned a PoC) and risk as `discovered` (enters the PoC cycle), with an `escalate` command to promote one when needed. `reachable: true` now requires a demonstrated lower→higher privilege step (or data reached) through the finding — a service/port/account existing, or a CVE-matching banner, is `false`/`inconclusive`.
- **Lesson:** The noisiest, slowest part of a sweep is re-proving things that merely exist. Classify inventory out of the PoC cycle, and hold a confirmation to demonstrated impact.

## 17. Audit a host as one SSH session, not N probes

- **Type:** Clone & network reliability / PoC rigor
- **Symptom:** Windows hosts came back `audit_failed` ("Connection timed out during banner exchange") despite being reachable with no real findings; some PowerShell checks (`always_install_elevated`, `service_binary_paths`) returned mangled output.
- **Root cause:** The audit opened ~9 SSH connections per Windows host (1 probe + 8 checks), tripping Windows OpenSSH's anti-brute-force throttle. Separately, the double-quote wrapper corrupted any PowerShell script containing a literal `"`.
- **Fix:** `range_host_audit.py` — one connection per host via OpenSSH ControlMaster multiplexing; PowerShell sent base64 `-EncodedCommand` (quoting-proof); `$ProgressPreference` suppressed so CLIXML progress noise no longer merges into finding output.
- **Lesson:** To a target, a burst of short-lived connections looks like an attack. Treat a host's whole audit as one session, and pass structured remote commands as encoded bytes rather than fighting nested shell quoting.

## 18. Don't frame a safety budget as a quota

- **Type:** Prompt ↔ state contract / PoC rigor
- **Symptom:** A "one report per finding is normal / don't burn the budget" framing (added for speed) made the agent stop after a shallow first probe; a real client-side-auth web vuln on `host9103` became a false negative.
- **Root cause:** Presenting the 3-report cap as a quota discouraged pursuing a live lead.
- **Fix:** Reverted both PoC prompts to "match your persistence to the lead"; only the factual 3-report cap and 30 s cooldown remain.
- **Lesson:** A budget stated as a quota suppresses depth. Separate "stop re-confirming an inert finding" from "don't pursue a lead that's still open."

## 19. For a privesc PoC, adopt the account — don't authenticate as it

- **Type:** PoC / remediation rigor
- **Symptom:** The agent regressed to marking real privilege escalations `inconclusive`, citing a locked password, "no credential for that account," or "only an operator key can log in as it."
- **Root cause:** It treated the unprivileged account's locked password as blocking the PoC — the wrong test. Connected as root, it adopts any identity without authenticating, and a locked password does not block that account's own `NOPASSWD` sudo (sudo reads the sudoers policy, not `/etc/shadow`).
- **Fix:** `poc_task_prompt_local.md` gained an explicit block: adopt the account with `runuser -u <acct> --` / `su - <acct>` (no password or key needed); a locked password changes nothing; the PoC for a `NOPASSWD` sudoers finding on `X` is literally `runuser -u X -- sudo -n id` → `uid=0(root)`. Never record `inconclusive` on the grounds that an account's password is locked.
- **Lesson:** In this range the low-privilege foothold is assumed; the PoC demonstrates what it *escalates to*, operating from root and adopting the lower identity. "I hold no credential for that account" is never a valid `inconclusive` — that's not the test.

## 20. Inline the bridge contract; don't send the agent to read openapi

- **Type:** Bridge / API design / Prompt ↔ state contract
- **Symptom:** Every startup the agent read `/openapi.json` to learn the bridge — burning per-turn context — and there rediscovered `/vm/exec` and tried guest-exec, the path already abandoned in #8. Separately, a `/vm/clone` POST without a JSON content-type header returned 422.
- **Root cause:** `SKILL.md` pointed the agent at a discovery document instead of giving it the few endpoints it actually uses, and the clone example omitted `Content-Type: application/json`.
- **Fix:** `SKILL.md` now inlines the exact endpoints the agent needs (and prohibits guest-exec), adds `-H 'Content-Type: application/json'` to the clone example, and states "clone only the current host" (pre-clone owns the next).
- **Lesson:** Give an agent the minimal, exact API contract inline. A discovery doc wastes per-turn context and re-surfaces paths you've deliberately abandoned.

## 21. Report in the scope the work ran in; split "is there a fix?" from "whose call to deploy?"

- **Type:** Reporting & interpretation
- **Symptom:** `reverified_closed` read as "fixed in production," when the whole assess→remediate→reverify loop runs on a disposable clone. And `needs_operator_decision` — whether deploying a proven fix is a conscious operator call — was invisible in the funnel, surfaced only as a raw attempt count.
- **Root cause:** The status labels described the lifecycle without naming its scope (the clone), and the funnel collapsed two independent axes into one.
- **Fix:** All three reporting tools were relabeled to clone scope (`reverified_closed` = "fix proven effective on the clone, ready to deploy — not a patched live host"). `range-vuln-list.py` gained a `⚑` marker and `--needs-decision`; `range-vuln-show.py` gained a per-finding operator-decision callout; `range-status-report.py` gained a "Deployment decision — 2nd axis" cross-tab that crosses "is the fix proven?" with `needs_operator_decision` into three tiers (proven + operator-decision / not-yet-proven + operator-decision / proven + AI-judged-routine). `README_range-reporting.md` documents the clone model and both axes.
- **Lesson:** A status that reads like "fixed in production" but means "proven on a throwaway clone" invites a dangerous misread — name the scope precisely. And "there is a proven fix" and "deploying it is a human's call" are independent facts; report them on separate axes.
