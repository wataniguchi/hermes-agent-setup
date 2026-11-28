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

ONE SSH CONNECTION PER HOST (added after a real failure): the audit used
to open a brand-new SSH connection for the probe and for every check — on
Windows that's ~9 fresh TCP/auth/banner handshakes in a tight loop, which
tripped Windows OpenSSH's anti-brute-force throttle ("Connection timed out
during banner exchange") and failed the local audit of at least one host
partway through, leaving it `audit_failed` even though it was
reachable and had no real local findings. The checks now share a SINGLE
SSH connection via OpenSSH connection multiplexing (ControlMaster): the
probe opens the master, every check rides it as a channel, so the whole
audit looks like one connection to the target's sshd. PowerShell checks
are additionally sent base64-encoded (-EncodedCommand), which removes all
dependence on fragile remote-shell quoting (the old double-quote wrapper
corrupted any check whose script embedded a literal `"`, e.g.
always_install_elevated).

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
                         "output": "...", "item": "..." | null,
                         "truncated": bool}],
          "errors": ["per-check failures that didn't abort the audit"]}

PER-ITEM SPLITTING: a category whose raw output genuinely bundles
several independently-actionable things (e.g. `sudoers` capturing four
separate NOPASSWD grants in one blob, `listening_sockets` capturing
every open port in one blob) previously became exactly ONE finding per
host per category — making selective remediation of just one grant or
one socket impossible, and making range_traversal.py's finding_id()
(hashed on category+command) collide across genuinely distinct items.
SPLITTERS/WINDOWS_SPLITTERS below opt specific categories into emitting
one finding per parsed item instead (each carrying that item's own text
in the new "item" field, used both for display and for a stable,
item-specific finding_id downstream). This is deliberately NOT applied
to every category: a splitter is only as safe as the output format it
assumes, and a wrong split (silently merging or fabricating item
boundaries) is worse than staying bundled. Categories left unsplit here
(os_release, packages_dpkg/rpm, sensitive_file_perms, cron_system,
systeminfo, hotfixes, service_binary_paths, always_install_elevated)
are either pure inventory, not individually actionable, or have output
too heterogeneous to split with a one-line-per-item heuristic — if a
real audit run shows a specific one of these would clearly benefit,
extend SPLITTERS/WINDOWS_SPLITTERS for it, don't force a generic parser
across the board.

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
import base64
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile

MAX_OUTPUT_CHARS = 20000
SSH_USER = {"Linux": "root", "Windows": "Administrator"}
SSH_OPTS = [
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=10",
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "LogLevel=ERROR",
]


def _ps_encode(script: str) -> str:
    """PowerShell -EncodedCommand payload: base64 of the UTF-16LE script.
    The result is pure [A-Za-z0-9+/=] — no spaces, no quotes — so it
    survives any remote-shell quoting untouched. This is the robust way
    to carry a PowerShell script containing quotes, $vars or pipes over
    SSH, and it is what finally fixes the always_install_elevated /
    service_binary_paths mangling the old double-quote wrapper caused."""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _maybe_encode_powershell(argv: list) -> list:
    """Rewrite `powershell … -Command <script>` to
    `powershell … -EncodedCommand <base64>`. cmd.exe checks and any
    non-PowerShell argv are returned unchanged. Returns a NEW list; the
    caller's argv (used for the human-readable `command` field in the
    finding) is left intact."""
    if argv and argv[0] in ("powershell", "pwsh") and "-Command" in argv:
        i = argv.index("-Command")
        script = argv[i + 1] if i + 1 < len(argv) else ""
        # Suppress PowerShell's progress stream ("Preparing modules for first
        # use"), which over SSH is serialized as CLIXML onto stderr and would
        # otherwise be merged into the finding output. The real command output
        # is unaffected; genuine error records still surface.
        script = "$ProgressPreference='SilentlyContinue'; " + script
        return argv[:i] + ["-EncodedCommand", _ps_encode(script)]
    return argv


