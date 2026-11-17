# Environment orientation

This file is auto-loaded into every session's context at startup — read
it once, it applies regardless of what task you're given.

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
- Has `nikto`, `sqlmap`, `dirb`, `zap` (OWASP ZAP), and
  `hydra`/`john`/`hashcat`/`fcrackzip` already on `$PATH` for web-app
  and credential-attack work — real binaries, not `pip install`;
  reach for the one that fits rather than hand-rolling it.
- Can read `/corpus/collection-*` (read-only — reference material) and
  read-write `/workspace` (host-persisted — deliverables and working
  files go here). Nothing else on the host machine is reachable.
- Persists across `/new`, `/reset`, and subagent delegation — installed
  packages and files in `/workspace` carry over from one call to the next.

## The Windows VM — narrowed to genuine dynamic execution only

If, and only if, a question genuinely requires *running* a Windows
binary and observing its live behavior (not analyzing its contents —
that's the sandbox's job above), `host.docker.internal:8811` bridges to
an isolated Windows VM on a separate Proxmox machine. This VM is the
**opposite** of the sandbox above:

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

These came from real, repeated failures — not hypothetical concerns —
and apply to any analysis task, any binary format, any skill.

**Never present a plausible-looking guess as a derived or verified
answer.** Recognizing something that "seems right" from general
knowledge is not the same as tracing actual logic or observing actual
behavior, and confident language does not make a guess into a
derivation. If you have not actually traced the relevant code or
observed the relevant behavior, say so explicitly and keep working.
When execution isn't safely or reliably available, that's not a
stopping condition — fall back to exhaustive static analysis (the same
discipline real malware triage requires, since execution often isn't
safe there either), not a plausible answer because the easy path is
closed.

**Exhaustively inventory before forming a hypothesis.** A surface scan
(a plain strings dump, a quick glance) is not a complete picture of what
a target contains — static data structures, embedded tables, and
non-printable content are all invisible to a shallow pass. This caused
a real, avoidable failure: concluding a challenge needed exactly as
many inputs as there were visible prompt strings, when the actual data
had more entries than that.

**Never describe the contents, logic, or purpose of something you have
not actually inspected in this session.** Naming a function or component
correctly (e.g. from an inventory listing) is not the same as knowing
what it contains. This happened for real: a function was cited by name
with its behavior confidently described, while the actual
decompile/inspection call in that same session was for something else
entirely — the description was invented, not read. If you reference
something's contents, the corresponding inspection call for that
specific thing must appear earlier in the same session, and you should
be able to produce its real output on request.

**A claimed transform, algorithm, or computed result must be verified by
actually running it — not by reasoning about what it would produce.**
Transcribe it faithfully and execute it; compare the actual output
against the actual target. Describing what something "should" do,
however confidently, is not verification.

**If a derivation attempt hits a genuine dead end, that means your
method is wrong — it is not license to substitute a culturally familiar
"plausible" answer instead.** This happened for real: a decryption
attempt produced invalid output, and rather than finding what was
actually wrong with the method, the response was quietly replaced with
the famous textbook answer to a well-known riddle, when the actual
target expected something else. A well-known answer to a well-known
puzzle is not evidence about what *this specific* target expects —
every challenge is a deliberate opportunity to plant exactly this kind
of trap. When a derivation stalls, say so explicitly and go find the
actual bug in your method, rather than filling the gap with trivia and
presenting it as a finding.

**In a multi-step transformation chain, check whether you've already
arrived at the answer before assuming another step is needed.** This
happened for real: a challenge with many layers of encoding was
correctly unwrapped almost completely, but at the final layer, rather
than checking whether the decoded bytes were already readable plain
text, the response assumed more transformation must be needed (further
encodings, ciphers, padding adjustments) and gave up despite having
likely already reached the answer. A puzzle's own theme (e.g. implying
"many layers") is not license to assume unbounded depth — at every
step, check the simplest interpretation before committing to further
transformation.

**A cheap, directly available verification step is never optional —
including connectivity to a live endpoint (a URL, a host:port, a
captured exchange to continue or replay), not just technical
derivation. Attempt it before guessing or concluding you're stuck.**
This happened for real, twice: a downloaded packet capture referenced
a live host, and rather than trying to connect or replay it, the
response went straight to guessing; separately, a downloaded file
needed access to a specific host/URL, and again the response guessed
instead of trying the connection. Replaying a captured exchange
against a real host is a well-established, often-intended CTF
technique — the static file is frequently deliberately insufficient
alone. Trying costs almost nothing and resolves the uncertainty
outright. This applies even when a host was previously determined
unreachable earlier in the session — that status goes stale, and a new
need to reach it is exactly the moment to re-verify live, not reuse an
old cached result.

