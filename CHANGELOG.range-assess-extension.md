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
