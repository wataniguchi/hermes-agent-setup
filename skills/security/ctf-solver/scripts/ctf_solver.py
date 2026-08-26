"""
ctf_solver.py — top-level orchestrator: acquire a challenge via
ksnctf-fetch, then route based on what was actually found.

Honest about scope: this fully automates the acquire-and-classify step
for every delivery mode, and hands off to binary-static-analysis
automatically for downloadable binaries — the one path with genuinely
complete tooling behind it. For embedded_web_app and direct_ssh_access
modes, it reports the target/credentials clearly but does NOT pretend to
automate an attack — no dedicated skill exists for either yet, and
pretending otherwise here would be worse than just saying so.

Deliberately does NOT call ksnctf-submit automatically. That skill's
whole guardrail exists to add friction against casual, automatic
guessing — wiring "derive something -> immediately submit it" into one
seamless pipeline would undermine that on purpose. Submission stays a
separate, explicitly-invoked step for whoever is confident in a
derivation, not something this script does on your behalf.

Usage:
    python3 ctf_solver.py solve <problem_url> --scope <allowed_host>

Scope must be sourced from AGENTS.md's "Current CTF challenge scope"
section, exactly as with ksnctf-fetch itself — never hardcoded or
guessed here either.
"""
import sys
import os
import json
import subprocess
import argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILLS_DIR = os.path.dirname(os.path.dirname(SCRIPT_DIR))  # .../skills/security

DEFAULT_KSNCTF_FETCH = os.path.join(SKILLS_DIR, "ksnctf-fetch", "scripts", "ksnctf_fetch.py")
# Default rather than required, unlike --scope: a stale/wrong path here
# just fails loudly (file not found) rather than silently doing
# something risky, so the ergonomic cost of requiring it explicitly
# every time isn't worth paying for today's single-platform reality.
# Still fully overridable for a future platform's own fetch script.
BINARY_ANALYSIS = os.path.join(SKILLS_DIR, "binary-static-analysis", "scripts", "binary_analysis.py")

# Confirmed real ksnctf file types (see CTF_GENERALIZATION_DESIGN.md) that
# binary-static-analysis's `detect` won't recognize as PE/ELF/Mach-O —
# these need manual, agent-driven work with the relevant raw tool.
KNOWN_NON_BINARY_HINTS = {
    ".pcap": "scapy / a pcap-analysis tool — network capture, not a binary to decompile",
    ".pcapng": "scapy / a pcap-analysis tool — network capture, not a binary to decompile",
    ".docx": "extract and read directly — likely contains the challenge text/data itself",
    ".apk": "jadx or a similar Android decompiler — not yet wrapped by any skill here",
    ".zip": "extract and re-run detect on whatever's inside, or check for a password (fcrackzip/stegseek)",
    ".jpg": "zsteg / stegoveritas — steganography, not a binary to decompile",
    ".png": "zsteg / stegoveritas — steganography, not a binary to decompile",
    ".gif": "zsteg / stegoveritas — steganography, not a binary to decompile",
    ".html": "read the raw HTML/JS directly — likely contains the actual challenge "
             "logic client-side (a cipher, an obfuscated script, etc.), not something "
             "to decompile. Confirmed real example: problem 3 (Crawling Chaos) ships "
             "unya.html this way.",
}


# CONFIRMED, not hypothetical: ctfq.u1tramarine.blue — the live-target
# host behind both embedded_web_app and direct_ssh_access modes — is
# currently unreachable from the sandbox's network. This is a separate
# host from ksnctf.sweetduet.info (confirmed reachable — ksnctf-fetch
# and ksnctf-submit both work against it), so this postponement affects
# only these two modes, not downloadable_file. Revisit whether this
# resolves later; it may be a temporary or environment-specific network
# restriction rather than a permanent fact about the platform.
HOST_UNREACHABLE_NOTE = (
    "ctfq.u1tramarine.blue is confirmed unreachable from this sandbox's "
    "network right now. This challenge is postponed, not just unautomated "
    "— nothing can be done against it until connectivity is restored. "
    "Move on to a downloadable_file challenge instead."
)