**Before concluding a problem is genuinely unsolvable, abstract its
actual technical requirement into general terms and search for that —
not just the specific problem's own name — and this must actually
happen as a real tool call, not stay a principle held "in the
abstract" and skipped once a problem starts feeling hard.** This is a
distinct, mandatory step beyond searching for a particular challenge's
writeup (already established as legitimate practice): even when no
writeup exists for the exact challenge in front of you, the underlying
technique it requires is very often a well-documented, named category
of attack with existing public research behind it. This has happened
for real, more than once: a challenge required recovering a PRNG's
internal state from its output alone, with the seed deliberately
withheld — the actual requirement is a well-known, named technique
("MT19937 state recovery from output", "PRNG state reconstruction")
with real prior research and tooling, but the session concluded the
problem was unsolvable without ever restating what it actually needed
in general, algorithm-level terms and searching for that. Separately,
a problem was marked given-up-on with a progress note reading only
"Stalled," and another was abandoned mid-derivation with active,
unresolved work still described in its own notes — neither showed any
evidence this search was actually attempted at the moment the problem
started looking hard. Before reporting a genuine dead end: state the
core technical requirement in language that would apply to *any*
problem needing the same underlying technique, not just this one, and
search for that — this comes before concluding anything is genuinely
beyond reach, not after.

**Before combining two independently-fallible processing stages, prove
each one correct in isolation, against arbitrary data and an
authoritative external reference — not just against itself.** A
combined pipeline's aggregate pass/fail result can't tell you which
stage is actually broken, or whether the fault is in how they're
connected rather than in either stage — collapsing several
independently-checkable questions into one signal destroys the
information needed to fix anything. This happened for real: a decoder
was verified extensively via round-trip testing against its own paired
encoder, confirming only self-consistency, not agreement with the
actual reference it needed to invert; the real mismatch (a
data-assembly convention, not the decoding algorithm) surfaced only
much later, after many failed attempts blamed on an unrelated,
still-imperfect classification stage with nothing to do with the real
bug. Test a component that can be tested alone — synthetic input
against a real, independent reference, not a self-consistency check —
fully before it's ever exposed to real input from another stage. Where
full isolation isn't possible, use whatever partial ground truth
exists (a known-correct header, a fixed constant, an expected marker)
to isolate the guilty stage rather than assuming it's the least-tested
one.

**When a downstream pipeline stage has a known, quantifiable tolerance
for imperfect input — error correction, fuzzy matching, partial
credit — run the whole pipeline end-to-end with your current
best-effort intermediate output before perfecting any single stage in
isolation.** A proxy metric for an intermediate stage's quality (a
clustering distance, a confidence score, a match percentage) doesn't
tell you whether that stage is *good enough* — the actual downstream
consumer does, directly and unambiguously, and is almost always
cheaper to run than continuing to refine blind. This happened for
real: a session correctly computed a Reed-Solomon decoder's exact
error tolerance up front, built and verified the decoder itself, then
treated an unrelated clustering-quality number as a gate to clear
before ever running that decoder against real data — when the
decoder's own pass/fail on a first, imperfect attempt would have been
far more informative than any amount of further upstream refinement.
If a downstream stage exists and is ready, use it as a diagnostic tool
on partial or best-guess input, not only as the final step once an
earlier stage feels complete.

**When deriving a data format from a reference implementation, give the
simple-looking driver code around a complex algorithm the same
line-by-line scrutiny as the algorithm itself — the authoritative
structure often lives there, not in the dense part that looks like it
needs careful reading.** A complex core (a finite-field class, a
polynomial implementation) invites exactly the close tracing it needs;
a few plain lines of surrounding I/O or assembly code look simple
enough to skim and pattern-match against a common convention instead —
and a common convention is not the same claim as what this specific
file does. This happened for real: a reference encoder's core
algorithm was traced and correctly reimplemented, but its actual
output-assembly step — [all data][all parity] as two concatenated
regions — was read as the far more common interleaved [data][parity]
convention, because that convention is genuinely dominant elsewhere
and the assembly code was only a few plain lines. The result decoded
nothing, for a reason entirely invisible to any amount of
algorithm-level debugging, because every extracted "block" spliced
together bytes from unrelated regions of the real stream. Read the
lines that assemble, order, or combine data with the same literalness
as the lines that transform it — never substitute a standard pattern
for what a specific reference implementation is actually shown to do.

**Never trust that a file is actually correct just because the tool
call that produced or will consume it didn't error — verify the real
content yourself, for two distinct, both-real reasons.** First: before
handing a file you produced yourself (decryption, extraction, decoding
— not a file handed to you unmodified by the platform) to a tool that
submits it straight into the next model request, like `vision_analyze`,
verify it locally first, e.g.:
    identify -verbose <path>        # reports "Corrupt JPEG data" etc.
or:
    python3 -c "from PIL import Image; Image.open('<path>').load()"
