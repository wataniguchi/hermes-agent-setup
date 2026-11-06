# PoC task prompt (network-scope findings) — returned by `range_poc.py assign`

This is a template, not a finished prompt: `range_poc.py assign` fills
in the `{{ }}` placeholders from a context file before handing this
text back to you, the agent. It intentionally contains no vulnerability
technique, payload, or exploit code — figuring that part out, and
actually attempting it, is your job, using your own real tools (shell,
network access) — not a separate model call hidden inside any script.

---

You are the exploitation step of an authorized, internal security
assessment. The operator owns and controls the entire environment you
are about to touch. This is not a public target and not a real
production system — it is an isolated Proxmox-based range built for
this purpose.

{{reverify_banner}}
## Scope — read this before doing anything

- **Target: `{{ipv4_or_ipv6}}` port `{{port}}/{{proto}}` only.** Do not
  scan, probe, or act against any other host or address, even if you
  discover one is reachable from here. If you believe testing a
  different host or port is necessary to make progress on this finding,
  stop and report that instead of doing it.
- **You are operating against the working clone `{{host}}-ctf`
  (VMID `{{working_vmid}}`), never the golden image `{{host}}-golden`
  (VMID `{{golden_vmid}}`).** The working clone is destroyed after this
  assessment run; treat it as disposable, but do not act against
  anything else.
- Finding under test (`{{finding_id}}`): service `{{service}}`,
  reported version `{{version}}`, discovered by `{{scan_method}}`.

## Task

Determine whether this service, as actually running on the target, is
exploitable **over the network** from your current position — not from
theory, not from a CVE ID matching the banner, but from an actual
attempt against the live service, using your own shell/network tools
right now, in this turn. Then:

1. If you find a working path: demonstrate it with the **minimum action
   that proves network reachability**, preferring non-destructive
   evidence over anything further:
   - reading a marker/canary value you can only reach via the
     vulnerability,
   - obtaining code execution and running a harmless command
     (`id`, `whoami`, `hostname`) to prove it, rather than anything
     destructive or persistent,
   - do **not** install a backdoor, persistence mechanism, or anything
     that outlives this assessment run,
   - do **not** pivot to another host or exfiltrate data beyond what is
     needed as evidence.
2. Stop as soon as you have clear evidence either way. Do not continue
   trying additional techniques once reachability is confirmed.
3. If nothing you try succeeds within the budget below, report
   **inconclusive** — do not guess, and do not report a finding as
   confirmed without having actually reproduced it against the live
   target this run.

## Budget

- At most `{{max_attempts}}` distinct techniques.
- Stop and report if you exceed `{{time_budget_minutes}}` minutes.

## Reporting your result

Write your result as JSON to a file of your own choosing, e.g.
`/workspace/result_{{finding_id}}.json`:

```json
{
  "reachable": true | false | "inconclusive",
  "method": "short description of what was attempted",
  "evidence": "the concrete proof — command output, retrieved value, etc.",
  "commands_run": ["each command or request actually executed, in order"],
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
the JSON file yourself is not the final step.
