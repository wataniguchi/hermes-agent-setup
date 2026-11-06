#!/usr/bin/env python3
"""
range_host_audit.py — local-vantage-point enumeration of ONE working
clone via Proxmox's QEMU guest-exec channel (proxmox_bridge.py's
POST /vm/{vmid}/exec — virtio-serial, NOT the guest's own network
interface). Complements range_discover.py's external network scan with
what can only be seen (or meaningfully checked) from inside the guest:
exact locally-installed package/kernel versions, SUID/SGID binaries,
world-writable files/dirs, cron/sudoers entries, loopback-only listening
sockets an external scan can't see, and a handful of classic Windows
local-privesc indicators.

STATELESS, like range_discover.py and ksnctf_discover.py — it only
reports what it finds; range_traversal.py (via its `record-audit`
subcommand) decides what to do with the result.

PRIVILEGE CAVEAT — read this before trusting any output. `qm guest
exec` runs through the QEMU Guest Agent, which executes as the guest's
own privileged service account: root on Linux, SYSTEM on Windows. This
script therefore sees EVERYTHING on the guest, not what an actual
unprivileged local user could see. That's the whole point of running
it this way: every check below exists specifically to surface
conditions an unprivileged user (or a remote attacker who has already
landed a low-privilege shell) could abuse — a world-writable file a
root-owned cron job reads, a SUID binary, a NOPASSWD sudo rule, a
service running from a writable or unquoted path. The privileged
vantage point is what lets this script SEE those conditions in one
pass; it is not itself evidence that an unprivileged user has already
exploited one. That demonstration is `range_poc.py`'s job (specifically
its `--scope local` path, prompts/poc_task_prompt_local.md), which
reasons about and attempts to actually reproduce the finding from as
close to an unprivileged actor's position as the check in question
allows.

NEW, UNTESTED CODE — nothing here has been run against a real host in
the range yet. Every check is read-only enumeration: find/ls/cat of
metadata, dpkg/rpm queries, systemctl/ss listings, PowerShell Get-*
cmdlets. Nothing here modifies guest state or attempts exploitation —
that boundary is deliberate, same as every other script in this skill.
The check list is a non-exhaustive starting baseline, not a claim of
completeness — see SKILL.md for how to extend it; the LINUX_CHECKS /
WINDOWS_CHECKS lists below are the one place to add more.

Usage:
    python3 range_host_audit.py audit --golden-vmid 9001 --working-vmid <clone vmid> --os Linux
    python3 range_host_audit.py audit --golden-vmid 9001 --working-vmid <clone vmid> --os Windows --timeout 300

Outputs: {"working_vmid": N, "reachable": bool, "os": "...",
          "findings": [{"category": "...", "description": "...",
                         "command": "...", "exit_code": N,
                         "output": "...", "truncated": bool}],
          "errors": ["per-check failures that didn't abort the audit"]}

Set PROXMOX_BRIDGE_URL to override the default
http://host.docker.internal:8811 — same convention already used by
skills/security/windows-binary-analysis/scripts/analyze_windows_binary.py,
which this script's bridge-calling helper is deliberately copied from.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BRIDGE_URL = os.environ.get("PROXMOX_BRIDGE_URL", "http://host.docker.internal:8811")
MAX_OUTPUT_CHARS = 20000


def call(method: str, path: str, body: dict | None = None, params: dict | None = None, timeout: int = 330) -> dict:
    url = f"{BRIDGE_URL}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


# (category, argv, human-readable description of what this is looking for)
LINUX_CHECKS = [
    ("os_release", ["cat", "/etc/os-release"],
     "OS/distro identification"),
    ("kernel_version", ["uname", "-a"],
     "kernel version — cross-reference against known-vulnerable kernel CVEs"),
    ("packages_dpkg", ["dpkg-query", "-W", "-f=${Package} ${Version}\n"],
     "installed package versions (Debian/Ubuntu — expect failure on non-dpkg systems)"),
    ("packages_rpm", ["rpm", "-qa"],
     "installed package versions (RHEL/Fedora/etc — expect failure on non-rpm systems)"),
    ("suid_binaries", ["find", "/", "-xdev", "-perm", "-4000", "-type", "f"],
     "SUID binaries — each is a potential local-privesc vector if its behavior can be influenced by an unprivileged caller"),
    ("sgid_binaries", ["find", "/", "-xdev", "-perm", "-2000", "-type", "f"],
     "SGID binaries — same concern as SUID, scoped to group privilege"),
    ("world_writable_files",
     ["find", "/", "-xdev", "-type", "f", "-perm", "-0002", "-not", "-path", "/proc/*", "-not", "-path", "/sys/*"],
     "world-writable files — a problem if anything privileged reads or executes one"),
    ("world_writable_dirs",
     ["find", "/", "-xdev", "-type", "d", "-perm", "-0002",
      "-not", "-path", "/tmp*", "-not", "-path", "/var/tmp*", "-not", "-path", "/proc/*", "-not", "-path", "/sys/*"],
     "world-writable directories outside the expected /tmp, /var/tmp"),
    ("cron_system",
     ["sh", "-c", "cat /etc/crontab 2>/dev/null; ls -la /etc/cron.d /etc/cron.daily /etc/cron.hourly /etc/cron.weekly 2>/dev/null"],
     "system-wide cron entries and their script permissions"),
    ("cron_user",
     ["sh", "-c", 'for u in $(cut -f1 -d: /etc/passwd); do echo "== $u =="; crontab -l -u "$u" 2>/dev/null; done'],
     "per-user crontabs"),
    ("sudoers",
     ["sh", "-c", "cat /etc/sudoers 2>/dev/null; cat /etc/sudoers.d/* 2>/dev/null"],
     "sudo rules — especially NOPASSWD or overly broad command grants"),
    ("sensitive_file_perms",
     ["ls", "-la", "/etc/shadow", "/etc/passwd", "/etc/sudoers", "/etc/ssh/sshd_config"],
     "permissions on security-sensitive files (contents not read for /etc/shadow — perms only)"),
    ("listening_sockets",
     ["sh", "-c", "ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null"],
     "locally-bound listening sockets, including loopback-only services an external scan can't see"),
    ("running_services",
     ["sh", "-c", "systemctl list-units --type=service --state=running --no-pager 2>/dev/null || service --status-all 2>/dev/null"],
     "running services"),
    ("shell_users",
     ["sh", "-c", "grep -E '/(ba)?sh$' /etc/passwd"],
     "accounts with an interactive shell configured"),
]

WINDOWS_CHECKS = [
    ("systeminfo", ["cmd", "/c", "systeminfo"],
     "OS/build identification"),
    ("hotfixes",
     ["powershell", "-NoProfile", "-Command",
      "Get-HotFix | Select-Object HotFixID,InstalledOn | Format-Table -AutoSize | Out-String -Width 300"],
     "installed updates — for spotting an unpatched, known-vulnerable build"),
    ("running_services",
     ["powershell", "-NoProfile", "-Command",
      "Get-Service | Where-Object {$_.Status -eq 'Running'} | Select-Object Name,DisplayName | Out-String -Width 300"],
     "running services"),
    ("service_binary_paths",
     ["powershell", "-NoProfile", "-Command",
      "Get-CimInstance win32_service | Select-Object Name,PathName,StartName | Out-String -Width 300"],
     "service binary paths and run-as accounts — check for unquoted paths containing spaces "
     "(classic local-privesc) and world/user-writable binary locations"),
    ("listening_ports",
     ["powershell", "-NoProfile", "-Command",
      "Get-NetTCPConnection -State Listen | Select-Object LocalAddress,LocalPort,OwningProcess | Out-String -Width 300"],
     "locally-bound listening ports"),
    ("scheduled_tasks",
     ["powershell", "-NoProfile", "-Command",
      "Get-ScheduledTask | Where-Object {$_.State -ne 'Disabled'} | Select-Object TaskName,State,TaskPath | Out-String -Width 300"],
     "enabled scheduled tasks"),
    ("local_admins",
     ["powershell", "-NoProfile", "-Command", "Get-LocalGroupMember -Group Administrators | Out-String -Width 300"],
     "local Administrators group membership"),
    ("always_install_elevated",
     ["powershell", "-NoProfile", "-Command",
      "$m=Get-ItemProperty -Path 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\Installer' "
      "-Name AlwaysInstallElevated -ErrorAction SilentlyContinue; "
      "$u=Get-ItemProperty -Path 'HKCU:\\SOFTWARE\\Policies\\Microsoft\\Windows\\Installer' "
      "-Name AlwaysInstallElevated -ErrorAction SilentlyContinue; "
      "\"HKLM=$($m.AlwaysInstallElevated) HKCU=$($u.AlwaysInstallElevated)\""],
     "AlwaysInstallElevated policy — if both are 1, any local user can run an MSI package as SYSTEM"),
]


def truncate(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text, False
    return text[:MAX_OUTPUT_CHARS] + f"\n...[truncated, {len(text) - MAX_OUTPUT_CHARS} more chars]", True


def cmd_audit(args):
    if args.working_vmid == args.golden_vmid:
        print(json.dumps({
            "reachable": False,
            "error": (
                f"REFUSED: --working-vmid ({args.working_vmid}) is the same as "
                f"--golden-vmid ({args.golden_vmid}) — refusing to audit a golden "
                "image. Pass the actual working clone's VMID."
            ),
        }, indent=2))
        sys.exit(1)

    checks = LINUX_CHECKS if args.os == "Linux" else WINDOWS_CHECKS
    probe_cmd = ["sh", "-c", "true"] if args.os == "Linux" else ["cmd", "/c", "ver"]

    try:
        probe = call("POST", f"/vm/{args.working_vmid}/exec", {"command": probe_cmd}, timeout=args.timeout)
    except urllib.error.HTTPError as e:
        print(json.dumps({
            "working_vmid": args.working_vmid, "reachable": False, "os": args.os, "findings": [],
            "errors": [f"bridge error ({e.code}): {e.read().decode(errors='replace')}"],
        }, indent=2))
        return
    except urllib.error.URLError as e:
        print(json.dumps({
            "working_vmid": args.working_vmid, "reachable": False, "os": args.os, "findings": [],
            "errors": [
                f"could not reach bridge at {BRIDGE_URL}: {e.reason}. Confirm proxmox_bridge.py is "
                "running and reachable, and that the working clone is actually started with its "
                "guest agent responsive — this failure does not by itself mean the guest agent "
                "is down; a VM that's merely not started yet looks identical from here."
            ],
        }, indent=2))
        return

    if probe.get("exitcode") != 0:
        print(json.dumps({
            "working_vmid": args.working_vmid, "reachable": False, "os": args.os, "findings": [],
            "errors": [f"guest agent reachable but probe command failed (exitcode {probe.get('exitcode')}): "
                       f"{probe.get('stderr', '')}"],
        }, indent=2))
        return

    findings = []
    errors = []
    for category, argv, description in checks:
        try:
            result = call("POST", f"/vm/{args.working_vmid}/exec", {"command": argv}, timeout=args.timeout)
        except urllib.error.HTTPError as e:
            errors.append(f"{category}: bridge error ({e.code}): {e.read().decode(errors='replace')}")
            continue
        except urllib.error.URLError as e:
            errors.append(f"{category}: could not reach bridge: {e.reason}")
            continue
        except Exception as e:
            errors.append(f"{category}: unexpected error: {e}")
            continue

        output, truncated = truncate(result.get("stdout", "") + result.get("stderr", ""))
        findings.append({
            "category": category,
            "description": description,
            "command": " ".join(argv),
            "exit_code": result.get("exitcode"),
            "output": output,
            "truncated": truncated,
        })

    print(json.dumps({
        "working_vmid": args.working_vmid, "reachable": True, "os": args.os,
        "findings": findings, "errors": errors,
    }, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_audit = sub.add_parser("audit")
    p_audit.add_argument("--golden-vmid", type=int, required=True)
    p_audit.add_argument("--working-vmid", type=int, required=True)
    p_audit.add_argument("--os", choices=["Linux", "Windows"], required=True)
    p_audit.add_argument("--timeout", type=int, default=330, help="per-command exec timeout, seconds")

    args = parser.parse_args()
    if args.command == "audit":
        cmd_audit(args)


if __name__ == "__main__":
    main()
