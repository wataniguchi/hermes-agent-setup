---
name: windows-binary-analysis
description: Analyze Windows PE binaries in an isolated, air-gapped Proxmox VM
version: 1.0.0
metadata:
  hermes:
    tags: [security, ctf, windows, reverse-engineering, pwn]
    category: security
---

# Windows Binary Analysis

## When to Use

Use this skill when a CTF assignment or security task requires analyzing a
Windows executable (PE file) — pwn challenges targeting Windows, malware
triage, or reverse engineering — since the Docker sandbox only runs Linux
containers and cannot execute or debug Windows binaries directly.

This gives you a real, isolated Windows 10 VM with no network access
(air-gapped — cannot reach the internet or your LAN under any circumstance),
pre-loaded with: Sysinternals Suite, x64dbg/x32dbg, PE-bear, Ghidra
(including `analyzeHeadless.bat` for scripted analysis), and Python. All
tools are on the machine-wide `PATH`, callable by bare filename.

**A professional security analyst does not give up or guess just because
dynamic execution isn't safely or readily available.** This is the same
discipline real malware triage requires — you frequently cannot safely
execute a target at all, and that has never been an excuse to stop
investigating. When execution is unavailable (a binary that won't launch —
missing old runtime dependencies are common in this VM) or would be unsafe
to run freely, the correct professional response is **exhaustive static
analysis**: full disassembly (Ghidra), decompilation (`decompile`, for
.NET binaries), tracing data flow and cross-references until you actually
understand what the program does — not settling for a plausible-looking
guess because the "easy" path (just run it and see) isn't open. Dynamic
execution (`gui-probe`, piped `exec`) remains a legitimate, valuable
technique when a target genuinely runs in this air-gapped, disposable VM —
use it. The point is never let its unavailability be a stopping condition.

## Procedure

All commands run via `terminal` in this container. The helper script talks
to the Proxmox bridge over `host.docker.internal:8811` — you don't need to
know anything about Proxmox itself.

**1. Start a session** (pushes your sample into a fresh, isolated VM):

```
python3 /path/to/skills/security/windows-binary-analysis/scripts/analyze_windows_binary.py start <local_path_to_sample>
```

Returns `{"vmid": N, "guest_path": "C:\\Samples\\<filename>", "reused": false}`.
**This step takes a while the first time** — the template disk clones from a
slow archival drive. This is normal, not a hang.

**`start` is safe to call more than once — it will not create a duplicate
VM.** If a session is already active, `start` detects it, confirms it's
still alive, and pushes the new sample into the *existing* session instead
of cloning again (`"reused": true` in the response). You do not need to
track whether you've already called `start` in this task — just call it
again if unsure, rather than guessing.

Save the `vmid` — every subsequent command in this session needs it.

**If you have more than one sample to analyze in the same task, push
additional files into this same session instead of starting a second one**
(a second `start` means a second 30-60+ minute clone wait, which is almost
never worth it — one VM can hold and analyze several samples):

```
python3 .../analyze_windows_binary.py push <vmid> <local_path_to_second_sample>
```

Returns `{"vmid": N, "guest_path": "C:\\Samples\\<filename>"}`, same shape as
`start`, but immediate — no clone/boot wait since the VM is already running.

**2. Run analysis commands** (repeat as many times as needed — this is the
normal way to interact with the VM, not just for setup):

```
python3 .../analyze_windows_binary.py exec <vmid> -- cmd /c "certutil -hashfile C:\Samples\<filename> SHA256"
python3 .../analyze_windows_binary.py exec <vmid> -- cmd /c "where x64dbg.exe"
python3 .../analyze_windows_binary.py exec <vmid> -- C:\Tools\ghidra\support\analyzeHeadless.bat C:\Samples\project ProjectName -import C:\Samples\<filename>
```

