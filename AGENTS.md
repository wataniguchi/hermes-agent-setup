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

**In a multi-step transformation chain, check whether you've already
arrived at the answer before assuming another step is needed.** This
has happened for real: a challenge involving many layers of encoding
was correctly unwrapped almost completely — each layer identified and
decoded correctly — but at the final layer, rather than simply checking
whether the decoded bytes were already readable plain text, the response
assumed more transformation must be needed (trying further encodings,
ciphers, padding adjustments) and eventually gave up despite having
likely already reached the answer. A puzzle's own theme or title (e.g.
implying "many layers") is not license to assume unbounded depth — at
every step, check the simplest interpretation (is this already
readable? does it already match the expected answer format?) before
committing to further transformation.

**If analysis reveals a reference to a live network endpoint (a URL, a
host:port, a captured exchange to continue or replay), actually attempt
the connection or replay before guessing or concluding you're stuck.**
This has happened for real: analyzing a downloaded packet capture
revealed it referenced a live host, and rather than actually trying to
connect or replay the captured exchange against it, the response went
straight to guessing candidate answers. Replaying or continuing a
captured network exchange against the real host is a well-established,
often-intended CTF technique — the static file is frequently deliberately
insufficient on its own, and the whole point is to actually engage the
live target with what you learned from it. A cached reachability
determination from earlier in a session (e.g. from session
initialization) is not necessarily still accurate the moment a specific
new technique makes reachability relevant again — when it actually
matters for a real next step, re-check live rather than trust a
possibly-stale earlier result.

**A cheap, directly available verification step is never optional —
this includes connectivity, not just technical derivation.** This has
happened for real: analysis of a downloaded file revealed it needed
access to a specific host/URL, and rather than simply attempting a real
connection to find out whether it's actually reachable right now, the
response went straight to guessing instead. Trying the connection costs
almost nothing and immediately resolves the uncertainty — guessing does
not. This applies even when a host was previously determined
unreachable earlier in the same session: that status can go stale over
time, and a specific new need to reach it is exactly the moment to
re-verify live, not to treat an old cached assumption as a permanent
fact to build further guessing on top of.

**Before concluding a problem is genuinely unsolvable, abstract its
actual technical requirement into general terms and search for that —
not just the specific problem's own name.** This is a distinct,
mandatory step beyond searching for a particular challenge's writeup
(already established as legitimate practice): even when no writeup
exists for the exact challenge in front of you, the underlying
technique it requires is very often a well-documented, named category
of attack with existing public research behind it. This has happened
for real: a challenge required recovering a PRNG's internal state from
its output alone, with the seed deliberately withheld — the actual
requirement is a well-known, named technique ("MT19937 state recovery
from output", "PRNG state reconstruction") with real prior research and
tooling — but the session concluded the problem was unsolvable without
ever restating what it actually needed in general, algorithm-level
terms and searching for that description specifically. Before reporting
a genuine dead end: state the core technical requirement in language
that would apply to *any* problem needing the same underlying technique,
not just this one, and search for that — this comes before concluding
anything is genuinely beyond reach, not after.

**Validate a file's integrity locally before handing it to a tool that
submits it directly to the model API.** Tools like `vision_analyze`
pass binary content straight into the next model request rather than
returning inspectable text — if that content is malformed, the failure
can surface as a hard API-level error on the *next* turn rather than a
normal, recoverable tool error. This has happened for real: a
challenge's decryption step produced a JPEG that passed a superficial
check (correct header, valid EXIF) but was corrupted in its compressed
body — `vision_analyze` itself succeeded, and the failure only
surfaced one turn later, aborting the session outright. Before calling
`vision_analyze` on any file you produced yourself (via decryption,
extraction, decoding, etc. — not files handed to you unmodified by the
platform), verify it decodes cleanly with a local, non-model check
first, e.g.:
    identify -verbose <path>        # reports "Corrupt JPEG data" etc.
or:
    python3 -c "from PIL import Image; Image.open('<path>').load()"
If validation fails, the artifact itself is wrong — treat this the
same as any other verification failure per the rules above: your
derivation has a bug, go find it, don't call `vision_analyze` on
suspect output and don't route around the check.

## Current CTF challenge scope

Active platform: ksnctf (https://ksnctf.sweetduet.info/)
Allowed attack target: ctfq.u1tramarine.blue — per the platform's own
posted policy, never attack anything outside this host for live-target
(web-app or SSH) challenges. Downloadable-file challenges have no such
restriction, since analysis happens locally, not against the platform's
own infrastructure.

When using `challenge-fetch` or any downstream attack tool, pass this
scope explicitly (e.g. `--scope ctfq.u1tramarine.blue`) — do not assume
any tool already knows it, and do not try to derive it from a challenge
page yourself. Update this section directly when moving to a different
platform (e.g. HTB) — no code changes should be needed anywhere else.
