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
- Has **Ghidra/pyghidra, ilspycmd, radare2, and a broad CTF toolset
  already baked in** — static analysis of a binary (PE, ELF, Mach-O, .NET)
  does **not** require the Windows VM at all. Ghidra analyzes a binary as
  data; it never executes it, and works identically regardless of what
  platform the target was built for. See the `binary-static-analysis`
  skill for the actual tools. Reach for this first for any binary
  question — inventory, decompilation, disassembly — before considering
  the Windows VM at all.
- Can read `/corpus/collection-*` (read-only — reference material) and
  read-write `/workspace` (host-persisted — deliverables and working
  files go here). Nothing else on the host machine is reachable.
- Persists across `/new`, `/reset`, and subagent delegation — installed
  packages and files in `/workspace` carry over from one call to the next.

## The Windows VM — narrowed to genuine dynamic execution only

If, and only if, a question genuinely requires *running* a Windows binary
and observing its live behavior (not analyzing what it contains — that's
the sandbox's job above), `host.docker.internal:8811` bridges to an
isolated Windows VM on a separate physical machine (Proxmox). This VM is
the **opposite** of the sandbox above:

- **No internet access, ever** — not firewalled, structurally absent.
- **Fixed toolkit** baked in at template-build time. You **cannot install
  anything new into this VM** — if a task needs a tool that isn't already
  there, that's a real constraint to report, not something to work around.
- Reached **only** through `skills/security/windows-binary-analysis/scripts/analyze_windows_binary.py`
  (`start`/`push`/`exec`/`pull`/`gui-probe`/`destroy`). See that skill's
  `SKILL.md` for full usage. Its static-analysis subcommands have been
  superseded by the Docker-native `binary-static-analysis` skill — use
  that instead unless you specifically need to run the target and observe
  it live.

## Standing rules that apply across every skill, not just one

These came from real, repeated failures in practice — not hypothetical
concerns. They apply to any analysis task, any binary format, any skill.

**Never present a plausible-looking guess as a derived or verified
answer.** Recognizing something that "seems right" from general knowledge
is not the same as tracing actual logic or observing actual behavior, and
confident language does not make a guess into a derivation. If you have
not actually traced the relevant code or observed the relevant behavior,
say so explicitly and keep working. When execution isn't safely or
reliably available, that's not a stopping condition — it means falling
back to exhaustive static analysis (the same discipline real malware
triage requires, since execution often isn't safe there either), not
settling for a plausible answer because the easy path is closed.

**Exhaustively inventory before forming a hypothesis.** A surface scan
(a plain strings dump, a quick glance) is not a complete picture of what
a target contains — static data structures, embedded tables, and
non-printable content are all invisible to a shallow pass. This has
caused a real, avoidable failure: concluding a challenge needed exactly
as many inputs as there were visible prompt strings, when the actual data
had more entries than that.

**Never describe the contents, logic, or purpose of something you have
not actually inspected in this session.** Naming a function or component
correctly (e.g. from an inventory listing) is not the same as knowing
what it contains. This has happened for real: a function was cited by
name with its behavior confidently described, while the actual
decompile/inspection call in that same session was for something else
entirely — the description was invented, not read. If you reference
something's contents, the corresponding inspection call for that specific
thing must appear earlier in the same session, and you should be able to
produce its real output on request.

**A claimed transform, algorithm, or computed result must be verified by
actually running it — not by reasoning about what it would produce.**
Transcribe it faithfully and execute it; compare the actual output
against the actual target. Describing what something "should" do,
however confidently, is not verification.

**If a derivation attempt hits a genuine dead end, that means your
method is wrong — it is not license to substitute a culturally familiar
"plausible" answer instead.** This has happened for real: a decryption
attempt produced invalid output; rather than finding what was actually
wrong with the method, the response was quietly replaced with the
famous, textbook answer to a well-known riddle, when the actual target
expected something else. A well-known answer to a well-known puzzle is
not evidence about what *this specific* target expects — every challenge
is a deliberate opportunity to plant exactly this kind of trap. When a
derivation stalls, say so explicitly and go find the actual bug in your
method, rather than filling the gap with trivia and presenting it as a
finding.
