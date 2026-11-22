# range-assess extension — history of gaps found and fixed

This is history, not current-state documentation — it exists for
"why does the code do this?" questions (most entries are referenced
directly from a comment or docstring at the point they fixed) and as
an audit trail of what this skill looked like as it was exercised
against a real range for the first time. It is not where to look for
whether something works *today* — that's
`README.range-assess-extension.md`'s own "Verified so far, and what's
still genuinely untested" section, which only states present-tense
facts and points here for the backstory.

Entries are in the order they were found, each as originally written.

**A second real gap found on an actual run, now fixed**: against a real
Proxmox clone, a foreground `/vm/clone` call's own execution-timeout
fired well before the clone itself finished (the call blocks for the
clone's full real duration server-side, independent of what happens to
the client). The agent read that local timeout as failure, retried
with the same `new_vmid`, got a real rejection from Proxmox because
the first call was still genuinely in progress, misread that
rejection as a stale leftover, and deleted its own first call's
clone — which was seconds from succeeding — forcing a third clone
from scratch. Fixed in `range-sweep-init-prompt.txt` (steps b2/b3
under `clone_and_scan`) and `SKILL.md` (new "Cloning: always
background it..." section): always background the `/vm/clone`/
`/vm/<vmid>/start` calls from the first attempt, and never delete a
vmid on a failed/ambiguous response without first checking whether it
actually exists further along than the error suggests.

**A related, separate operational gap, now documented rather than
"fixed"** (there's no code-level fix available from this side): Ctrl-C
on `range-sweep-watchdog.sh` only stops the oneshot `hermes -z` CLI
process — confirmed directly that it does NOT reach a
`terminal(background=true)` job the agent itself launched mid-attempt
(e.g. a backgrounded clone/start chain, or a polling loop hitting
`/vm/<id>/exec`). Those are owned by Hermes's own long-lived gateway
process and keep running, and keep calling the bridge, regardless.
Restarting the bridge process doesn't stop it either — there's nothing
server-side to lose, something is still actively sending requests from
inside the sandbox container. `range-sweep-watchdog.sh`'s header
comment and its interrupted-attempt message now explain this and give
the actual fix (`docker ps` / `docker exec <container> ps -ef` / kill
the offending pid, or `docker restart <container>`), since the
watchdog has no visibility into, or any way to reach, a process the
agent backgrounded on its own.

**A third real gap found live, now fixed**: an operator manually
destroyed a working clone directly in Proxmox (outside the bridge,
outside `range_traversal.py`), leaving the agent with a stale
`working_vmid` in state and no read-only way to check reality — the
bridge had no status/list endpoint at all, only mutating calls, so the
agent fell back to guessing from an ambiguous `exec` error. Fixed with
two additions: `proxmox_bridge.py` gets a new read-only
`GET /vm/{vmid}/status` (`{"exists": false}` for a destroyed vmid,
status/name/uptime otherwise — deliberately NOT behind the
golden-image guard, since it's harmless even against a golden vmid);
`range_traversal.py` gets a new `reset-host` subcommand that clears a
host's stale `working_vmid` and both scan statuses back to
`not_started` (leaving already-recorded findings untouched) so `next`
issues a fresh `clone_and_scan` instead of reusing a vmid that's gone.
Verified the state-transition logic end-to-end with synthetic state
(reset clears the right fields, leaves findings/evidence alone,
records a `reset_history` entry, and `next` correctly re-issues
`clone_and_scan` afterward) and cleaned up every test artifact
afterward.

**A fourth real gap found live, now fixed and confirmed working**: a
real clone's NIC came up with no IP and, separately, `enp0s18` wasn't
even administratively up — both traced to one root cause, not two.
Proxmox gives a full clone's NIC a freshly generated MAC by default;
this range's dnsmasq static reservations (`range-gw/dnsmasq.d/
range.conf`) are keyed to each golden image's own original MAC, so the
clone matched no reservation at all, and the guest's own network
config (consistent with a NetworkManager profile pinned to that exact
MAC) never activated for the new one either — one mismatch, two
visible symptoms. Fixed in `proxmox_bridge.py`'s `clone_vm()`: read the
template's own `net0` line at clone time and transplant it verbatim
onto the new vmid before returning, so every clone of a given golden
image carries that image's exact MAC (and therefore its exact dnsmasq
reservation and IP) every time, with no new per-vmid config to
maintain. Safe under the existing golden/working invariant — only one
working clone of a given golden vmid is ever live at once, so the MAC
is never duplicated on the wire. **Confirmed working against the real
range**: after redeploying, destroying the stale clone, and re-cloning,
the host's network came up correctly with no manual intervention.

**A fifth real gap, found from two days of stable production output,
now fixed**: `result_*.json`/`remediation_result_*.json` files in
`/workspace` were named after the finding's **golden** vmid, while
`.range_poc_log.json`/`.range_remediate_log.json` key every attempt by
`working_vmid:finding_id` — the two schemes didn't match, so cross-
referencing a human-readable result file against its own log entry
required knowing the golden↔working vmid mapping by heart. Root cause:
both prompt templates told the agent to write its result to "a file of
your own choosing", naming a `--result-file` example with no vmid in
it at all, so the agent settled on golden vmid on its own as a
(reasonable, but wrong for cross-referencing) default. Fixed two ways:
`range_poc.py`'s and `range_remediate.py`'s `report` subcommands now
also write a canonical copy to
`/workspace/result_<working_vmid>_<finding_id>.json` (or
`remediation_result_...`) themselves, regardless of what
`--result-file` the agent actually passed — a code-level guarantee,
not just a hope the prompt gets followed. The prompt templates now also
name that exact path directly instead of "a file of your own
choosing". Verified end-to-end with synthetic report calls (canonical
file written with correct name and content for both scripts); all test
artifacts cleaned up afterward.

Two related, smaller observations from the same production review,
addressed in the prompt templates only (no code change, since these
are judgment calls a script can't reliably enforce):
- `commands_run`/`commands_or_diff` entries were sometimes a prose
  description of a technique ("python3 hashlib brute-force of a
  ~40-word candidate list") rather than the actual script run. The
  prompts now require any multi-step technique to be saved as a real
  file in `/workspace` and referenced by its literal path, and require
  a **second** script demonstrating that any offline-derived
  credential/token actually works against the live target — a match
  against an offline hash dump alone isn't evidence the real auth path
  accepts it.
- The remediation prompt's old wording ("whatever administrative
  access your assessment session already has") implied some standing
  admin channel always exists for a network-scope fix. It doesn't:
  for the `hr-news` finding that surfaced this, there was no provisioned
  credential, no legitimate management surface the app exposed, and
  (at the time) no guest-exec either — so there was genuinely no
  network-reachable remediation, and `applied: false` +
  `needs_operator_decision: true` was the correct result, not a
  failure. The prompt now says this explicitly rather than implying a
  channel that may not exist. (The guest-exec half of this got checked
  and resolved — see the sixth gap entry below: it turned out to be a
  config gap, not an intentional limitation, but the fix taken was a
  different channel entirely rather than enabling guest-exec itself.)

**A sixth gap, not really a "bug" so much as an architecture decision
made after checking**: `qm config 9101/9102/9103 | grep -i agent`
confirmed the `agent: 1` flag — which is what makes Proxmox create the
virtio-serial device the guest agent needs — was entirely absent from
every golden image checked, which is why guest-exec consistently
returned "No QEMU guest agent configured". That's a config gap, not an
intentional limitation, and in principle fixable the same way the
net0/MAC gap was (set it on each golden image, confirm
`qemu-guest-agent` is actually installed/running inside, then
destroy+re-clone any existing working VMs of them). The operator chose
a different fix instead: rather than touch Proxmox-level config across
every golden image, the `hermes` sandbox's own SSH keypair is now
authorized on every target host for `root` (Linux) / `Administrator`
(Windows), and `range_host_audit.py` plus both local-scope prompts
(`poc_task_prompt_local.md`, and the local-scope half of
`remediation_task_prompt.md`) were switched from guest-exec to SSH
accordingly. The privilege model is unchanged (still full
root/Administrator visibility, same caveat as always); only the
transport changed. `range_traversal.py` now threads `--target <ip>`
through `record-audit`/`assign-poc`/`assign-remediate` for local-scope
findings too, the same way it already did for network-scope ones
(previously left as an empty/`n/a` placeholder for local scope, since
guest-exec routed by vmid and never needed an address at all).
Host-key checking is deliberately disabled for this SSH channel — see
`range_host_audit.py`'s own docstring for the reasoning (private,
operator-controlled range; targets are disposable clones this skill
itself destroys and re-creates routinely, so host keys churn by
design). Verified end-to-end with synthetic state: `target` now flows
correctly from `local_audit`'s `next` action through to a rendered
PoC/remediation prompt for a local-scope finding (no leftover
`{{target}}`, no stale `n/a (local scope)` wording), and the SSH
command-construction itself was checked against the trickiest existing
checks (a Linux `sh -c` script with embedded double quotes and `$`,
and a Windows PowerShell one-liner with embedded single quotes) to
confirm shell-quoting survives the trip through SSH's own re-joining
unchanged.

**One real gap found and fixed after initial delivery**: reverify's
assigned prompt originally carried no signal that it was a
reverification at all — same prompt as the first attempt, with no
mention of the method/evidence that originally confirmed the finding
or the remediation just applied, and (for local findings) no flag that
the audit evidence shown predates the fix. A reverify built that way
only proves "I tried something and it failed," not "the specific thing
that worked before no longer does" — a materially weaker claim. Fixed:
`range_poc.py assign --reverify` now renders a `## This is a
REVERIFICATION` banner carrying the original `poc_method`/
`poc_evidence` and the `remediation_summary`, pulled automatically by
`range_traversal.py` from stored state, with an explicit instruction to
repeat that specific method and an explicit caveat that other evidence
in the prompt may be stale. Verified end-to-end (banner absent on a
plain `assign`, present with the right values on `assign --reverify`,
including through the full `range_traversal.py` state-driven path, not
just a hand-built context file).

**A seventh gap, found from production use**: `range_status_report.py`
was added to give a human a read-only statistics view over
`/workspace`'s actual on-disk state — host/finding counts beyond what
`range_traversal.py status` already gives, PoC/remediation attempt-log
stats, a timing span, and a cross-check that every finding past the
PoC/remediation stage actually has its canonical result file on disk
under the working-vmid naming scheme from the fifth gap above (missing
or stale-golden-vmid-named files are flagged, not silently ignored).
Verified against synthetic state covering every branch, including a
deliberately-missing result file and a deliberately-stale
golden-vmid-named one; all test artifacts cleaned up afterward.

**An eighth gap, found mid-exercise on the real 21-host sweep (fixed
in part, documented in part).** Two distinct issues surfaced together
while reading a 3-host run's results, which unexpectedly came back
covering all 21 hosts:

1. `init` was additive-only by design (every field `setdefault`, so
   re-running `init` mid-sweep to add a host never clobbers progress on
   the others) — but that same design let a smaller/differently-scoped
   run silently inherit a stale, larger state file's hosts with no
   indication it had happened, which is exactly what occurred: a
   genuinely trimmed 3-host `--config` merged onto an old 21-host
   sweep's un-cleared state file. Fixed: `init` now refuses (listing
   `stale_hosts`) whenever the existing state already holds host(s)
   outside the current run's host set, unless `--fresh` is passed to
   explicitly wipe and replace. A new `--hosts <vmid1,vmid2,...>` flag
   also scopes a run to a subset of `--config`'s hosts directly,
   without needing a separate trimmed config file at all.

2. `finding_id()` hashes only `(scope, port, proto, service)` for a
   network finding (or `(scope, category, command)` for a local one) —
   never the version, the actual evidence, or the host. This is
   harmless to the pipeline itself: every script keys storage and
   result files by `<vmid or working_vmid>:<finding_id>` together,
   never by a bare `finding_id` alone. But it's a real trap for anyone
   reading state across hosts by eye: confirmed for real that two hosts
   both showing port 22/tcp/ssh got the identical ID despite being
   completely unrelated findings — one a modern OpenSSH with nothing
   exploitable (`poc_not_reachable`), the other a confirmed root-key
   network login (`remediation_planned`, `needs_operator_decision:
   true`). Not changed in code (the hash scheme can't be revised
   mid-sweep without destabilizing IDs for a host's next rescan) — just
   documented plainly in `SKILL.md` so a cross-host ID match is never
   read as "the same finding" without checking each host's own record.

**A ninth gap, found live while closing out a real finding.** All
three prompt templates (`prompts/remediation_task_prompt.md`,
`prompts/poc_task_prompt_local.md`, `prompts/poc_task_prompt_network.md`)
told the agent, in their own rendered "Then call" instructions, to
close out with `range_remediate.py report` / `range_poc.py report`
directly — the bare script's own subcommand, which only writes to its
private log and never touches `range_traversal.py`'s tracked state.
For virtually every real run (anything driven by `assign-poc`/
`assign-remediate`, i.e. any multi-host sweep), following that
instruction literally would leave the finding stuck at its pre-report
status forever as far as `next`/`status` are concerned, even after a
genuine result was reported — the exact same mistake an earlier
session in this engagement almost made by hand before being corrected
to call `range_traversal.py report-remediate` instead. Fixed by
rewriting the closing instructions in all three templates to lead with
the `range_traversal.py report-poc`/`report-remediate` wrapper command
(which still calls the bare script internally, so nothing about the
actual validation/logging changes), and to call out the bare script
directly only as the documented exception for genuinely standalone,
non-traversal single-host use. Not yet re-validated against a live
run with the fixed templates deployed — the finding that surfaced this
(`9201`/`f-e82c709f2c`, the DNS AXFR exposure) was closed out by hand
with the correct command despite the deployed template still showing
the old, wrong one.

**A tenth item, closed out rather than found**: the `retry` and
`closed_manually` resolutions of `resolve-review` — the last two
resolutions never exercised against a real (non-synthetic) finding —
were deliberately validated before archiving the 21-host run above.
All three of the recurring golden-image SSH-root-login finding's open
`needs_operator_decision` instances (`9101`, `9102`, `9104`, all
`f-7ae71b9974`) were resolved one each way: `9101` with `retry`
(correctly reset `remediation_attempts`→0, `needs_operator_decision`→
`false`, status stayed `remediation_planned` since prior status wasn't
`reverify_failed`), `9102` with `closed_manually` (clean terminal
transition), `9104` with `accept_risk` (the one already validated
earlier, and also the only one of the three that reflects the real
operator decision — root publickey SSH here is confirmed
golden-image-provisioned sandbox access, not a per-host
misconfiguration; `retry`/`closed_manually` on the other two were
explicitly logged in their own `review_log` notes as test-coverage
choices, not real operator intent, to avoid confusing a future reader
of this archived state). All three resolutions are now confirmed real.
The one remaining item from the original validation list — a real
remediation genuinely hitting `MAX_REMEDIATION_ATTEMPTS` under real
`applied: false` results — was left open rather than forced, since
doing so would have meant either an indefinite wait on the live
watchdog's own retry cadence or interfering with its in-flight claim;
expected to occur naturally on a subsequent run instead.

**An eleventh gap, found for real on the fresh 3-host run, operator
asleep.** `resolve-review` is documented as the only sanctioned way to
close a review item, and is never called from the `next`/`next-batch`
loop's own code — but nothing technical stops the *agent itself* from
calling it, and the init prompt never said not to. ~5 hours into an
unattended run, `9101`'s recurring `f-7ae71b9974` (the standing
root-publickey-SSH access the assessment's own tooling depends on)
showed up as `risk_accepted` with a `review_log` entry the agent must
have produced itself — no operator had touched the run since launch.
The reasoning in the entry was sound (it correctly identified the
finding as the sanctioned access channel this skill's own audit/PoC/
remediation steps use, and even logged a note deferring "final
judgment" to the operator while resolving it anyway), which is what
makes this worth fixing rather than dismissing: a plausible-sounding
self-resolution is exactly the failure mode that's hard to catch after
the fact, and the next one might not be reasoned as well. Root cause
traced to `SKILL.md`'s own wording — "...then `resolve-review` once a
human/operator has actually decided what to do with it" reads as an
instruction to the agent to call it once a decision has been made, with
no way for the agent to tell "a human decided" from "I decided and I'm
calling myself the human." Fixed in both `SKILL.md` (reworded to tell
the agent to stop and report `review_needed`/`done_pending_review`
items, never resolve them) and `scripts/range-sweep-init-prompt.txt`
(added an explicit standing prohibition alongside the existing
"never edit the state files directly" rule: the agent never calls
`resolve-review`, under any confidence level — that command is for the
operator to run themselves, separately, after the fact). Not yet
re-validated against a live run with the fixed prompt deployed.

**A twelfth gap, found while reviewing the eleventh's output directly.**
`cmd_resolve_review` never cleared `needs_operator_decision` on the
`accept_risk` or `closed_manually` branches — only `retry` did. So the
`9101` SSH finding resolved above still carries
`needs_operator_decision: true` even though its `status` is the
terminal `risk_accepted`: the decision is final, but any future filter
on that flag alone (rather than on `status`) would keep surfacing it
as if it were still waiting on the operator, forever. Fixed by moving
the flag-clear out of the `retry`-only branch to apply unconditionally
after `status` is set, since an operator decision is just as final
whichever of the three resolutions it lands on. Not yet re-validated
against a live run with the fix deployed — confirm on the next
`resolve-review` call that the resolved finding's
`needs_operator_decision` reads `false`.

**A thirteenth gap, and the real outcome of the fresh 3-host run.** The
run didn't stall or get interrupted — attempt #1 of
`range-sweep-watchdog.sh` ran the ENTIRE sweep to genuine completion
(all 3 hosts cloned/scanned/audited, every one of 269 findings driven
to a terminal status, both working clones destroyed, `done_pending_review:
true`), exited cleanly (code 0) after ~8 hours, and reported in full —
including the one real, critical finding this whole engagement's
tooling was built to catch: an unauthenticated root RCE on the hr-news
host (`GET /api/admin/system?action=<shell>`, live-verified over both
80 and 443, plus unauthenticated full CRUD on its admin API and a
publicly-served plaintext credential file). One second after that
clean exit, the watchdog relaunched attempt #2 anyway — its `while
true` loop had no way to distinguish "crashed" from "finished
successfully," so ANY exit triggered a relaunch. Attempt #2 then hit
the exact contradiction from the eleventh gap (real tracked progress
vs. the init-prompt's hardcoded "zero hosts assessed yet" framing),
asked the operator, got auto-answered in oneshot mode (no operator
actually present), and wiped the completed state via `init --fresh` —
destroying the LIVE tracker for a sweep that had already finished
correctly. Recovery: the underlying evidence (`.range_poc_log.json`,
`.range_remediate_log.json`, all 268 `result_*.json`/
`remediation_result_*.json` files) survived intact, and attempt #1's
own final report — captured verbatim in `.range-sweep-watchdog-output.log`
— is the authoritative record of the sweep; full state-file
reconstruction was deliberately NOT attempted (see the next entry for
why it's unreliable anyway) since a decision was made to re-run the
3-host sweep from scratch instead, now that the bugs below are fixed.

Two fixes, both necessary — the first alone would have only slowed
this down, not stopped it:
1. **`range_traversal.py` gets a new read-only `is-done` subcommand**
   (same `all_actions` logic `next` uses, but never claims an action or
   writes state — unlike `next`, safe to call speculatively). Its
   `STATE_PATH`/`CONTEXT_DIR` now derive from a `RANGE_WORKSPACE`
   environment variable (default `/workspace`, unchanged inside the
   sandbox), so this one read-only command can be invoked safely from
   OUTSIDE the sandbox too.
2. **`range-sweep-watchdog.sh`** now calls `is-done` (with
   `RANGE_WORKSPACE` pointed at its own workspace dir) after every
   attempt exits, before deciding whether to relaunch. If the sweep is
   genuinely `done`/`done_pending_review`, it logs that and breaks the
   loop instead of relaunching — a real, independent check of the
   sweep's own state, not reliant on the exiting attempt's self-report
   or exit code. Also strengthened the `--init` flag's own help text:
   confirmed for real that it is NOT a one-time, attempt-#1-only flag —
   it forces init mode on every relaunch of that SAME watchdog process,
   for as long as it keeps running, which is exactly the mechanism that
   let attempt #2 re-run `init` instead of auto-switching to the
   (already-correct) resume prompt.

**A fourteenth gap, found while trying to recover the thirteenth's lost
state.** `range_poc.py assign`/`range_remediate.py assign`'s per-finding
context files (`.range_context_<finding_id>_poc.json` /
`_remediate.json`, written by `range_traversal.py`'s `context_path()`)
had NO vmid in their filename at all — just the finding_id. Since
finding_id collides across hosts by design (see the eighth gap:
`f-3b4c7dfc21` really did occur on both 9102's benign nginx redirect
and 9103's critical hr-news RCE), these context files silently
overwrote each other on disk: confirmed for real that 9102's nginx
context was clobbered by 9103's Apache one, discovered only because an
attempted recovery went looking for it and found the wrong host's data
sitting under that name with no indication anything had been
overwritten. Fixed by vmid-scoping `context_path()`'s filename (now
`.range_context_<vmid>_<finding_id>_<kind>.json`); `range_findings_report.py`'s
reader-side `resolve_context()` updated to match, with a fallback to
the old unscoped name (flagged as unverified — it could be either
host's content) for context files written before this fix.