def ssh_exec(target: str, os_name: str, argv: list, identity: str | None,
             timeout: int, control_path: str | None = None) -> dict:
    """Runs argv on `target` over SSH as root (Linux) / Administrator
    (Windows). Returns {"exitcode", "stdout", "stderr"} — the same
    shape the old guest-agent exec call returned, so the rest of this
    script didn't need to change. Raises RuntimeError if SSH itself
    couldn't connect or authenticate (distinct from the remote COMMAND
    failing, which is a normal per-check result, not an error here).

    When `control_path` is set, every call for one host shares a single
    SSH connection (OpenSSH ControlMaster multiplexing): the first call
    opens the master, the rest ride it as channels — no new
    TCP/auth/banner handshake per check. That is what stops an N-check
    audit from looking like an N-connection burst to the target's sshd,
    which on Windows OpenSSH tripped an anti-brute-force throttle
    ("Connection timed out during banner exchange") and failed the audit
    partway through.

    NOTE: ssh's own convention is to exit 255 when the ssh client
    itself fails (no route, auth failure, etc.) — but a remote command
    that legitimately exits 255 on its own is indistinguishable from
    that by the exit code alone. None of the checks below are expected
    to do that; flagging it here rather than pretending it can't
    happen.
    """
    user = SSH_USER[os_name]
    if os_name == "Windows":
        # PowerShell checks go out base64-encoded (-EncodedCommand), so
        # embedded quotes / $vars can't be corrupted by remote-shell
        # quoting. Only the cmd.exe checks still use the simple wrapper
        # below, and none of those embed a literal double quote.
        send_argv = _maybe_encode_powershell(argv)
        remote_cmd = " ".join(f'"{a}"' if " " in a else a for a in send_argv)
    else:
        remote_cmd = shlex.join(argv)

    ssh_argv = ["ssh"] + SSH_OPTS
    if control_path:
        # Reuse ONE SSH connection for every check on this host instead of
        # a fresh handshake per check — the burst that tripped Windows
        # OpenSSH's throttle. ControlPersist keeps the master briefly alive
        # between calls; _close_master() tears it down at the end.
        ssh_argv += ["-o", "ControlMaster=auto",
                     "-o", f"ControlPath={control_path}",
                     "-o", "ControlPersist=60s"]
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


def _close_master(target: str, os_name: str, identity: str | None, control_path: str) -> None:
    """Best-effort teardown of the shared SSH master connection. Safe to
    call even if no master was ever established (e.g. the probe failed)."""
    user = SSH_USER[os_name]
    argv = ["ssh", "-o", f"ControlPath={control_path}"]
    if identity:
        argv += ["-i", identity]
    argv += ["-O", "exit", f"{user}@{target}"]
    try:
        subprocess.run(argv, capture_output=True, text=True, timeout=15)
    except Exception:
        pass


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
    ("privileged_group_members",
     ["sh", "-c",
      'for g in docker lxd lxc disk shadow; do '
      'line=$(getent group "$g" 2>/dev/null) || continue; '
      'members=${line##*:}; '
      '[ -n "$members" ] && printf "%s: %s\\n" "$g" "$members"; done'],
     "membership in root-equivalent groups (docker/lxd/lxc/disk/shadow) — each non-root "
     "member can typically escalate to root WITHOUT sudo and independent of any sudoers "
     "policy (mount the host fs via a container, raw block-device access, or read "
     "/etc/shadow). Found necessary for real: on one host the cloud-init default account "
     "had its NOPASSWD sudo grant removed and marked closed, while its docker-group "
     "membership — a second, equivalent root path the PoC itself had noted — went "
     "untracked because docker.service is inventory, not a risk finding."),
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


def _nonblank(lines):
    return [l for l in lines if l.strip()]


def split_find_paths(output: str) -> list[str]:
    """One finding per path line from `find`. Drops find's own
    stderr-merged noise ("find: '/proc/...': No such file or
    directory") rather than treating it as a real path."""
    return [l for l in _nonblank(output.splitlines()) if not l.strip().startswith("find:")]


def split_sudoers(output: str) -> list[str]:
    """One finding per actual sudo rule line — drops comments/blanks
    from the concatenated sudoers + sudoers.d output."""
    return [l for l in _nonblank(output.splitlines()) if not l.strip().startswith("#")]