**Use whichever technique is safely and reliably available, and never let
unavailability be a stopping condition.** If a binary genuinely runs, both
static and dynamic analysis are legitimate — use what's actually useful.
If a binary can't be run safely or reliably (missing runtime dependencies
are common in this VM — that's diagnostic information, not a blocker),
that means exhaustive static analysis: find the actual comparison,
validation, or construction logic in the disassembly/decompiled source,
and derive/recompute the result directly. This is professional discipline,
not a workaround — it's the same standard real malware triage requires,
since execution frequently isn't safe there either.

**If the binary is an interactive GUI app and genuinely runs**, and static
analysis alone hasn't yielded the answer, `gui-probe` can drive it and
report back real dialog/window text as a secondary confirmation:

```
python3 .../analyze_windows_binary.py gui-probe <vmid> C:\Samples\<filename> --input-text "candidate answer"
```

For multi-stage challenges (a result only appears after several correct
answers submitted in order), use `--input-sequence` instead, semicolon-
separated:

```
python3 .../analyze_windows_binary.py gui-probe <vmid> C:\Samples\<filename> --input-sequence "answer1;answer2;answer3"
```

This submits each answer in order, capturing window/dialog state after
every step (not just the last) — useful both for confirming intermediate
steps succeeded and for seeing the final result.

This launches the exe, sends candidate text plus Enter into its main
window, and reports the resulting window/dialog text as JSON. Known
limitation: it only tracks windows owned by the target's own process ID —
if the binary crashes on launch (e.g. a missing-DLL error), the resulting
system error dialog may belong to a different process and won't be
captured; an empty result here is more likely a launch failure than a
script bug — check whether the binary runs at all before assuming
`gui-probe` itself is broken.

**`gui-probe` also captures a real screenshot** (actual rendered pixels,
not just control text — catches custom-painted content that window-text
enumeration misses entirely). Add `--screenshot-out <local-path>` to pull
it back automatically:

```
python3 .../analyze_windows_binary.py gui-probe <vmid> C:\Samples\<filename> --input-text "candidate" --screenshot-out /workspace/screenshot.png
```

**Important honest limitation: the model driving this skill is very
likely text-only and cannot actually view the resulting image.** This
screenshot capability exists for a human operator to inspect directly when
the agent is stuck or when window-text enumeration alone isn't giving a
clear picture — not as something the agent itself can currently read. If
OCR tooling gets added to the golden template later, that would let the
agent extract text from the screenshot programmatically; until then, treat
a saved screenshot as something to hand back to the operator rather than
something you can interpret yourself.

**Check whether the binary is .NET (MSIL/CIL) BEFORE reaching for Ghidra.**
Run `strings.exe -accepteula` against it (see step 3 below for the
mechanics) and look for `mscorlib`, `System.Reflection`, `.NETFramework`,
or similar CLR/BCL references. **Ghidra and x64dbg are the wrong tools for
.NET binaries** — they analyze native x86/x64 code, not MSIL, and will
waste significant time producing garbled or useless results. If it's .NET,
use the `decompile` subcommand instead, which uses `ilspycmd` to turn the
binary back into near-original, readable C# source:

```
python3 .../analyze_windows_binary.py decompile <vmid> C:\Samples\<filename>
```

Returns `{"vmid": N, "decompiled_source": "C:\\Samples\\decompiled_<name>\\combined.cs"}`
— pull that file and read it directly. Look for the actual win-condition
logic: a `check()`-style function, comparisons against generated or
static-array-derived strings, hardcoded values. This is very often exactly
where the flag (or the logic that produces it) lives, in plainly readable
C#.

**Once you've found pure, self-contained logic in the decompiled source
(no GUI/system dependencies — just computing a value from a fixed input),
reimplement it in Python and run it directly in your own environment (this
sandbox container) rather than round-tripping through the VM.** This is
faster, avoids all Windows-path-quoting issues, and doesn't need any guest
interaction at all for this step. Only go back to the VM if you need to
confirm behavior against the real binary.

