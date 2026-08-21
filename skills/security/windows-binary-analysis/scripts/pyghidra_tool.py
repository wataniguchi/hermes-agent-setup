"""
pyghidra_tool.py — proper PyGhidra-based Ghidra analysis, run as a plain
Python script (python.exe pyghidra_tool.py ...) rather than via
analyzeHeadless.bat -postScript. This sidesteps Ghidra 12.x's script-
runtime detection entirely (see README_Proxmox.md's troubleshooting
history) by using the `pyghidra` Python package directly.

One-time setup on the golden template (already documented in
README_Proxmox.md, offline, no internet needed — Ghidra ships its own
wheel):
    python -m pip install --no-index -f C:\\Tools\\ghidra\\Ghidra\\Features\\PyGhidra\\pypkg\\dist pyghidra

Usage:
    python pyghidra_tool.py inventory <binary_path>
        Dumps EVERY defined data symbol (address, type, length, value)
        AND every function (name, address) — not just printable strings.
        Use this BEFORE forming any hypothesis about what a binary needs.
        A multi-entry validation table (e.g. several answer arrays) is a
        static data structure, invisible to a plain strings dump — this
        is how you actually find it.

    python pyghidra_tool.py decompile <binary_path> <function_name_or_address>
        Decompiles ONE function and prints the real C code. Use this
        instead of reasoning about disassembly by hand — read what the
        code actually does, don't guess from opcodes.
"""
import sys
import json


def main():
    if len(sys.argv) < 3:
        print("Usage: pyghidra_tool.py <inventory|decompile> <binary_path> [function]")
        sys.exit(1)

    mode = sys.argv[1]
    binary_path = sys.argv[2]
    target_function = sys.argv[3] if len(sys.argv) > 3 else None

    import pyghidra
    pyghidra.start()

    with pyghidra.open_program(binary_path) as flat_api:
        program = flat_api.getCurrentProgram()
        listing = program.getListing()

        if mode == "inventory":
            # PE-loader structural bookkeeping — the .reloc section's
            # relocation table (one header plus hundreds of individual
            # "word" fixup entries per block) — is never CTF-relevant and
            # confirmed in practice to be genuinely enormous for even a
            # small binary. Filtering by data-type name alone isn't
            # enough: only the block HEADER carries an
            # IMAGE_BASE_RELOCATION-prefixed type — every individual fixup
            # underneath it is typed just "word", indistinguishable by
            # type name alone from any other word-sized value elsewhere in
            # the binary. Filtering by actual PE section name instead
            # correctly catches the whole section regardless of each
            # entry's own type.
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

        elif mode == "decompile":
            if not target_function:
                print("decompile mode requires a function name or address")
                sys.exit(1)
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
        else:
            print(f"Unknown mode: {mode}")
            sys.exit(1)


if __name__ == "__main__":
    main()
