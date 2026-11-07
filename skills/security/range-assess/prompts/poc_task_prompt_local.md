# PoC task prompt (local-scope findings) — returned by `range_poc.py assign`

Filled in by `range_poc.py assign` from the context file (built from a
`range_host_audit.py` finding) before being handed back to you, the
agent. No vulnerability technique or exploit code is written here —
figuring out whether this local condition is actually exploitable, and
demonstrating it for real, is your job, using your own real tools.

---

You are the exploitation step of an authorized, internal security
assessment. The operator owns and controls the entire environment you
are about to touch. This is not a public target and not a real
production system — it is an isolated Proxmox-based range built for
this purpose.

{{reverify_banner}}
## Scope — read this before doing anything

- **You act on the working clone `{{host}}-ctf` (VMID `{{working_vmid}}`,
  address `{{target}}`) only, via SSH as `root` (Linux) / `Administrator`
  (Windows) — never touch VMID `{{golden_vmid}}`** (the golden image).
  This is the same account `range_host_audit.py` used to find this
  condition in the first place.
- Finding under test (`{{finding_id}}`), category `{{category}}`: the
  audit command `{{command}}` produced the output below. This output
  was captured as `root`/`Administrator` over SSH — it shows you the
  condition exists, not that an unprivileged user has already exploited
  it. That's what you're here to determine.

  ```
  {{audit_output}}
  ```

  One-line summary: {{finding_description}}

## Task

Determine whether this specific local condition is genuinely exploitable
by a realistic lower-privilege actor on this host — not from theory,
from an actual attempt over SSH right now, in this turn. You're
connecting as `root`/`Administrator`, so where it makes sense for the
finding (e.g. a SUID binary, a cron job reading a world-writable file, a
NOPASSWD sudo rule), act it out as close to an unprivileged actor's own
position as you reasonably can from there — e.g. `su`/`sudo -u` down to
an unprivileged account first if the finding is specifically about what
that account could do, rather than just proving the condition exists
from the connection's own full-privilege vantage point. For example:
invoke the SUID binary and show it does something an unprivileged
caller shouldn't be able to do, write to the
world-writable file and show the privileged job later executed or
trusted your content, run the specific sudo command the rule grants and
show what it actually lets you do. Then:

1. If you confirm it: demonstrate with the **minimum action that proves
   real impact** — reading a marker/file you shouldn't be able to, or
   running a harmless command (`id`, `whoami`) in a context you
   shouldn't have reached, rather than anything destructive or
   persistent.
   - do **not** install a backdoor, persistence mechanism, or anything
     that outlives this assessment run,
   - do **not** affect anything outside the working clone `{{working_vmid}}`,
   - do **not** touch the golden image under any circumstance.
2. Stop as soon as you have clear evidence either way.
3. If nothing you try succeeds within the budget below, or the
   condition turns out to be benign on closer inspection (e.g. a
   "world-writable" path that's actually a dead end nothing privileged
   ever reads), report that honestly — **inconclusive** or **false**,
   not a guessed confirmation.

## Budget

- At most `{{max_attempts}}` distinct techniques.
- Stop and report if you exceed `{{time_budget_minutes}}` minutes.

## Reporting your result

For any step beyond a single one-line SSH command — a brute-force loop,
a decode/parse routine, anything you'd normally write as a script
rather than type at a prompt — **save the actual script to
`/workspace` and run it from there (or on the working clone over SSH,
as the finding requires)**, then list its real path in
`commands_run`, not a prose description of what it does. A human — or
another session — needs to be able to open that exact file and rerun
it; a sentence describing what a script would do is not independently
verifiable.

If your method derives a credential or other material **offline** and
you're reporting `reachable: true` on that basis, also write and run
a **second** script that uses it against the real local condition (not
a local reproduction of the check), and include that script's path and
its actual output in `commands_run`/`evidence` too.

Write your result as JSON to
`/workspace/result_{{working_vmid}}_{{finding_id}}.json` — use
`{{working_vmid}}`, not `{{golden_vmid}}`: the attempt log this feeds
into is keyed by the working vmid, so a result file named after the
golden vmid instead is hard to cross-reference against it later:

```json
{
  "reachable": true | false | "inconclusive",
  "method": "short description of what was attempted (what you ran over SSH, and why)",
  "evidence": "the concrete proof — command output, retrieved value, etc.",
  "commands_run": ["each SSH command actually run, in order"],
  "notes": "anything the operator should know (partial progress, dead ends, risk to flag for remediation)"
}
```

Then call:

```
python3 .../range_poc.py report \
    --golden-vmid {{golden_vmid}} --working-vmid {{working_vmid}} \
    --finding-id {{finding_id}} --result-file <path to the JSON file above>
```

That call is what actually validates and logs your result — writing
the JSON file yourself is not the final step. (It also saves a
canonical copy at that same `result_{{working_vmid}}_{{finding_id}}.json`
path regardless of what you named your own file, so following the
naming above just means there's nothing extra to reconcile.)