**Search for a public writeup if the binary's strings reveal identifying
info.** Copyright strings, embedded PDB paths, company/product names, or
challenge-platform references (e.g. a specific CTF platform name + problem
number) are often enough to find that this is a previously-published
challenge with a known writeup — worth a `web_search` before or alongside
deeper RE work, as a way to confirm your approach or find one faster.

Each `exec` call returns `{"exitcode": N, "stdout": "...", "stderr": "..."}`.

**Quote every argument containing a backslash when writing `exec` commands
by hand** — bash strips backslashes from unquoted words before they ever
reach this script, silently mangling every Windows path
(`C:\Samples\file.exe` becomes `C:Samplesfile.exe`). This caused repeated,
hard-to-diagnose failures in practice. When a command has more than one or
two arguments, or involves redirection (`>`, `|`) or nested quotes,
**prefer writing it to a local file and pushing that file** (via `push`)
over constructing it as a giant one-line `exec` command — this sidesteps
shell-escaping entirely and was far more reliable in practice than fighting
multi-layer quoting through bash → this script → the bridge → Proxmox →
Windows.

**3. Retrieve result files** (decoded correctly for both text and binary):

```
python3 .../analyze_windows_binary.py pull <vmid> "C:\Samples\output.txt" /workspace/output.txt
```

**4. Always destroy the session when the task is done:**

```
python3 .../analyze_windows_binary.py destroy <vmid>
```

This stops and deletes the VM, freeing storage. Leftover VMs consume real
disk space on the `hot-ssd` storage pool with no automatic cleanup.

## Pitfalls

- **A 404 from the bridge's bare root path (`http://host.docker.internal:8811/`) does NOT mean the bridge is down.** No route is defined there by design. If you need to check the bridge is reachable, use `/openapi.json` or just try the actual command you need — a real failure will give a clearer error.
- **The 30-60 minute `start` wait on first use is expected**, not a bridge failure — the template lives on a slow archival drive by design (see `README_Proxmox.md`).
- **Always call `destroy`** at the end of a session, even if the analysis
  didn't find what you were looking for — orphaned VMs aren't cleaned up
  automatically and will eventually exhaust the `hot-ssd` storage pool.
- **The VM has no network access whatsoever** — this is intentional and
  permanent (`vmbr1` has no physical uplink at all). Don't attempt to
  download additional tools into the guest; if something's missing, it
  needs to be added to the golden template on the Proxmox side instead
  (see `README_Proxmox.md`'s toolkit section).
- **Commands run as SYSTEM, not an interactive user** — no GUI is visible.
  If a task genuinely needs interactive GUI debugging (stepping through
  x64dbg visually), that's outside what this skill's `exec` subcommand can
  do; flag this back rather than attempting to fake it with headless output.
  Sysinternals command-line tools (`handle.exe`, etc.) work fine; their
  EULAs were pre-accepted when the template was built — but for any
  Sysinternals tool NOT explicitly pre-accepted, add `-accepteula` to the
  command rather than risk a silent blocking dialog.
- **Never report a flag you have not verified.** Verification means one of:
  (a) you found and read the actual comparison/validation logic in the
  disassembly or decompiled source, and independently derived/recomputed
  the exact value it checks against (this is usually the right approach —
  it's how a real .NET binary's XOR-encoded flag was correctly solved
  purely through static analysis, no execution needed), or (b) you
  actually ran the program with your candidate and observed it succeed.
  Recognizing a plausible-looking answer (a famous number, a common
  phrase, a pattern that "seems right" based on general knowledge) is
  **neither of these** — it is a guess. This has happened in practice: a
  binary referencing the Hitchhiker's Guide's "42" led to reporting
  `FLAG{42}` as final without ever deriving it from the actual code or
  confirming it against real behavior.
- **Never treat unavailable or unsafe execution as a reason to stop
  investigating or to guess.** A binary that won't launch (missing old
  runtime dependencies are common in this VM) or that shouldn't be run
  freely is not a dead end — it is precisely the situation exhaustive
  static analysis exists for. This is a professional standard, not a
  workaround: real malware triage routinely can't safely execute the
  target at all, and "I couldn't run it" is never an acceptable reason to
  fall back on a plausible-sounding guess. Push disassembly/decompilation
  as far as it takes — trace the actual comparison, validation, or
  construction logic — before concluding you're stuck.
