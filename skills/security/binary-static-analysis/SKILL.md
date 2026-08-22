---
name: binary-static-analysis
description: Static analysis of any binary format (PE, ELF, Mach-O, .NET) directly in the Docker sandbox — no VM required
version: 1.0.0
metadata:
  hermes:
    tags: [security, ctf, reverse-engineering, binary-analysis, ghidra]
    category: security
---

## Why this exists, and why it's not the Windows lab

Ghidra analyzes a binary as data — it never executes it, and works
identically regardless of what platform the target was built for. The
same is true of `ilspycmd` (a cross-platform .NET decompiler, despite
decompiling Windows-built assemblies). Neither tool needs Windows at all.

This skill runs both directly in the Docker sandbox: no VM clone, no
bridge, no guest-agent round-trip, no 30-60 minute wait. Use this for any
binary-analysis question. Only reach for `windows-binary-analysis`
(the Proxmox-backed skill) if you genuinely need to *run* a Windows
binary and observe its live behavior — a much narrower, much rarer need
than most CTF binary questions actually require.

## Workflow

**1. Detect the format first — this determines which tool actually
applies:**
```
python3 .../binary_analysis.py detect /workspace/samples/<filename>
```
Returns the format (PE/ELF/Mach-O) and, for PE binaries, whether it's a
.NET assembly. **Check this before reaching for Ghidra** — Ghidra
handles .NET's IL bytecode poorly; a .NET assembly needs
`dotnet-decompile` instead.

**2a. For native code (the common case) — exhaustive inventory before
any hypothesis:**
```
python3 .../binary_analysis.py ghidra-inventory /workspace/samples/<filename>
```
Dumps every function and every defined data symbol — not just printable
strings. A static data table (e.g. multiple separate answer arrays) has
no string representation at all; this is how you actually find it. See
the exhaustive-inventory rule in `AGENTS.md` — it applies here directly.

**2b. Decompile specific functions once you know which ones matter:**
```
python3 .../binary_analysis.py ghidra-decompile /workspace/samples/<filename> <function_name_or_address>
```
Read the actual decompiled C — don't reason about disassembly by hand.
See `AGENTS.md`'s rule against describing a function's contents without
having actually decompiled it in this same session — that rule applies
here with zero modification.

**3. For .NET assemblies — decompile to real C# source:**
```
python3 .../binary_analysis.py dotnet-decompile /workspace/samples/<filename> [output_dir]
```
Outputs full C# source files. Read the actual code, same standard as
native decompilation — no shortcuts for managed code either.

## What's genuinely untested here

The `detect` and `dotnet-decompile` modes are new — built by adapting the
already-validated `ghidra-inventory`/`ghidra-decompile` logic (proven
end-to-end against a real CTF binary in the Windows lab) but not yet
dry-run tested themselves. Validate both against a known sample before
trusting them for real work:
- `detect` against a known native PE, a known ELF, and — if available — a
  known .NET assembly, to confirm the format/`.NET` classification is
  actually correct in each case.
- `dotnet-decompile` against any .NET assembly, checking that real,
  readable C# comes out the other end.

## Standing rules

Every rule in `AGENTS.md`'s "Standing rules" section applies here
directly and without modification — exhaustive inventory before
hypothesis, never describe something you haven't inspected, verify by
running rather than reasoning, don't substitute trivia for a stalled
derivation, never report an unverified answer. Not repeated here to avoid
drift between two copies; read them there.
