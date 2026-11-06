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
2. Apply it yourself, using your own tools: a network-scope finding's
   fix typically runs on `{{host}}-ctf` (VMID `{{working_vmid}}`) over
   whatever administrative access your assessment session already has;
   a local-scope finding's fix runs via `proxmox_bridge.py`'s guest-exec
   channel (`POST /vm/{{working_vmid}}/exec`), the same channel the
   audit and PoC steps used. **Never target the golden image
   (VMID `{{golden_vmid}}`) or any other VMID.**
3. If the fix requires a judgment call the operator should make
   (e.g. a service they may need kept open, a breaking change), say so
   instead of applying it, and explain the trade-off.
4. **dry_run for this assignment is `{{dry_run}}`.** If that's `true`,
   propose the fix but do not apply it, and set `"applied": false` in
   your report regardless of how confident the proposal is.

## Reporting your result

Write your result as JSON to a file of your own choosing, e.g.
`/workspace/remediation_result_{{finding_id}}.json`:

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
the JSON file yourself is not the final step.