- **When a binary genuinely runs, dynamic tools (`gui-probe`, piped
  `exec`) are equally legitimate — use whichever technique the situation
  actually calls for**, not a fixed preference order. `gui-probe`
  (below) is a secondary tool for when a binary genuinely does run and
  static analysis alone hasn't yielded the answer — it does not replace
  reading the actual logic when that's the more reliable path.
- **Never try to restart the guest agent service itself (`sc stop QEMU-GA` /
  `sc start QEMU-GA`) via `exec`.** This is a hard QEMU limitation, not a
  bug: a command that stops the agent service, issued *through that same
  agent*, kills the channel before it can complete — there is no way to
  safely restart QGA remotely this way. If something needs the guest
  environment refreshed (e.g. after a PATH change made through another
  channel), that requires an actual VM restart, which isn't something this
  skill's tools do automatically — flag it back rather than attempting a
  guest-agent self-restart.
- **A binary that never produces output when run or piped to might be a
  GUI application, not a hung/broken process.** `.NET`/WinForms binaries
  in particular run a message loop and never touch stdin/stdout at all —
  no amount of piped input will do anything. Check the binary's strings
  for CLR/BCL references (`mscorlib`, `System.Windows.Forms`, etc.) before
  concluding a binary is "hanging."
- **A timed-out `exec` call may leave the underlying process still running
  in the guest.** The bridge attempts a best-effort cleanup kill on
  timeout, but commands that finish quickly yet still don't produce useful
  output (repeated retries with different inputs) can accumulate orphaned
  processes over a session. If something seems to be behaving
  inconsistently, check `tasklist` for unexpected accumulated instances of
  the target binary or of tools like `strings.exe` before assuming a new
  bug — `taskkill /F /IM <name> /T` clears them.
- If a sample starts behaving like it might be real malware rather than a
  scoped CTF challenge (persistence attempts, anti-VM evasion, unexpected
  outbound-connection attempts despite no network path), stop and report
  this rather than continuing automated analysis — see the Security notes
  in `README_Proxmox.md`.
- **If delegating this task to subagents (`delegate_task`), the primary must
  `start` the session once and pass the resulting `vmid` to all subagents —
  subagents must NOT each call `start` themselves.** A second clone costs
  another 30-60+ minute wait for no benefit, and running two clones
  concurrently competes for the same physical disk read, likely making
  *both* slower than one clone alone (the source drive is SMR — see
  `README_Proxmox.md`). Subagents calling `exec`/`push`/`pull` concurrently
  against the *same* `vmid` is safe — each `exec` call gets its own
  independent guest-agent process, so concurrent calls don't interfere with
  each other at the protocol level.
- **Serialize heavy analysis steps even within one shared VM.** The
  template VM has 8GB RAM / 4 vCPUs — fine for one subagent's workload, but
  multiple subagents running `analyzeHeadless.bat` (or other
  memory-intensive analysis) at the same time can genuinely contend for
  guest resources and slow each other down. Lightweight steps (hashing,
  `where`, string search) are fine running concurrently across subagents;
  have subagents take turns for anything Ghidra/heavy-analysis-shaped.
- Give each subagent's work **distinct guest paths and Ghidra project
  names** (e.g. include the sample's filename or a task ID) to avoid
  collisions when multiple subagents write into the same shared VM.

## Verification

After `start` returns, confirm the VM is actually responsive before relying
on it further:

```
python3 .../analyze_windows_binary.py exec <vmid> -- cmd /c "where x64dbg.exe"
```

Should return `exitcode: 0` and a real path. If this fails, the guest agent
may not have finished initializing yet — wait another minute and retry this
one check (not the whole `start`).
