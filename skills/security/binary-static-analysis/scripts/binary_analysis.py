"""
binary_analysis.py — Docker-native static binary analysis: Ghidra
inventory/decompile (any format Ghidra supports — PE, ELF, Mach-O) plus
.NET decompilation via ilspycmd. Runs directly in the sandbox; no VM, no
bridge, no network round-trip of any kind. This is the same validated
pyghidra logic proven against Riddle.exe in the Windows lab, adapted to
run locally instead of inside a Proxmox-hosted Windows VM.

Usage:
    python3 binary_analysis.py detect <binary_path>
        Identifies the binary format (PE, ELF, Mach-O) and, for PE
        binaries specifically, whether it's a .NET assembly (managed
        code) or native code. Run this FIRST — it determines which of
        the modes below is actually the right tool: .NET assemblies
        should go through dotnet-decompile, not ghidra-inventory/
        ghidra-decompile, which are for native code.

    python3 binary_analysis.py ghidra-inventory <binary_path>
        Dumps EVERY defined data symbol (address, type, length, value)
        AND every function (name, address) — not just printable strings.
        Use this BEFORE forming any hypothesis about what a binary needs.
        A multi-entry validation table (e.g. several answer arrays) is a
        static data structure, invisible to a plain strings dump — this
        is how you actually find it.

    python3 binary_analysis.py ghidra-decompile <binary_path> <function_name_or_address>
        Decompiles ONE function and prints the real C code. Use this
        instead of reasoning about disassembly by hand — read what the
        code actually does, don't guess from opcodes. For native (non-
        .NET) binaries only.

    python3 binary_analysis.py dotnet-decompile <binary_path> [output_dir]
        Decompiles a .NET assembly to C# source using ilspycmd. Outputs
        to output_dir (default: alongside the binary, in a
        <binary_name>_decompiled/ folder). For .NET assemblies only —
        check with `detect` first.
"""
import sys
import json
import subprocess
import os


def cmd_detect(binary_path: str):
    with open(binary_path, "rb") as f:
        header = f.read(4)

    if header[:2] == b"MZ":
        fmt = "PE"
        is_dotnet = False
        try:
            import pefile
            pe = pefile.PE(binary_path, fast_load=True)
            pe.parse_data_directories(
                directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_COM_DESCRIPTOR"]]
            )
            com_descriptor = pe.OPTIONAL_HEADER.DATA_DIRECTORY[
                pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_COM_DESCRIPTOR"]
            ]
            is_dotnet = com_descriptor.VirtualAddress != 0
        except Exception as e:
            print(f"Warning: pefile-based .NET check failed ({e}); "
                  "falling back to unreliable, do not trust this alone",
                  file=sys.stderr)
        result = {
            "format": fmt,
            "is_dotnet": is_dotnet,
            "recommendation": (
                "dotnet-decompile (managed code — Ghidra will not give "
                "useful results)" if is_dotnet else
                "ghidra-inventory / ghidra-decompile (native code)"
            ),
        }
    elif header == b"\x7fELF":
        result = {
            "format": "ELF",
            "is_dotnet": False,
            "recommendation": "ghidra-inventory / ghidra-decompile",
        }
    elif header in (b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",
                    b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
                    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"):
        result = {
            "format": "Mach-O",
            "is_dotnet": False,
            "recommendation": "ghidra-inventory / ghidra-decompile",
        }
    else:
        result = {
            "format": "unknown",
            "is_dotnet": False,
            "recommendation": (
                "Format not recognized from magic bytes alone — run "
                "`file` on it directly, and consider whether this is "
                "even a binary-analysis task at all (could be a "
                "different CTF category entirely)."
            ),
        }
    print(json.dumps(result, indent=2))


def cmd_ghidra_inventory(binary_path: str):
    import pyghidra
    pyghidra.start()

    with pyghidra.open_program(binary_path) as flat_api:
        program = flat_api.getCurrentProgram()
        listing = program.getListing()

        # PE-loader structural bookkeeping — the .reloc section's
        # relocation table (one header plus hundreds of individual
        # "word" fixup entries per block) — is never CTF-relevant and
        # confirmed in practice to be genuinely enormous for even a
        # small binary. This is a no-op for ELF/Mach-O, which don't have
        # a section by this name — safe to leave unconditional.
        memory = program.getMemory()
        SKIP_SECTIONS = {".reloc"}

        def in_skipped_section(addr) -> bool:
            block = memory.getBlock(addr)
            return block is not None and block.getName() in SKIP_SECTIONS

        result = {"functions": [], "data": []}
        for func in listing.getFunctions(True):
            result["functions"].append({
                "name": func.getName(),
                "address": str(func.getEntryPoint()),
            })
        skipped_count = 0
        for data in listing.getDefinedData(True):
            if in_skipped_section(data.getAddress()):
                skipped_count += 1
                continue
            try:
                val = str(data.getValue())
            except Exception:
                val = None
            result["data"].append({
                "address": str(data.getAddress()),
                "type": str(data.getDataType()),
                "length": data.getLength(),
                "value": val,
            })
        if skipped_count:
            result["_note"] = (
                f"{skipped_count} entries in .reloc (PE relocation "
                "table bookkeeping) omitted — never CTF-relevant, and "
                "were making this dump excessively large."
            )
        print(json.dumps(result, indent=2))


def cmd_ghidra_decompile(binary_path: str, target_function: str):
    import pyghidra
    pyghidra.start()

    with pyghidra.open_program(binary_path) as flat_api:
        program = flat_api.getCurrentProgram()
        listing = program.getListing()

        from ghidra.app.decompiler import DecompInterface
        from ghidra.util.task import ConsoleTaskMonitor

        func = None
        for f in listing.getFunctions(True):
            if f.getName() == target_function or str(f.getEntryPoint()).endswith(target_function):
                func = f
                break
        if func is None:
            print(f"Function not found: {target_function}")
            sys.exit(1)

        decomp = DecompInterface()
        decomp.openProgram(program)
        monitor = ConsoleTaskMonitor()
        res = decomp.decompileFunction(func, 60, monitor)
        if res.decompileCompleted():
            print(res.getDecompiledFunction().getC())
        else:
            print("Decompilation failed")
            sys.exit(1)


def cmd_dotnet_decompile(binary_path: str, output_dir: str = None):
    if output_dir is None:
        base = os.path.splitext(os.path.basename(binary_path))[0]
        output_dir = os.path.join(os.path.dirname(binary_path) or ".", f"{base}_decompiled")
    os.makedirs(output_dir, exist_ok=True)

    result = subprocess.run(
        ["ilspycmd", binary_path, "-o", output_dir],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"ilspycmd failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    print(f"Decompiled to: {output_dir}")
    print(result.stdout)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    mode = sys.argv[1]
    binary_path = sys.argv[2]

    if mode == "detect":
        cmd_detect(binary_path)
    elif mode == "ghidra-inventory":
        cmd_ghidra_inventory(binary_path)
    elif mode == "ghidra-decompile":
        if len(sys.argv) < 4:
            print("ghidra-decompile requires a function name or address")
            sys.exit(1)
        cmd_ghidra_decompile(binary_path, sys.argv[3])
    elif mode == "dotnet-decompile":
        output_dir = sys.argv[3] if len(sys.argv) > 3 else None
        cmd_dotnet_decompile(binary_path, output_dir)
    else:
        print(f"Unknown mode: {mode}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