def split_listening_sockets(output: str) -> list[str]:
    """One finding per socket line from `ss -tlnp`/`netstat -tlnp` —
    drops whichever tool's own header line is present."""
    items = []
    for l in _nonblank(output.splitlines()):
        if l.strip().startswith(("State", "Active Internet", "Proto ")):
            continue
        items.append(l)
    return items


def split_grep_lines(output: str) -> list[str]:
    """One finding per matched line — already one account per line for
    the shell_users check."""
    return _nonblank(output.splitlines())


def split_cron_user(output: str) -> list[str]:
    """cron_user's command prints '== <user> ==' markers between each
    user's crontab. Attributes each non-empty entry to the user whose
    block it fell under, so a splitter restart doesn't lose that
    context."""
    items = []
    current_user = None
    for l in _nonblank(output.splitlines()):
        s = l.strip()
        if s.startswith("==") and s.endswith("=="):
            current_user = s.strip("= ").strip()
            continue
        items.append(f"[{current_user}] {l}" if current_user else l)
    return items


def split_group_members(output: str) -> list[str]:
    """One finding per (dangerous group, member). The check prints one
    'group: m1,m2,...' line per non-empty root-equivalent group; expand to
    one item per member so each membership is independently actionable
    (its own finding_id, its own remediation = remove that user from that
    group). `root` is skipped — root already being in a root group is not
    a finding."""
    items = []
    for l in _nonblank(output.splitlines()):
        group, sep, members = l.partition(":")
        if not sep:
            continue
        group = group.strip()
        for m in members.split(","):
            m = m.strip()
            if m and m != "root":
                items.append(f"{group}: {m}")
    return items


def split_systemctl_units(output: str) -> list[str]:
    """One finding per unit line from `systemctl list-units` — keeps
    only lines whose first token looks like a real unit name
    (name.service/.socket/.timer/.mount/.target), which skips the
    header row and the trailing 'N loaded units listed.' line without
    needing to assume a fixed column count. Conservative on purpose: if
    the fallback `service --status-all` form is in use instead (no
    unit-suffixed first token), this returns nothing and the category
    stays bundled rather than guessing at that format's shape."""
    items = []
    for l in _nonblank(output.splitlines()):
        first = l.split()[0] if l.split() else ""
        if first.endswith((".service", ".socket", ".timer", ".mount", ".target")):
            items.append(l)
    return items


def split_table_rows(output: str) -> list[str]:
    """One finding per data row from a PowerShell `Select-Object ... |
    Out-String` table: header line, then a dashes line, then one row
    per remaining line. Assumes no cell wraps (Out-String -Width 300
    makes that unlikely for this check list's column widths) — if a
    future check's values are long enough to wrap, this will
    under-split rather than fabricate a boundary, which is the safer
    failure direction."""
    lines = _nonblank(output.splitlines())
    return lines[2:] if len(lines) > 2 else []


# A category is either a RISK condition — something an unprivileged
# actor might actually abuse, so it's worth the PoC/remediate/reverify
# cycle — or INVENTORY: host facts that are genuinely useful as context
# but are NOT, on their own, an exploitable finding. Both are still
# recorded and searchable; the difference is downstream, in
# range_traversal.py's record-audit: a risk item becomes `discovered`
# (enters the PoC cycle), an inventory item becomes `informational`
# (recorded, never auto-assigned a PoC). The agent can still escalate a
# specific inventory item it judges exploitable — an old kernel, a
# vulnerable package version — with range_traversal.py's `escalate`
# command; it just doesn't happen by default for every listening port
# and running service.
#
# Confirmed why this matters (2026-11-24): on one host, 8 of 10
# `poc_confirmed` results were inventory restated as "confirmed the
# condition exists" (sshd listening, an account having a shell) — each
# then cost a full remediate turn and a reverify turn on the slowest
# step of the loop, and the sshd ones risked the agent cutting its own
# SSH access mid-host. Inventory is context, not a finding to "fix."
#
# To retune, move a category between risk and inventory here — this one
# dict is the whole control surface. Anything NOT listed is treated as
# inventory (the safe default: a new check doesn't silently create PoC
# work until you deliberately mark it risk).
RISK_CATEGORIES = {
    # Linux — each is a concrete local-privesc primitive an unprivileged
    # user could try to turn into elevated access.
    "suid_binaries", "sgid_binaries",
    "world_writable_files", "world_writable_dirs",
    "sudoers", "privileged_group_members", "cron_system", "cron_user",
    # Windows
    "service_binary_paths",       # unquoted/writable service paths
    "always_install_elevated",    # any user can install an MSI as SYSTEM
}
# Treated as inventory (NOT in the set above), for reference:
#   Linux:   os_release, kernel_version, packages_dpkg, packages_rpm,
#            listening_sockets, running_services, shell_users,
#            sensitive_file_perms
#   Windows: systeminfo, hotfixes, running_services, listening_ports,
#            scheduled_tasks, local_admins


