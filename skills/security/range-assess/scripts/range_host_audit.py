#!/usr/bin/env python3
"""
range_host_audit.py — local-vantage-point enumeration of ONE working
clone over SSH (as `root` on Linux, `Administrator` on Windows — NOT
proxmox_bridge.py's guest-exec channel; see "WHY SSH, NOT GUEST-EXEC"
below). Complements range_discover.py's external network scan with
what can only be seen (or meaningfully checked) from inside the guest:
exact locally-installed package/kernel versions, SUID/SGID binaries,
world-writable files/dirs, cron/sudoers entries, loopback-only listening
sockets an external scan can't see, and a handful of classic Windows
local-privesc indicators.

STATELESS, like range_discover.py and ksnctf_discover.py — it only
reports what it finds; range_traversal.py (via its `record-audit`
subcommand) decides what to do with the result.

WHY SSH, NOT GUEST-EXEC: this originally ran over proxmox_bridge.py's
guest-exec channel (QEMU Guest Agent, virtio-serial). That turned out
to need `agent: 1` set on the golden image's own VM config, which was
absent on every golden image checked so far (9101-9103) — "No QEMU
guest agent configured" is Proxmox failing fast because the
virtio-serial device for the agent doesn't exist on the VM at all, not
the daemon being unreachable inside the guest. Rather than add that
config to every golden image, the operator switched this skill to a
different standing channel instead: the `hermes` sandbox's own SSH
keypair, authorized on every target host for `root` (Linux) /
`Administrator` (Windows), reachable now that the sandbox has a real
network route to the range (see this skill's own history of that
networking fix). This script's command-execution shape changed; the
privilege model (full root/Administrator access) and everything it
implies for the PRIVILEGE CAVEAT below did not.

PRIVILEGE CAVEAT — read this before trusting any output. This script
connects as `root` (Linux) / `Administrator` (Windows), so it sees
EVERYTHING on the guest, not what an actual unprivileged local user
could see. That's the whole point of running it this way: every check
below exists specifically to surface conditions an unprivileged user
(or a remote attacker who has already landed a low-privilege shell)
could abuse — a world-writable file a root-owned cron job reads, a
SUID binary, a NOPASSWD sudo rule, a service running from a writable or
unquoted path. The privileged vantage point is what lets this script
SEE those conditions in one pass; it is not itself evidence that an
unprivileged user has already exploited one. That demonstration is
`range_poc.py`'s job (specifically its `--scope local` path,
prompts/poc_task_prompt_local.md), which reasons about and attempts to
actually reproduce the finding from as close to an unprivileged actor's
position as the check in question allows.

Confirmed running against real hosts in the range over SSH — a live
sweep is producing real findings from the checks below. Every check is
read-only enumeration: find/ls/cat of
metadata, dpkg/rpm queries, systemctl/ss listings, PowerShell Get-*
cmdlets. Nothing here modifies guest state or attempts exploitation —
that boundary is deliberate, same as every other script in this skill.
The check list is a non-exhaustive starting baseline, not a claim of
completeness — see SKILL.md for how to extend it; the LINUX_CHECKS /
WINDOWS_CHECKS lists below are the one place to add more.

Usage:
    python3 range_host_audit.py audit --golden-vmid 9001 --working-vmid <clone vmid> --os Linux --target <ip>
    python3 range_host_audit.py audit --golden-vmid 9001 --working-vmid <clone vmid> --os Windows --target <ip> --timeout 300

Outputs: {"working_vmid": N, "reachable": bool, "os": "...",
          "findings": [{"category": "...", "description": "...",
                         "command": "...", "exit_code": N,
                         "output": "...", "truncated": bool}],
          "errors": ["per-check failures that didn't abort the audit"]}

Pass --ssh-identity to use a non-default private key; otherwise `ssh`
resolves its own default identity (e.g. ~/.ssh/id_ed25519) the normal
way.

HOST-KEY CHECKING IS DELIBERATELY DISABLED (StrictHostKeyChecking=no,
UserKnownHostsFile=/dev/null) — this range is private and
operator-controlled, and every target here is a disposable clone this
same skill destroys and re-creates routinely, so host keys churn by
design, not by attack. Do not reuse this setting against anything you
don't fully control end-to-end.
"""
import argparse
import json
import shlex
import subprocess
import sys

