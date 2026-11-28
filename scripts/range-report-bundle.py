#!/usr/bin/env python3
"""
range-report-bundle.py — generate the full operator report set for a
range-assess sweep into one directory under the workspace.

Runs the three read-only reporting tools and collects their output:

  1. status_report.txt                overall sweep status
                                        (range-status-report.py)
  2. vuln_list.txt                     every proven vulnerability
                                        (range-vuln-list.py)
  3. vuln_list_needs_decision.txt      the proven vulns flagged
                                        needs_operator_decision
                                        (range-vuln-list.py --needs-decision)
  4. vulns/<vmid>_<host>_<fid>.txt     one full dossier per proven
                                        vulnerability
                                        (range-vuln-show.py --vmid <vmid> <fid>)

Plus two conveniences:
  - vuln_list.json   the machine-readable manifest the per-finding dossiers
                     are enumerated from (also handy downstream).
  - INDEX.md         a table of everything generated, with per-finding
                     host / status / ⚑-flag and a link to each dossier.

Read-only against the sweep: it only reads the workspace *through* those
three tools and writes files into the output directory. It never touches
sweep state, VMs, or anything else. Standard library only.

Usage:
    python3 range-report-bundle.py --workspace ~/hermes-agent-setup/workspace
    python3 range-report-bundle.py --workspace <ws> --out <dir>
    python3 range-report-bundle.py --workspace <ws> --scripts-dir <dir>
    python3 range-report-bundle.py --workspace <ws> --json

--workspace is the sweep workspace (the directory holding
.range_traversal_state.json and the logs); like the other range-sweep
scripts it defaults to /workspace (the path inside the sandbox), so from
the Mac you pass --workspace ~/hermes-agent-setup/workspace.

Output goes to <workspace>/reports/range-report_<timestamp>/ by default;
override the whole path with --out. The three tools are looked up next to
this script unless --scripts-dir says otherwise.

--json also saves the JSON form of the status report and the
needs-decision list alongside the text ones.

Exit status: 0 if every tool call succeeded, 1 if the workspace or a tool
is missing (nothing is generated), 2 if the bundle was produced but one or
more individual tool calls failed (see _errors.log in the output dir).
"""
import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys

TOOLS = {
    "status": "range-status-report.py",
    "list": "range-vuln-list.py",
    "show": "range-vuln-show.py",
}

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe(s: str) -> str:
    return _SAFE.sub("_", str(s)).strip("_") or "x"