def finding_kind(category: str) -> str:
    return "risk" if category in RISK_CATEGORIES else "inventory"


# Per-category splitter, Linux checks. Absent from this dict == stays
# one bundled finding per host per category, same as before.
SPLITTERS = {
    "suid_binaries": split_find_paths,
    "sgid_binaries": split_find_paths,
    "world_writable_files": split_find_paths,
    "world_writable_dirs": split_find_paths,
    "sudoers": split_sudoers,
    "privileged_group_members": split_group_members,
    "listening_sockets": split_listening_sockets,
    "shell_users": split_grep_lines,
    "cron_user": split_cron_user,
    "running_services": split_systemctl_units,
}

# Same idea, Windows checks — kept as a separate dict since "running_services"
# means a different command/format on this side.
WINDOWS_SPLITTERS = {
    "running_services": split_table_rows,
    "listening_ports": split_table_rows,
    "scheduled_tasks": split_table_rows,
    "local_admins": split_table_rows,
}


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

    # One shared SSH connection for the whole host: the probe opens the
    # master, every check rides it (see ssh_exec's control_path note). The
    # try/finally guarantees the master is torn down on every exit path.
    control_dir = tempfile.mkdtemp(prefix="range-ssh-")
    control_path = os.path.join(control_dir, "cm.sock")
    try:
        try:
            probe = ssh_exec(args.target, args.os, probe_cmd, args.ssh_identity,
                             args.timeout, control_path)
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

        splitters = SPLITTERS if args.os == "Linux" else WINDOWS_SPLITTERS

        findings = []
        errors = []
        for category, argv, description in checks:
            try:
                result = ssh_exec(args.target, args.os, argv, args.ssh_identity,
                                  args.timeout, control_path)
            except RuntimeError as e:
                errors.append(f"{category}: {e}")
                continue
            except Exception as e:
                errors.append(f"{category}: unexpected error: {e}")
                continue

            output, truncated = truncate(result["stdout"] + result["stderr"])

            items = []
            splitter = splitters.get(category)
            if splitter:
                try:
                    items = splitter(output)
                except Exception as e:
                    errors.append(f"{category}: splitter failed ({e}) — falling back to one bundled finding")
                    items = []

            kind = finding_kind(category)
            if items:
                for item_text in items:
                    item_display, item_truncated = truncate(item_text)
                    findings.append({
                        "category": category,
                        "kind": kind,
                        "description": description,
                        "command": " ".join(argv),
                        "exit_code": result["exitcode"],
                        "output": item_display,
                        "item": item_text,
                        "truncated": item_truncated,
                    })
            else:
                findings.append({
                    "category": category,
                    "kind": kind,
                    "description": description,
                    "command": " ".join(argv),
                    "exit_code": result["exitcode"],
                    "output": output,
                    "item": None,
                    "truncated": truncated,
                })

        print(json.dumps({
            "working_vmid": args.working_vmid, "reachable": True, "os": args.os,
            "findings": findings, "errors": errors,
        }, indent=2))
    finally:
        _close_master(args.target, args.os, args.ssh_identity, control_path)
        shutil.rmtree(control_dir, ignore_errors=True)


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