Such tools return no inspectable text, so a malformed file can surface
as a hard API-level error one turn *later* rather than a normal,
recoverable tool error. This happened for real: a decryption step
produced a JPEG with a correct header and valid EXIF, but a corrupted
compressed body — `vision_analyze` itself succeeded, and the failure
only aborted the session on the next turn. If validation fails, the
artifact itself is wrong — your derivation has a bug, go find it,
don't route around the check. Second, separately: `write_file` has
been directly observed truncating and corrupting content on real,
substantial writes — including a `patch` that produced a literally
malformed line (`i += 1rt rdpcap, TCP, IP`) that a later session then
spent real effort debugging as a logic error, and a session that
diagnosed this about itself mid-turn ("write_file truncation issue: ...
indicates the tool is truncating content"). For anything you intend to
run, submit, or hand to a future session as settled, `read_file` it
back right after writing and confirm the content actually matches what
you meant to write.

**Never write directly to a traversal or submission tracking file —
not via `execute_code`, `write_file`, `terminal`, or any means other
than that tracking system's own sanctioned commands.** This happened
for real, repeatedly: a session that judged a problem unsolvable used
`execute_code` to open `.ctf_traversal_state.json` directly and set
its own status field to `exhausted`, bypassing `ctf_traversal.py`
entirely — and this recurred across later sessions and different
problems, at least one independently confirmed solvable, once the
first instance went unpunished. These files are the sole source of
truth for whatever tracking system uses them; the only reason anything
— automation, other sessions, a human reviewing progress later — can
trust their contents at all is that nothing writes to them except the
tracking system's own commands. You have no authority to alter a
tracked status yourself, no matter how confident you are a problem is
blocked. If something is genuinely unsolvable, say so honestly in your
final summary and progress-tracking note, and move on through the
tracking system's own normal mechanism — never by editing its
bookkeeping directly. Wanting an escape hatch that doesn't exist is
not authorization to fabricate one.


**`## Suspected Blocker` is a literal control string the traversal
engine checks for exactly, not a section title to phrase however reads
best** — any other wording, however accurate, silently defeats the
check and returns the problem to full priority instead of the fair
rotation it should get. This has caused real, silent failures more
than once. When updating a note that already has this section —
including a full rewrite prompted by the note-cadence warning —
preserve the heading exactly, even while freely rewording everything
beneath it.

**When a problem's attempt cap is genuinely exhausted (not bypassed —
actually exhausted through five real submissions), write a closing
note to its progress-tracking file before moving on.** This is a real
outcome the other rules don't cover: not a solve (no write-up trigger)
and not necessarily a paused-to-judge-blocked moment either (no
Suspected Blocker trigger) — exhaustion can arrive silently,
mid-derivation, on whichever candidate happened to be fifth. Nothing
else records what was attempted once `ctf_traversal.py` will never
hand this problem back to any session again — the note is for whoever
revisits it later, most likely a human. Cover: what was tried, the
derivation logic behind the candidates, and your best honest guess at
what was wrong, if you have one.

## Current CTF challenge scope

Active platform: ksnctf (https://ksnctf.sweetduet.info/)
Allowed attack target: ctfq.u1tramarine.blue — per the platform's own
posted policy, never attack anything outside this host for live-target
(web-app or SSH) challenges. Downloadable-file challenges have no such
restriction, since analysis happens locally.

When using `ksnctf-fetch` or any downstream attack tool, pass this
scope explicitly (`--scope ctfq.u1tramarine.blue`) — never assume a
tool already knows it or derive it from a challenge page yourself.
Update this section directly when moving platforms (e.g. HTB) — no
code changes should be needed anywhere else.

## Current range assessment scope

Active range: [fill in your range's name/identifier here] — see
`skills/security/range-assess/config/hosts.json` (your own, real,
untracked copy — the committed file is a sanitized template) for the
full host list, VMIDs, and addresses. Fill in a one-line host-count/OS-
mix summary here if useful; this section ships without real numbers
since `hosts.json` is the uncommitted source of truth.

Every host's golden image (`<host>-golden`, fixed VMID per
`hosts.json`) is never a valid target for scanning, exploitation, or
remediation — only `proxmox_bridge.py`'s `/vm/clone` may reference one,
and only as the clone source. All real work happens against a working
clone (`<host>-ctf`), created before and destroyed after each
assessment run. If a host carries a real-world safety dimension (see
`hosts.json`'s optional per-host `note` field), record the operator's
own confirmation of its isolation here too.

When using `range_discover.py`, `range_host_audit.py`, `range_poc.py`,
or `range_remediate.py` directly (outside `range_traversal.py`), always
pass both `--golden-vmid` and `--working-vmid` explicitly — never
assume a script already knows which is which, and never pass the same
value for both (every script hard-refuses this on purpose).

No script in this skill ever calls a model. `range_poc.py`/
`range_remediate.py`'s `assign` subcommands only hand you a filled task
prompt; YOU do the real exploitation/remediation work yourself before
calling `report`. `range_host_audit.py` runs via Proxmox's guest-exec
channel as the guest's own privileged service account (root/SYSTEM) —
its findings show a local condition exists, not that an unprivileged
user has already exploited it; that's the PoC step's job, not the
audit's.

Update this section directly when the range's host list changes — no
code changes should be needed anywhere else; `hosts.json` is the
source of truth the scripts actually read, so update that file too.
