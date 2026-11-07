# Remediation task prompt — returned by `range_remediate.py assign`

Filled in by `range_remediate.py assign` from a context file, only for
a finding whose PoC evidence is non-empty (i.e., actually confirmed —
the script itself refuses otherwise). This step is defensive —
proposing and applying a fix — so it carries fewer restrictions than
the PoC prompt, but still scopes you to the working clone only, and
still asks you to do the real work yourself (a package update or
config change via your own network-session access for a network-scope
finding; a `proxmox_bridge.py` guest-exec call for a local one) — not a
separate model call hidden inside any script.

---

You are the remediation step of an internal security assessment. A
prior step confirmed the following finding (`{{finding_id}}`, scope
`{{scope}}`) against the working clone `{{host}}-ctf`
(VMID `{{working_vmid}}`), never the golden image (VMID `{{golden_vmid}}`):

- What was confirmed: {{finding_description}}
- Target (network scope) or `n/a` (local scope): `{{target}}`
- Evidence it is exploitable: `{{poc_evidence}}`
- Method used to confirm it: `{{poc_method}}`

## Task

1. Identify the most direct fix for this specific finding — a package
   update, a configuration change, disabling an unnecessary service,
   tightening a firewall rule or sudoers/cron entry, fixing a
   permissions bit, rotating a weak credential, etc. Prefer the
   smallest change that actually closes the finding over a broad
   hardening pass.
2. Apply it yourself, using your own tools. For a network-scope
   finding, that means whatever *legitimate* administrative or
   management surface the application itself actually exposes over
   the network — a real admin API endpoint, a config-upload path, a
   service's own control interface. **There is no standing SSH or
   admin credential provisioned for these hosts beyond whatever the
   app offers on its own** — don't assume one exists just because a
   fix conceptually needs filesystem/config access; probe for it for
   real. For a local-scope finding, it's `proxmox_bridge.py`'s
   guest-exec channel (`POST /vm/{{working_vmid}}/exec`), the same
   channel the audit and PoC steps used — check whether it's actually
   available for this vmid rather than assuming either way. **Never
   target the golden image (VMID `{{golden_vmid}}`) or any other
   VMID.**
3. If, after actually checking (not assuming), neither a legitimate
   network-facing admin surface nor guest-exec is available for this
   finding, that is a genuine, expected outcome for some findings — not
   a failure on your part. Say so explicitly, set `"applied": false`,
   and report `"needs_operator_decision": true`: the fix needs either
   guest/console access this assessment doesn't have, or an
   application-level code change only the operator can make.
4. If the fix requires a judgment call the operator should make
   instead (e.g. a service they may need kept open, a breaking change),
   say so instead of applying it, and explain the trade-off.
5. **dry_run for this assignment is `{{dry_run}}`.** If that's `true`,
   propose the fix but do not apply it, and set `"applied": false` in
   your report regardless of how confident the proposal is.

## Reporting your result

For any applied fix beyond a single one-line command — a multi-step
config rewrite, a script that rotates all the affected credentials,
etc. — save the actual script/diff to `/workspace` and list its real
path in `commands_or_diff`, not a prose description of it.

Write your result as JSON to
`/workspace/remediation_result_{{working_vmid}}_{{finding_id}}.json` —
use `{{working_vmid}}`, not `{{golden_vmid}}`: the remediation log this
feeds into is keyed by the working vmid, so a result file named after
the golden vmid instead is hard to cross-reference against it later:

```json
{
  "remediation_summary": "one sentence describing the fix",
  "commands_or_diff": ["each command/config change actually run (or proposed, if dry_run), in order"],
  "applied": true | false,
  "risk_notes": "anything the operator should know before/after applying this",
  "needs_operator_decision": true | false
}
```

Then call:

```
python3 .../range_remediate.py report \
    --golden-vmid {{golden_vmid}} --working-vmid {{working_vmid}} \
    --finding-id {{finding_id}} --result-file <path to the JSON file above>
```

That call is what actually validates and logs your result — writing
the JSON file yourself is not the final step. (It also saves a
canonical copy at that same
`remediation_result_{{working_vmid}}_{{finding_id}}.json` path
regardless of what you named your own file, so following the naming
above just means there's nothing extra to reconcile.)