def run_script(script_path: str, args: list) -> dict:
    result = subprocess.run(
        ["python3", script_path] + args,
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return {"_error": True, "stderr": result.stderr, "stdout": result.stdout}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"_error": True, "reason": "non-JSON output", "stdout": result.stdout}


def handle_downloadable_files(fetch_result: dict) -> dict:
    findings = []
    for file_path in fetch_result.get("downloaded_files", []):
        ext = os.path.splitext(file_path)[1].lower()
        detect_result = run_script(BINARY_ANALYSIS, ["detect", file_path])

        # CONFIRMED BUG, found via real testing: binary_analysis.py
        # detect never actually sets _error for a file it successfully
        # opens and reads but doesn't recognize (PE/ELF/Mach-O) — it
        # returns format: "unknown" instead, a genuine success case with
        # an unhelpful classification, not a failure. Checking for
        # _error here meant KNOWN_NON_BINARY_HINTS could never actually
        # trigger; every non-binary file fell through to
        # binary_analysis.py's own generic "run `file` on it" message
        # instead of the tailored per-extension hint intended here.
        is_unrecognized = (
            detect_result.get("_error")
            or detect_result.get("format") not in ("PE", "ELF", "Mach-O")
        )

        if is_unrecognized:
            hint = KNOWN_NON_BINARY_HINTS.get(ext)
            findings.append({
                "file": file_path,
                "recognized_binary": False,
                "hint": hint or (
                    f"Unrecognized file type ({ext or 'no extension'}) — "
                    "no automated path exists for this; inspect it "
                    "directly (`file`, `strings`, or open it) to figure "
                    "out what it actually is before deciding on a tool."
                ),
            })
        else:
            findings.append({
                "file": file_path,
                "recognized_binary": True,
                "format": detect_result.get("format"),
                "is_dotnet": detect_result.get("is_dotnet"),
                "recommendation": detect_result.get("recommendation"),
            })
    return {"downloadable_file_findings": findings}


def cmd_solve(url: str, scope: str, fetch_script: str):
    fetch_result = run_script(fetch_script, ["fetch", url, "--scope", scope])

    if fetch_result.get("_error"):
        print(json.dumps({
            "solved": False,
            "stage": "acquire",
            "error": fetch_result,
        }, indent=2))
        sys.exit(1)

    result = {
        "url": url,
        "metadata": fetch_result.get("metadata"),
        "modes_detected": fetch_result.get("modes_detected", []),
        "next_steps": {},
    }

    if "downloadable_file" in result["modes_detected"]:
        result["next_steps"]["downloadable_file"] = handle_downloadable_files(fetch_result)

    if "embedded_web_app" in result["modes_detected"]:
        web_app = fetch_result.get("web_app_target", {})
        result["next_steps"]["embedded_web_app"] = {
            "target_url": web_app.get("url"),
            "in_scope": web_app.get("in_scope"),
            "status": "postponed",
            "note": HOST_UNREACHABLE_NOTE,
        }

    if "direct_ssh_access" in result["modes_detected"]:
        ssh = fetch_result.get("ssh_access", {})
        result["next_steps"]["direct_ssh_access"] = {
            "user": ssh.get("user"),
            "host": ssh.get("host"),
            "port": ssh.get("port"),
            "password": ssh.get("password"),
            "status": "postponed",
            "note": HOST_UNREACHABLE_NOTE,
        }

    if not result["modes_detected"]:
        result["note"] = (
            "ksnctf-fetch detected no delivery mode at all for this "
            "page. Inspect the raw fetch output directly rather than "
            "assuming nothing is there — could be an untested page "
            "structure."
        )

    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_solve = sub.add_parser("solve")
    p_solve.add_argument("url")
    p_solve.add_argument("--scope", required=True,
                          help="Allowed attack-target host, sourced from "
                               "AGENTS.md's current scope section")
    p_solve.add_argument("--fetch", default=DEFAULT_KSNCTF_FETCH,
                          help="Path to the platform's fetch script — "
                               "defaults to ksnctf-fetch; override for a "
                               "different platform's own fetch script")

    args = parser.parse_args()

    if args.command == "solve":
        cmd_solve(args.url, args.scope, args.fetch)


if __name__ == "__main__":
    main()