def run_tool(pyexe, tool_path, workspace, extra_args, timeout=300):
    """Run one reporting tool. Returns (stdout, stderr, returncode).

    returncode -1 means the call timed out; stdout is '' in that case.
    """
    cmd = [pyexe, tool_path, "--workspace", workspace] + list(extra_args)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "", f"TIMEOUT after {timeout}s", -1
    return p.stdout, p.stderr, p.returncode


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workspace", default="/workspace",
                    help="sweep workspace holding .range_traversal_state.json (default: /workspace)")
    ap.add_argument("--out", default=None,
                    help="output directory (default: <workspace>/reports/range-report_<timestamp>)")
    ap.add_argument("--scripts-dir", default=None,
                    help="directory holding the three reporting tools (default: next to this script)")
    ap.add_argument("--json", action="store_true",
                    help="also save JSON forms of the status report and the needs-decision list")
    args = ap.parse_args()

    pyexe = sys.executable or "python3"
    ws = os.path.abspath(os.path.expanduser(args.workspace))
    scripts_dir = os.path.abspath(os.path.expanduser(args.scripts_dir)) \
        if args.scripts_dir else os.path.dirname(os.path.abspath(__file__))

    # --- Preconditions: fail fast, generate nothing, exit 1 ---
    state_file = os.path.join(ws, ".range_traversal_state.json")
    if not os.path.isfile(state_file):
        print(f"FATAL: no .range_traversal_state.json in {ws}", file=sys.stderr)
        return 1
    tool_path = {}
    for key, name in TOOLS.items():
        pth = os.path.join(scripts_dir, name)
        if not os.path.isfile(pth):
            print(f"FATAL: cannot find {name} in {scripts_dir}", file=sys.stderr)
            return 1
        tool_path[key] = pth

    # --- Output directory ---
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.abspath(os.path.expanduser(args.out)) if args.out \
        else os.path.join(ws, "reports", f"range-report_{stamp}")
    vulns_dir = os.path.join(out, "vulns")
    os.makedirs(vulns_dir, exist_ok=True)

    errors = []  # (label, stderr)

    def emit(label, text, filename):
        with open(os.path.join(out, filename), "w") as f:
            f.write(text)
        return filename

    def record(label, stderr, rc):
        if rc != 0 or (stderr and stderr.strip()):
            errors.append((label, (stderr or "").strip() or f"exit {rc}"))

    # --- 1. status report ---
    sout, serr, rc = run_tool(pyexe, tool_path["status"], ws, [])
    emit("status", sout, "status_report.txt")
    record("status_report.txt", serr, rc)
    if args.json:
        jout, jerr, jrc = run_tool(pyexe, tool_path["status"], ws, ["--json"])
        emit("status-json", jout, "status_report.json")
        record("status_report.json", jerr, jrc)

    # --- 2. full vulnerability list ---
    lout, lerr, lrc = run_tool(pyexe, tool_path["list"], ws, [])
    emit("list", lout, "vuln_list.txt")
    record("vuln_list.txt", lerr, lrc)

    # --- 3. needs-operator-decision list ---
    nout, nerr, nrc = run_tool(pyexe, tool_path["list"], ws, ["--needs-decision"])
    emit("list-needs-decision", nout, "vuln_list_needs_decision.txt")
    record("vuln_list_needs_decision.txt", nerr, nrc)
    if args.json:
        njout, njerr, njrc = run_tool(pyexe, tool_path["list"], ws,
                                      ["--needs-decision", "--json"])
        emit("list-needs-decision-json", njout, "vuln_list_needs_decision.json")
        record("vuln_list_needs_decision.json", njerr, njrc)

    # --- manifest: the full list as JSON drives the per-finding dossiers ---
    mout, merr, mrc = run_tool(pyexe, tool_path["list"], ws, ["--json"])
    emit("list-json", mout, "vuln_list.json")
    record("vuln_list.json", merr, mrc)
    try:
        rows = json.loads(mout) if mout.strip() else []
    except json.JSONDecodeError as e:
        errors.append(("vuln_list.json(parse)", str(e)))
        rows = []

    # --- 4. one dossier per proven vulnerability ---
    produced = []  # (row, rel_path)
    for row in rows:
        gvmid = str(row.get("golden_vmid", "?"))
        fid = str(row.get("finding_id", "?"))
        host = row.get("host", "?")
        fname = f"{safe(gvmid)}_{safe(host)}_{safe(fid)}.txt"
        rel = os.path.join("vulns", fname)
        dout, derr, drc = run_tool(pyexe, tool_path["show"], ws,
                                   ["--vmid", gvmid, fid])
        with open(os.path.join(vulns_dir, fname), "w") as f:
            f.write(dout)
        record(rel, derr, drc)
        produced.append((row, rel))

    # --- INDEX.md ---
    total = len(rows)
    flagged = sum(1 for r in rows if r.get("needs_operator_decision"))
    status_counts = {}
    for r in rows:
        status_counts[r.get("status")] = status_counts.get(r.get("status"), 0) + 1

    tzname = _dt.datetime.now().astimezone().tzname() or "local"
    idx = []
    idx.append("# Range report bundle\n")
    idx.append(f"- **Workspace:** `{ws}`")
    idx.append(f"- **Generated:** {stamp} ({tzname})")
    idx.append(f"- **Proven vulnerabilities:** {total}")
    idx.append(f"- **Flagged `needs_operator_decision` (⚑):** {flagged}")
    if status_counts:
        by = ", ".join(f"{k}={v}" for k, v in sorted(status_counts.items()))
        idx.append(f"- **By status:** {by}")
    if errors:
        idx.append(f"- **Tool errors:** {len(errors)} — see `_errors.log`")
    idx.append("\n## Reports\n")
    idx.append("| File | Contents |")
    idx.append("|---|---|")
    idx.append("| `status_report.txt` | overall sweep status (coverage, funnel, deployment-decision axis, timing, integrity) |")
    idx.append("| `vuln_list.txt` | every proven vulnerability, grouped by status |")
    idx.append("| `vuln_list_needs_decision.txt` | only the ⚑ findings — deploying the proven fix is the operator's call |")
    idx.append("| `vuln_list.json` | machine-readable manifest (the per-finding dossiers are enumerated from this) |")
    idx.append(f"| `vulns/` | one full dossier per proven vulnerability ({total} file{'s' if total != 1 else ''}) |")
    if args.json:
        idx.append("| `status_report.json`, `vuln_list_needs_decision.json` | JSON forms (via --json) |")

    idx.append("\n## Vulnerabilities\n")
    if produced:
        idx.append("| ⚑ | vmid | host | category | status | finding | dossier |")
        idx.append("|---|---|---|---|---|---|---|")
        for row, rel in sorted(produced, key=lambda rr: (str(rr[0].get("golden_vmid")),
                                                         str(rr[0].get("finding_id")))):
            flag = "⚑" if row.get("needs_operator_decision") else ""
            idx.append("| {f} | {v} | {h} | {c} | {s} | `{fid}` | [`{rel}`]({rel}) |".format(
                f=flag, v=row.get("golden_vmid", "?"), h=row.get("host", "?"),
                c=row.get("category", "?"), s=row.get("status", "?"),
                fid=row.get("finding_id", "?"), rel=rel))
    else:
        idx.append("_No proven vulnerabilities in this sweep._")
    idx.append("")
    emit("index", "\n".join(idx), "INDEX.md")

    # --- error log ---
    if errors:
        lines = ["Tool calls that failed or wrote to stderr:\n"]
        for label, msg in errors:
            lines.append(f"### {label}\n{msg}\n")
        emit("errors", "\n".join(lines), "_errors.log")

    # --- summary to stdout ---
    print(f"Report bundle written to {out}")
    print(f"  status_report.txt")
    print(f"  vuln_list.txt")
    print(f"  vuln_list_needs_decision.txt")
    print(f"  vuln_list.json")
    print(f"  vulns/  ({total} dossier{'s' if total != 1 else ''})")
    print(f"  INDEX.md")
    if errors:
        print(f"  _errors.log  ({len(errors)} tool call(s) had problems)")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
