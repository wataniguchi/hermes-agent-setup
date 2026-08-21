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
            result = {"functions": [], "data": []}
            for func in listing.getFunctions(True):
                result["functions"].append({
                    "name": func.getName(),
                    "address": str(func.getEntryPoint()),
                })
            for data in listing.getDefinedData(True):
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
