#!/usr/bin/env python3
"""
Windows binary analysis lifecycle, wrapping the Proxmox bridge
(proxmox/proxmox_bridge.py) as a single CLI with four subcommands.

Runs inside the hermes-sandbox Docker container; reaches the bridge on the
Mac Studio host via host.docker.internal, which is only reachable this way
BECAUSE the bridge binds to 127.0.0.1 on the host, not 0.0.0.0 — Docker's
host.docker.internal routes container traffic to the host's loopback
specifically for this pattern.

Deliberately clone-once, exec-many: a full clone from the SMR-backed golden
template takes 30-60+ minutes, so an analysis session pushes one sample,
starts one VM, and runs many exec/pull calls against that same vmid before
a single destroy at the end. Never clone per-command.

Concurrent access from multiple Hermes subagents against the SAME vmid is
safe: each /vm/{vmid}/exec call spawns an independent QEMU Guest Agent
process with its own pid, and the bridge (uvicorn) runs each request in its
own thread, so one subagent's polling loop doesn't block another's. No
shared mutable state in this script serializes concurrent calls against one
vmid.

KNOWN LIMITATION: the vmid picked by `start` when --vmid isn't given
(9100 + time.time() % 400) has no existence check, so two DIFFERENT
sessions (not subagents sharing one vmid -- that's fine per above) started
around the same moment could collide on vmid. Not currently guarded against.
If running multiple independent analysis sessions concurrently, pass
distinct --vmid values explicitly rather than relying on the default.

Subcommands:
  start   <local_sample_path> [--vmid N]        -> clone, start, wait for
                                                     guest agent, push sample,
                                                     print {"vmid":N,"guest_path":...}
  exec    <vmid> -- <command> [args...]          -> run one command, print
                                                     {"exitcode":N,"stdout":...,"stderr":...}
  pull    <vmid> <guest_path> <local_out_path>   -> retrieve a file, decoding
                                                     correctly for both text
                                                     and binary content
  destroy <vmid>                                 -> stop + destroy

Examples:
  analyze_windows_binary.py start /workspace/samples/chall.exe
  analyze_windows_binary.py exec 9142 -- cmd /c "certutil -hashfile C:\\Samples\\chall.exe SHA256"
  analyze_windows_binary.py exec 9142 -- C:\\Tools\\x64dbg\\release\\x64\\x64dbg.exe C:\\Samples\\chall.exe
  analyze_windows_binary.py pull 9142 "C:\\Samples\\output.txt" /workspace/output.txt
  analyze_windows_binary.py destroy 9142
"""

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

BRIDGE_URL = os.environ.get("PROXMOX_BRIDGE_URL", "http://host.docker.internal:8811")
TEMPLATE_VMID = int(os.environ.get("PVE_TEMPLATE_VMID", "9000"))
GUEST_SAMPLE_DIR = "C:\\Samples"
# Tracks the active session's vmid so a second `start` call (whether from a
# confused primary, a subagent that didn't get the memo, or the model
# simply retrying) reuses the existing VM instead of cloning a second one.
# This has happened in practice despite explicit prompt/SKILL.md
# instructions not to — enforcing it here doesn't depend on the model
# reliably following that instruction. /workspace persists across
# container recreations (real host bind mount), so this survives even a
# fresh sandbox container.
SESSION_MARKER_PATH = "/workspace/.windows_analysis_session.json"


import urllib.parse

def call(method: str, path: str, body: dict | None = None, params: dict | None = None) -> dict:
    url = f"{BRIDGE_URL}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=7500) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        print(f"Bridge error ({e.code}): {detail}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(
            f"Could not reach bridge at {BRIDGE_URL}: {e.reason}\n"
            "Confirm the bridge is running on the Mac Studio host "
            "(scripts/start-proxmox-bridge.sh) and reachable via "
            "host.docker.internal from inside this container.",
            file=sys.stderr,
        )
        sys.exit(1)


