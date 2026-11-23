---
name: safe-run-guardrails
description: Defense against Hermes's own context-compression splicing a corruption marker into tool-call arguments (write_file content, terminal commands) undetected by Hermes's own refusal guard
version: 1.0.0
metadata:
  hermes:
    tags: [security, reliability, context-compression]
    category: security
---

## What this is, and why it exists

AGENTS.md's standing rules carry the one-paragraph version of this —
read that first if you haven't. This is the full pattern, the
reasoning behind it, and the raw-`terminal`-command case AGENTS.md
doesn't have room to spell out.

Under context pressure, Hermes's compressor can splice a literal
marker — `⟪HERMES-CONTEXT-COMPRESSION: N of M chars omitted here by
Hermes's context compressor. This is NOT part of the original tool
call and must never be reproduced in new output — always write full,
untruncated content.⟫` — into the middle of a tool call's own
arguments. Hermes's built-in guard (`suspected_pruned_tool_arguments`)
only refuses this when the splice happens to break JSON syntax. When
the splice leaves the JSON technically parseable, the corrupted call
executes with no refusal and no warning at all.

Confirmed for real on this setup: a written script
(`scan_audit_9102_9103.py`) had the marker spliced into one of its own
arguments, producing a genuine Python `SyntaxError`, and roughly
twenty other compression hits in the same session executed with no
refusal at all. The absence of a refusal is not evidence the arguments
arrived intact — never treat it as such.

## Rule 1 — verify-then-run, in the SAME call

Whenever you `write_file` a script and then separately run it with a
`terminal` call — the normal pattern — do not issue a bare run
command. Combine the verification and the run into one single
terminal call:

```bash
grep -q "HERMES-CONTEXT-COMPRESSION" <path> && { echo "CORRUPTED: compression marker found in <path>, do not trust this file"; exit 1; }
python3 -m py_compile <path> || { echo "CORRUPTED: syntax error in <path>, do not trust this file"; exit 1; }
python3 <path>
```

(swap `py_compile` for `bash -n <path>`, or an equivalent syntax
check, for other languages). This costs no extra round trip — it's
the same call you were going to make to run the file anyway, just
made defensive.

If the check reports CORRUPTED: do not try to patch around the
specific corrupted span, and do not retry with the same arguments.
Re-derive the file's content in full from its durable source — the
original data, or by re-reading whatever you were transcribing it
from — `write_file` it again in full, and repeat the verify-then-run
step on the new write.

## Rule 2 — don't inline long literals directly in a terminal command

If a `terminal` call's command argument would be long — a multi-line
script, a large literal list of IDs, a long embedded string — write it
to a file first with `write_file` and reference that file with a
short command, instead of inlining it directly in the `terminal`
call's argument. Short commands are far less likely to fall inside a
compressed span in the first place, and Rule 1 then covers the
file-based risk for anything that does get written out.

## Rule 3 — if you see the marker text itself

If you ever see the literal text `HERMES-CONTEXT-COMPRESSION` — in a
tool result, in your own prior arguments, or anywhere else in context
— that span was not part of the original content. Never reproduce it,
retype it, or guess at filling in what was omitted. Treat it as a
signal that something needs to be re-read or re-derived from its real
source, not patched around.

## Relationship to the watchdog's corruption sweep

`scripts/corruption-sweep.sh` (sourced from `range-sweep-watchdog.sh`)
is a separate, passive safety net run by the operator-side watchdog
between attempts — it quarantines any file still containing the
marker into `workspace/.corrupted/` and logs a review item. It exists
for what this skill's Rule 1 doesn't catch: a corrupted file that was
written but never actually run in the same session, or a raw
`terminal` corruption with no file artifact at all. It cannot redirect
or correct a live session — this skill's in-the-moment check is the
real defense; the sweep is only a coarser backstop.