MAX_OUTPUT_CHARS = 20000
SSH_USER = {"Linux": "root", "Windows": "Administrator"}
SSH_OPTS = [
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=10",
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "LogLevel=ERROR",
]


def ssh_exec(target: str, os_name: str, argv: list, identity: str | None, timeout: int) -> dict:
    """Runs argv on `target` over SSH as root (Linux) / Administrator
    (Windows). Returns {"exitcode", "stdout", "stderr"} — the same
    shape the old guest-agent exec call returned, so the rest of this
    script didn't need to change. Raises RuntimeError if SSH itself
    couldn't connect or authenticate (distinct from the remote COMMAND
    failing, which is a normal per-check result, not an error here).

    NOTE: ssh's own convention is to exit 255 when the ssh client
    itself fails (no route, auth failure, etc.) — but a remote command
    that legitimately exits 255 on its own is indistinguishable from
    that by the exit code alone. None of the checks below are expected
    to do that; flagging it here rather than pretending it can't
    happen.
    """
    user = SSH_USER[os_name]
    if os_name == "Windows":
        # Windows OpenSSH's default remote shell is cmd.exe, whose
        # quoting rules aren't POSIX. None of WINDOWS_CHECKS below embed
        # a literal double quote, so wrapping any multi-word argv
        # element in double quotes is safe for the current check list —
        # it is NOT a general cmd.exe-quoting solution; a future check
        # containing an embedded " will need more care here.
        remote_cmd = " ".join(f'"{a}"' if " " in a else a for a in argv)
    else:
        remote_cmd = shlex.join(argv)

    ssh_argv = ["ssh"] + SSH_OPTS
    if identity:
        ssh_argv += ["-i", identity]
    ssh_argv += [f"{user}@{target}", remote_cmd]

    try:
        proc = subprocess.run(ssh_argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"ssh to {user}@{target} timed out after {timeout}s")
    except FileNotFoundError:
        raise RuntimeError("`ssh` not found on PATH in this environment")

    if proc.returncode == 255:
        raise RuntimeError(
            f"ssh to {user}@{target} failed at the connection/auth level (exit 255): {proc.stderr.strip()}"
        )
    return {"exitcode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


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
        probe = ssh_exec(args.target, args.os, probe_cmd, args.ssh_identity, args.timeout)
    except RuntimeError as e:
        print(json.dumps({
            "working_vmid": args.working_vmid, "reachable": False, "os": args.os, "findings": [],
            "errors": [
                f"{e} — this failure does not by itself mean the host is actually unreachable; "
                "a VM that's merely not started yet, or still booting, looks identical from here."
            ],
        }, indent=2))
        return

    if probe["exitcode"] != 0:
        print(json.dumps({
            "working_vmid": args.working_vmid, "reachable": False, "os": args.os, "findings": [],
            "errors": [f"SSH connected but the probe command failed (exitcode {probe['exitcode']}): "
                       f"{probe['stderr']}"],
        }, indent=2))
        return

    findings = []
    errors = []
    for category, argv, description in checks:
        try:
            result = ssh_exec(args.target, args.os, argv, args.ssh_identity, args.timeout)
        except RuntimeError as e:
            errors.append(f"{category}: {e}")
            continue
        except Exception as e:
            errors.append(f"{category}: unexpected error: {e}")
            continue

        output, truncated = truncate(result["stdout"] + result["stderr"])
        findings.append({
            "category": category,
            "description": description,
            "command": " ".join(argv),
            "exit_code": result["exitcode"],
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
    p_audit.add_argument("--target", required=True, help="working clone's IPv4/IPv6 address")
    p_audit.add_argument("--ssh-identity", default=None, help="private key path; default: ssh's own default identity")
    p_audit.add_argument("--timeout", type=int, default=330, help="per-command SSH timeout, seconds")

    args = parser.parse_args()
    if args.command == "audit":
        cmd_audit(args)


if __name__ == "__main__":
    main()