def wait_for_guest(vmid: int, timeout: int = 600) -> None:
    """Windows needs real boot time after start before the guest agent
    responds. Poll with a trivial command rather than assuming a fixed delay."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            result = call("POST", f"/vm/{vmid}/exec", {"command": ["cmd", "/c", "ver"]})
            if result.get("exitcode") == 0:
                return
        except SystemExit:
            pass
        time.sleep(5)
    print(f"Guest agent on vmid {vmid} did not become ready within {timeout}s", file=sys.stderr)
    sys.exit(1)


# Proxmox's agent/file-write has a hardcoded 61440-byte limit on the content
# parameter (confirmed on Proxmox's own support forum — not something this
# script or the bridge can raise). Base64 expands size by ~4/3, so the safe
# raw-byte ceiling for the direct path is meaningfully below 61440.
DIRECT_WRITE_MAX_BYTES = 40000


def verify_file_exists(vmid: int, guest_path: str) -> bool:
    result = call("POST", f"/vm/{vmid}/exec", {"command": ["cmd", "/c", "dir", guest_path]})
    return result.get("exitcode") == 0


def push_sample(vmid: int, sample_path: str) -> str:
    if not os.path.isfile(sample_path):
        print(f"No such file: {sample_path}", file=sys.stderr)
        sys.exit(1)

    basename = os.path.basename(sample_path)
    guest_path = f"{GUEST_SAMPLE_DIR}\\{basename}"
    call("POST", f"/vm/{vmid}/exec", {"command": ["cmd", "/c", "mkdir", GUEST_SAMPLE_DIR]})

    size = os.path.getsize(sample_path)
    if size <= DIRECT_WRITE_MAX_BYTES:
        with open(sample_path, "rb") as f:
            content_b64 = base64.b64encode(f.read()).decode()
        call("POST", f"/vm/{vmid}/file", {"path": guest_path, "content_base64": content_b64})
        if not verify_file_exists(vmid, guest_path):
            print(
                f"ERROR: direct write reported success but {guest_path} does not exist "
                "on the guest afterward. Not proceeding — this file transfer failed.",
                file=sys.stderr,
            )
            sys.exit(1)
        return guest_path

    # Large file: route around agent/file-write via the ISO-attach path.
    # This script runs inside the sandbox container and CAN read
    # sample_path directly — read it here and send the bytes to the
    # bridge, rather than a path (the bridge runs on the Mac Studio host
    # and has no visibility into the container's filesystem;
    # host.docker.internal is a network route, not a filesystem bridge).
    print(
        f"{basename} is {size} bytes, over the {DIRECT_WRITE_MAX_BYTES}-byte direct-write "
        "limit — using ISO attach instead ...",
        file=sys.stderr,
    )
    with open(sample_path, "rb") as f:
        content_b64 = base64.b64encode(f.read()).decode()
    upload_result = call("POST", "/storage/upload", {"filename": basename, "content_base64": content_b64})
    volid = upload_result["volid"]

    # Query CD-ROM drive letters BEFORE attaching, so we can identify OUR
    # drive by what's NEW afterward rather than guessing by position. This
    # matters because the golden template has a leftover virtio-win ISO
    # still attached (ide3) from template build, occupying its own CD-ROM
    # drive letter — with two+ CD-ROMs present, "take the first one wmic
    # lists" is not reliable and was confirmed to silently pick the WRONG
    # drive (the old virtio-win ISO, not the payload just attached).
    def get_cdrom_letters():
        result = call(
            "POST", f"/vm/{vmid}/exec",
            {"command": ["cmd", "/c", "wmic", "logicaldisk", "where", "drivetype=5", "get", "deviceid"]},
        )
        letters = set()
        for line in result.get("stdout", "").splitlines():
            line = line.strip()
            if line and line != "DeviceID" and ":" in line:
                letters.add(line)
        return letters

    call("POST", f"/vm/{vmid}/cdrom", {"volid": volid, "ide_slot": "ide3"})

    # ide3 already exists as a device on every clone (inherited from the
    # golden template's virtio-win reference) — swapping ITS media is
    # confirmed instant and reliable, unlike attaching a brand-new device
    # (ide2, which we deliberately removed before the golden template's
    # final snapshot) — QEMU does not hotplug new IDE devices into a
    # running VM at all, confirmed by direct manual testing: a new ide2
    # device never became visible no matter how long we waited, while
    # swapping ide3's media was visible immediately, every time.
    time.sleep(3)  # brief pause for the guest to notice the media change

    # ide3's drive letter is stable across the VM's lifetime (assigned
    # once at first boot, when virtio-win was the original ide3 media) —
    # but verify rather than hardcode, in case it ever differs.
    cd_letter = None
    for letter in get_cdrom_letters():
        probe = call("POST", f"/vm/{vmid}/exec", {"command": ["cmd", "/c", "dir", f"{letter}\\{basename}"]})
        if probe.get("exitcode") == 0:
            cd_letter = letter
            break

    if not cd_letter:
        print(
            f"Could not find {basename} on any CD-ROM drive after attaching to ide3 "
            f"(checked: {get_cdrom_letters()}).",
            file=sys.stderr,
        )
        cd_letter = "E:"  # will fail the copy below with a clear error either way

    copy_result = call(
        "POST", f"/vm/{vmid}/exec",
        {"command": ["cmd", "/c", "copy", "/Y", f"{cd_letter}\\{basename}", guest_path]},
    )
    copy_failed = copy_result.get("exitcode") != 0

    # Deliberately NOT deleting the ide3 device here (unlike the earlier
    # ide2 approach) — removing it would turn the NEXT push's attach back
    # into "attach a device that doesn't currently exist," which is
    # exactly the non-hotpluggable case we're avoiding. Leaving the last
    # sample's ISO attached on ide3 is harmless; the next push just swaps
    # its media again (still just a media-change on an existing device).

    if copy_failed or not verify_file_exists(vmid, guest_path):
        print(
            f"ERROR: copy from mounted ISO ({cd_letter}) to {guest_path} failed or "
            f"cannot be verified afterward (copy exitcode: {copy_result.get('exitcode')}, "
            f"stderr: {copy_result.get('stderr')}). Not reporting this as success.",
            file=sys.stderr,
        )
        sys.exit(1)

    return guest_path


def read_session_marker():
    if not os.path.isfile(SESSION_MARKER_PATH):
        return None
    try:
        with open(SESSION_MARKER_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def write_session_marker(vmid: int):
    with open(SESSION_MARKER_PATH, "w") as f:
        json.dump({"vmid": vmid, "started_at": time.time()}, f)


def clear_session_marker():
    if os.path.isfile(SESSION_MARKER_PATH):
        os.remove(SESSION_MARKER_PATH)


def session_is_alive(vmid: int) -> bool:
    try:
        result = call("POST", f"/vm/{vmid}/exec", {"command": ["cmd", "/c", "ver"]})
        return result.get("exitcode") == 0
    except SystemExit:
        return False


def cmd_start(args):
    marker = read_session_marker()
    if marker and not args.force:
        existing_vmid = marker["vmid"]
        print(
            f"A session already exists (vmid {existing_vmid}, started "
            f"{time.time() - marker['started_at']:.0f}s ago) — checking if it's still "
            "alive instead of cloning a new one ...",
            file=sys.stderr,
        )
        if session_is_alive(existing_vmid):
            guest_path = push_sample(existing_vmid, args.sample_path)
            print(
                f"Reusing existing session {existing_vmid} — pushed {args.sample_path} "
                "into it rather than starting a new session. This is expected: start is "
                "idempotent when a live session already exists.",
                file=sys.stderr,
            )
            print(json.dumps({"vmid": existing_vmid, "guest_path": guest_path, "reused": True}))
            return
        else:
            print(
                f"Existing session {existing_vmid} is no longer responsive — clearing "
                "stale marker and starting fresh.",
                file=sys.stderr,
            )
            clear_session_marker()

    vmid = args.vmid or (9100 + int(time.time()) % 400)

    print(f"Cloning template {TEMPLATE_VMID} -> {vmid} (this can take a while) ...", file=sys.stderr)
    call("POST", "/vm/clone", {"template_vmid": TEMPLATE_VMID, "new_vmid": vmid, "name": f"analysis-{vmid}"})

    print("Starting VM ...", file=sys.stderr)
    call("POST", f"/vm/{vmid}/start")

    print("Waiting for guest agent ...", file=sys.stderr)
    wait_for_guest(vmid)
    write_session_marker(vmid)  # written as soon as the VM is confirmed
    # alive, not after push — if the client dies mid-push (e.g. hit by
    # terminal.timeout on a slow clone), the session is still discoverable
    # and resumable on the next start call, instead of silently re-cloning.

    guest_path = push_sample(vmid, args.sample_path)
    print(json.dumps({"vmid": vmid, "guest_path": guest_path, "reused": False}))


def cmd_push(args):
    guest_path = push_sample(args.vmid, args.sample_path)
    print(json.dumps({"vmid": args.vmid, "guest_path": guest_path}))


def cmd_exec(args):
    result = call("POST", f"/vm/{args.vmid}/exec", {"command": args.command})
    print(json.dumps(result))


def cmd_pull(args):
    result = call("GET", f"/vm/{args.vmid}/file", params={"path": args.guest_path})
    # Proxmox's file-read API returns each byte mapped 1:1 onto a Unicode
    # codepoint (Latin-1), verified against a 256-byte all-values test file
    # + SHA256 comparison — NOT UTF-8, NOT base64. Encoding as latin-1
    # reconstructs the exact original bytes for both text and binary files.
    raw = result["content"].encode("latin-1")
    with open(args.local_out_path, "wb") as f:
        f.write(raw)
    print(json.dumps({"pulled_to": args.local_out_path, "bytes": len(raw)}))


def cmd_decompile(args):
    # For .NET (MSIL/CIL) binaries — NOT native x86/x64 code. Ghidra and
    # x64dbg are the wrong tools for these; a .NET binary decompiles almost
    # back to original C# source with ilspycmd, which reveals win-condition
    # logic (comparison values, generated strings, etc.) directly and
    # readably, far faster than fighting native disassembly on MSIL.
    #
    # How to tell if a binary needs this instead of the native path: check
    # strings output for "mscorlib", "System.Reflection", ".NETFramework",
    # or similar CLR/BCL references — if present, use `decompile`, not
    # Ghidra headless.
    #
    # This subcommand also sidesteps most of the shell-quoting fragility
    # that plagued manual exec-based Ghidra invocation: arguments here go
    # straight into the JSON request body, never re-quoted through an
    # intermediate bash command line, so Windows paths with backslashes
    # just work without the escaping games needed elsewhere.
    vmid = args.vmid
    guest_path = args.guest_path
    basename = os.path.basename(guest_path).rsplit(".", 1)[0]
    out_dir = f"{GUEST_SAMPLE_DIR}\\decompiled_{basename}"

    call("POST", f"/vm/{vmid}/exec", {"command": ["cmd", "/c", "mkdir", out_dir]})

    decompile_result = call(
        "POST", f"/vm/{vmid}/exec",
        {"command": ["ilspycmd", guest_path, "-o", out_dir]},
    )
    if decompile_result.get("exitcode") != 0:
        print(
            f"ilspycmd failed (exitcode {decompile_result.get('exitcode')}): "
            f"{decompile_result.get('stderr')}. Confirm ilspycmd is installed and on "
            "PATH — see README_Proxmox.md's toolkit section. Exact CLI flags may need "
            "adjusting for the installed ilspycmd version; check `ilspycmd --help` on "
            "the guest if this keeps failing.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Combine all produced .cs files into one, so the caller can pull a
    # single file rather than enumerating and pulling each class separately.
    combined_path = f"{out_dir}\\combined.cs"
    call(
        "POST", f"/vm/{vmid}/exec",
        {"command": ["cmd", "/c", "type", f"{out_dir}\\*.cs", ">", combined_path]},
    )

    if not verify_file_exists(vmid, combined_path):
        print(
            f"Decompilation ran but {combined_path} was not produced — check "
            f"{out_dir} on the guest directly for individual .cs files instead.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(json.dumps({"vmid": vmid, "decompiled_source": combined_path}))


def cmd_destroy(args):
    result = call("DELETE", f"/vm/{args.vmid}")
    marker = read_session_marker()
    if marker and marker.get("vmid") == args.vmid:
        clear_session_marker()
    print(json.dumps(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_start = sub.add_parser("start")
    p_start.add_argument("sample_path")
    p_start.add_argument("--vmid", type=int, default=None)
    p_start.add_argument("--force", action="store_true", help="Ignore any existing session marker and clone fresh")
    p_start.set_defaults(func=cmd_start)

    p_push = sub.add_parser("push")
    p_push.add_argument("vmid", type=int)
    p_push.add_argument("sample_path")
    p_push.set_defaults(func=cmd_push)

    p_exec = sub.add_parser("exec")
    p_exec.add_argument("vmid", type=int)
    p_exec.add_argument("command", nargs=argparse.REMAINDER)
    p_exec.set_defaults(func=cmd_exec)

    p_pull = sub.add_parser("pull")
    p_pull.add_argument("vmid", type=int)
    p_pull.add_argument("guest_path")
    p_pull.add_argument("local_out_path")
    p_pull.set_defaults(func=cmd_pull)

    p_decompile = sub.add_parser("decompile")
    p_decompile.add_argument("vmid", type=int)
    p_decompile.add_argument("guest_path")
    p_decompile.set_defaults(func=cmd_decompile)

    p_destroy = sub.add_parser("destroy")
    p_destroy.add_argument("vmid", type=int)
    p_destroy.set_defaults(func=cmd_destroy)

    args = parser.parse_args()
    if args.subcommand == "exec" and args.command and args.command[0] == "--":
        args.command = args.command[1:]
    args.func(args)


if __name__ == "__main__":
    main()
