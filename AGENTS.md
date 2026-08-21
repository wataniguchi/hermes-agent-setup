# Environment orientation

This file is auto-loaded into every session's context at startup — read
it once, it applies regardless of what task you're given. Full detail
lives in [README.md](README.md#architecture-at-a-glance) and
[README_Proxmox.md](README_Proxmox.md); this is the short version.

## Where you're running

You (primary or subagent — subagents share this same container, just
with a fresh conversation) operate inside **one persistent Docker
sandbox container** on a Mac Studio. This container:

- Has **real internet access** — `web_search`/`web_fetch`, and you can
  `pip install`/`apt install`/`npm install` anything a task needs. Nothing
  here is fixed or pre-approved.
- Can read `/corpus/collection-*` (read-only — reference material) and
  read-write `/workspace` (host-persisted — deliverables and working
  files go here). Nothing else on the host machine is reachable.
- Persists across `/new`, `/reset`, and subagent delegation — installed
  packages and files in `/workspace` carry over from one call to the next.

## The Windows analysis environment is a completely different regime

If a task involves a Windows binary, `host.docker.internal:8811` bridges
to an isolated Windows VM on a separate physical machine (Proxmox). This
VM is the **opposite** of the sandbox above:

- **No internet access, ever** — not firewalled, structurally absent.
- **Fixed toolkit** (Ghidra, x64dbg, Sysinternals, Python, .NET SDK,
  `ilspycmd`) baked in at template-build time. You **cannot install
  anything new into this VM** — if a task needs a tool that isn't already
  there, that's a real constraint to report, not something to work around.
- Reached **only** through `skills/security/windows-binary-analysis/scripts/analyze_windows_binary.py`
  (`start`/`push`/`exec`/`pull`/`decompile`/`gui-probe`/`destroy`). See
  that skill's `SKILL.md` for full usage.

## A standing rule that applies across every skill, not just one

**Never present a plausible-looking guess as a derived or verified
answer.** This has happened repeatedly in practice — recognizing
something that "seems right" from general knowledge is not the same as
tracing actual logic or observing actual behavior, and confident language
does not make a guess into a derivation. If you have not actually traced
the relevant code or observed the relevant behavior, say so explicitly
and keep working, rather than submitting a guess as final. When execution
isn't safely or reliably available, that's not a stopping condition — it
means falling back to exhaustive static analysis (the same discipline
real malware triage requires, since execution often isn't safe there
either), not settling for a plausible answer because the easy path is
closed.
