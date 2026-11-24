"""
Proxmox <-> Hermes bridge service.

Runs on the Mac Studio (NOT inside Docker). Exposes a narrow HTTP API that
Hermes's Docker sandbox calls via host.docker.internal to control isolated
analysis VMs on the Proxmox host, without ever giving those VMs a network
path of their own.

Two channels to Proxmox, kept separate on purpose:
  - VM lifecycle (clone/start/stop/snapshot/rollback/destroy) -> Proxmox REST API
  - In-guest command exec + file transfer                     -> QEMU Guest
    Agent, relayed through Proxmox's /agent/* endpoints (virtio-serial under
    the hood, NOT the guest's network interface).

Setup:
  pip install fastapi uvicorn requests --break-system-packages
  export PVE_HOST=https://<proxmox-ip>:8006
  export PVE_NODE=pve
  export PVE_TOKEN_ID="hermes-bridge@pve!bridge-token"
  export PVE_TOKEN_SECRET="<secret from pveum user token add>"
  uvicorn proxmox_bridge:app --host 127.0.0.1 --port 8811
"""

import base64
import os
import re
import subprocess
import tempfile
import threading
import time
from datetime import datetime

import requests
import urllib3
from fastapi import FastAPI, HTTPException
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PVE_HOST = os.environ["PVE_HOST"].rstrip("/")
PVE_NODE = os.environ["PVE_NODE"]
PVE_TOKEN_ID = os.environ["PVE_TOKEN_ID"]
PVE_TOKEN_SECRET = os.environ["PVE_TOKEN_SECRET"]
# The golden template(s) — never a valid target for start/stop/exec/file
# operations from a client. Only /vm/clone may ever reference one, and only
# as the SOURCE of a clone. Confirmed necessary: an agent attempted direct
# exec calls against this vmid in practice. Protecting it here, in the
# bridge itself, means this holds regardless of what any client — correct,
# confused, or malicious — asks for; it does not depend on any model
# remembering a rule stated in a prompt or skill doc.
#
# PVE_GOLDEN_VMIDS generalizes the original single-template guard to a set,
# for setups (e.g. a multi-host range) with more than one golden image to
# protect. Comma-separated, e.g. "101,102,201,202". If unset, falls back
# to the original singular PVE_TEMPLATE_VMID env var (default 9000) alone,
# so an existing single-template deployment's env needs no change.
_golden_vmids_env = os.environ.get("PVE_GOLDEN_VMIDS")
PVE_TEMPLATE_VMID = int(os.environ.get("PVE_TEMPLATE_VMID", "9000"))
if _golden_vmids_env:
    PVE_GOLDEN_VMIDS = {int(v.strip()) for v in _golden_vmids_env.split(",") if v.strip()}
else:
    PVE_GOLDEN_VMIDS = {PVE_TEMPLATE_VMID}


def guard_not_template(vmid: int) -> None:
    if vmid in PVE_GOLDEN_VMIDS:
        raise HTTPException(
            status_code=403,
            detail=(
                f"vmid {vmid} is a golden image — it is never a valid "
                "target for this operation. Only /vm/clone may reference it, "
                "as the clone source. Use the vmid returned by a prior "
                "/vm/clone call instead."
            ),
        )

HEADERS = {"Authorization": f"PVEAPIToken={PVE_TOKEN_ID}={PVE_TOKEN_SECRET}"}
# Proxmox ships a self-signed cert by default. For a lab setup this is fine;
# for anything more exposed, pin the actual cert instead of disabling verify.
VERIFY_TLS = False

app = FastAPI(title="Proxmox-Hermes bridge")


def _log_timestamp() -> str:
    # Local wall-clock time (whatever timezone this process's host is set
    # to — the Mac Studio this runs on, per this file's own module
    # docstring), not UTC. The original version always printed "...Z"
    # (UTC) regardless of where you actually are, which meant cross-
    # referencing this log against anything else you're looking at (your
    # own shell's `date`, a watchdog's own timestamped lines, your own
    # memory of "I did X around 9pm") required mentally offsetting every
    # single line by your UTC offset — tedious and easy to get wrong.
    # datetime.now() with no tz arg is naive-local; .astimezone() attaches
    # the system's actual local tz (and its DST state, if any) without
    # needing to hardcode or guess what that offset is.
    now = datetime.now().astimezone()
    return now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + now.strftime("%z")


@app.middleware("http")
async def log_with_timestamp(request, call_next):
    # Logging only on completion (the original version of this) is silent
    # for the ENTIRE duration of a long-running request like /vm/clone —
    # up to an hour of no output at all, which is exactly backwards for
    # the request type where "is this actually progressing or silently
    # stuck" is the question that matters most. Log on both start and
    # finish instead.
    start = time.monotonic()
    print(f"{_log_timestamp()} {request.method} {request.url.path} started", flush=True)

    response = await call_next(request)

    duration_ms = (time.monotonic() - start) * 1000
    print(
        f"{_log_timestamp()} {request.method} {request.url.path} -> {response.status_code} "
        f"({duration_ms:.0f}ms)",
        flush=True,
    )
    return response


# The middleware above only ever sees a status code — never WHY a request
# failed. Confirmed this cost a real investigation: two /vm/clone calls
# returned 400, and the only way to learn the reason was to dig through
# Proxmox's own task and pveproxy access logs on the host, because the
# detail (Proxmox's own error text, passed through by pve_request) went
# back to the calling agent and nowhere else. Log it here too, so the
# bridge log alone answers "what went wrong" for every non-2xx it returns.
# Registered on Starlette's HTTPException (FastAPI's is a subclass), so
# routing errors (404/405) are covered as well as the ones raised below.
def _log_error_detail(request, status_code: int, detail) -> None:
    text = str(detail).replace("\n", " ")
    if len(text) > 1000:
        text = text[:1000] + "...(truncated)"
    print(
        f"{_log_timestamp()} {request.method} {request.url.path} error {status_code}: {text}",
        flush=True,
    )


@app.exception_handler(StarletteHTTPException)
async def log_http_exception(request, exc):
    _log_error_detail(request, exc.status_code, exc.detail)
    return await http_exception_handler(request, exc)


@app.exception_handler(RequestValidationError)
async def log_validation_error(request, exc):
    # 422 — the request body didn't match the endpoint's schema. Seen in
    # practice when an agent guessed at /exec and /file payload shapes.
    _log_error_detail(request, 422, exc.errors())
    return await request_validation_exception_handler(request, exc)


def pve_url(path: str) -> str:
    return f"{PVE_HOST}/api2/json/nodes/{PVE_NODE}{path}"


def pve_request(method: str, path: str, **kwargs) -> dict:
    # Individual requests to Proxmox occasionally hit transient network
    # blips (read timeouts, connection resets) — especially plausible
    # during a slow clone, when the host is under load, or over this
    # particular Mac-to-Proxmox link, which has shown intermittent
    # flakiness before. Confirmed this actually broke a real session: a
    # clone's wait-loop died on a transient ReadTimeout on ONE poll out of
    # thousands, the bridge reported failure to the client, and the
    # calling agent concluded (incorrectly) that the VM itself was
    # broken and destroyed it — while the clone had very possibly
    # completed successfully server-side the whole time, since a Proxmox
    # task UPID isn't tied to our polling connection at all.
    #
    # Retry only genuine transport-level failures here (connection reset,
    # read timeout) — NOT a real non-2xx response from Proxmox, which
    # reflects an actual problem and should propagate immediately rather
    # than being silently retried.
    last_exc = None
    for attempt in range(4):
        try:
            resp = requests.request(
                method, pve_url(path), headers=HEADERS, verify=VERIFY_TLS,
                timeout=60, **kwargs
            )
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            last_exc = exc
            if attempt < 3:
                time.sleep(2 ** attempt)  # 1s, 2s, 4s before retrying
                continue
            raise HTTPException(
                status_code=502,
                detail=(
                    f"Transient network error talking to Proxmox after "
                    f"4 attempts (this call itself may have nothing wrong "
                    f"with the underlying VM/task — treat as inconclusive, "
                    f"not as evidence the VM is broken): {exc}"
                ),
            ) from exc
        if not resp.ok:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return resp.json().get("data")
    raise HTTPException(status_code=502, detail=f"Retry exhaustion: {last_exc}")


def wait_for_task(upid: str, timeout: int = 7200) -> None:
    """Poll a Proxmox task (UPID) until it finishes."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = pve_request("GET", f"/tasks/{upid}/status")
        if status.get("status") == "stopped":
            if status.get("exitstatus") != "OK":
                raise HTTPException(status_code=500, detail=f"Task failed: {status}")
            return
        time.sleep(1)
    raise HTTPException(status_code=504, detail=f"Task {upid} timed out")


# ---------- VM lifecycle ----------

class CloneRequest(BaseModel):
    template_vmid: int
    new_vmid: int
    name: str
    storage: str = "hot-ssd"


# A clone is NOT finished when Proxmox's clone task finishes — clone_vm
# still has to put the golden image's net0 (and so its MAC) back onto the
# new VM afterwards. Confirmed for real (2026-11-24, vmids 9610 and 9612):
# the agent called /vm/{vmid}/start while /vm/clone was still running.
# Proxmox's start task waited on the VM's config lock, grabbed it the
# instant the clone task released it, and booted the VM with the random
# MAC Proxmox assigns every clone; clone_vm's net0 PUT then hit a starting/
# running VM and Proxmox rejected it (400 in pveproxy's access log). The
# VM was up but on the wrong MAC, so unreachable at its range address,
# and both hosts were recorded scan_failed.
#
# GET /vm/{vmid}/status could not have warned anyone: Proxmox creates the
# new VM's config at the START of a clone, so status reports exists/stopped
# for the whole copy. Only /vm/clone returning tells you it's done.
#
# Guard: every vmid a clone_vm call is currently working on is recorded
# here, from before the clone starts until net0 has been verified; start_vm
# refuses those outright. Same principle as guard_not_template: enforced in
# the bridge itself, not left to a client remembering a rule. In-memory on
# purpose — it assumes ONE bridge process, which is what this file's own
# documented uvicorn command runs (no --workers). A bridge restart mid-clone
# loses the set, but that also drops the in-flight /vm/clone request itself;
# start_vm's Proxmox-side config-lock check below is the backstop for that
# case and for clones started outside this bridge.
_clone_guard_lock = threading.Lock()
_clones_in_progress: set[int] = set()
MAX_CONCURRENT_CLONES = max(1, int(os.environ.get("BRIDGE_MAX_CONCURRENT_CLONES", "1")))

_MAC_RE = re.compile(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})")


def _mac_of(net_value: str | None) -> str | None:
    m = _MAC_RE.search(net_value or "")
    return m.group(1).upper() if m else None


def _clone_in_progress(vmid: int) -> bool:
    with _clone_guard_lock:
        return vmid in _clones_in_progress


def _put_net0_with_retries(vmid: int, net0: str, attempts: int = 3) -> HTTPException | None:
    """PUT net0, retrying a few times — a config update can transiently
    fail on the VM's config lock (e.g. a task on the same VM still
    finishing). Returns the last error, or None on success."""
    last_err = None
    for attempt in range(attempts):
        try:
            pve_request("PUT", f"/qemu/{vmid}/config", data={"net0": net0})
            return None
        except HTTPException as exc:
            last_err = exc
            if attempt < attempts - 1:
                time.sleep(3)
    return last_err


def _restore_template_net0(vmid: int, template_net0: str) -> None:
    """Make the clone carry the golden image's exact net0 (same MAC), and
    make sure it will actually BOOT with it.

    If the VM is already running at this point, something started it
    before /vm/clone finished (start_vm's guard should prevent that via
    this bridge, so this means a restarted bridge or a start from outside
    it). A running VM that booted on the wrong MAC can't be fixed in
    place reliably — a MAC change on a running VM is either left pending
    until restart or hot-swapped under a guest that already configured
    its network — so stop it, apply net0, verify, and tell the caller to
    start it again. The clone itself is fine; only the early boot is
    undone."""
    want_mac = _mac_of(template_net0)
    put_err = _put_net0_with_retries(vmid, template_net0)

    status = pve_request("GET", f"/qemu/{vmid}/status/current")
    was_running = status.get("status") == "running"
    if was_running:
        stop_upid = pve_request("POST", f"/qemu/{vmid}/status/stop")
        wait_for_task(stop_upid)
        put_err = _put_net0_with_retries(vmid, template_net0)

    # current=1: Proxmox's config GET returns PENDING values by default;
    # we need what the VM will actually have.
    current = pve_request("GET", f"/qemu/{vmid}/config", params={"current": 1})
    have_mac = _mac_of(current.get("net0"))
    if want_mac and have_mac != want_mac:
        raise HTTPException(
            status_code=500,
            detail=(
                f"clone of vmid {vmid} finished, but its net0 could not be set to "
                f"the golden image's (want MAC {want_mac}, have {have_mac}). It will "
                "NOT get its range address. Do not start or scan it — destroy it and "
                f"clone again. Last Proxmox error: "
                f"{put_err.detail if put_err else '(none — PUT accepted but not applied)'}"
            ),
        )
    return was_running


def _early_start_error(vmid: int, mac: str | None) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail=(
            f"vmid {vmid} was STARTED before /vm/clone finished, so it booted "
            "with the wrong MAC and would not have been reachable at its range "
            "address. The bridge has stopped it and applied the golden image's "
            f"net0 (MAC {mac}). The clone itself is fine and is now STOPPED — "
            f"call POST /vm/{vmid}/start once, and only now. (No 'pristine' "
            "snapshot was taken: the disk is no longer identical to the golden "
            "image after that early boot.)"
        ),
    )


# ---------- Snapshots ----------
#
# Every successful /vm/clone ends with a disk-only snapshot named
# PRISTINE_SNAPSHOT, taken while the clone is still stopped and has never
# booted — i.e. byte-for-byte the golden image, with the golden MAC
# already applied. On LVM-thin it costs ~nothing until the VM diverges, and
# it turns "get back to a clean copy of this host" from a full re-clone
# (2-3 min Linux, 16-33 min Windows off the SMR source) into a rollback of
# seconds. Rolling back to it also discards every remediation already
# applied on that clone — it's a full reset of the host, not an undo of one
# change.
#
# Before this, the bridge had a rollback endpoint but no way to TAKE a
# snapshot, and its default target (`clean-tooled`) only exists on the
# windows-binary-analysis template; a full clone doesn't carry the
# source's snapshots, so range working clones had nothing to roll back to.
PRISTINE_SNAPSHOT = "pristine"
_SNAPNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,39}$")


def _take_snapshot(vmid: int, name: str, description: str) -> None:
    upid = pve_request(
        "POST", f"/qemu/{vmid}/snapshot",
        data={"snapname": name, "description": description, "vmstate": 0},
    )
    wait_for_task(upid)


# ---------- Storage capacity ----------
#
# Confirmed for real (2026-11-23): the hot-ssd LVM thin pool reached 93.58%
# data usage, an lvcreate under a parallel clone burst stalled instead of
# failing, and because it held the volume group lock, every later clone,
# destroy and even the Proxmox web UI hung for 7+ hours with no error
# surfaced (requests just timed out as 504s). range-assess's SKILL.md then
# told the agent to check headroom with `lvs` before cloning — but `lvs`
# only runs on the Proxmox host, and the agent has no shell there. Confirmed
# on 2026-11-24: it guessed at ~10 nonexistent bridge endpoints (/storage,
# /lvs, /pool, ...), found nothing, and cloned without checking. So: give it
# a real read-only endpoint, AND enforce the limit in /vm/clone itself, the
# same way the golden-image and mid-clone guards are enforced here rather
# than left to a rule in a prompt.
#
# Source: Proxmox's own storage status API. For an lvmthin storage its
# `used`/`total` come from the thin pool's own data usage — the same Data%
# `lvs` shows. (Metadata% is not available through this API; Data% is the
# one the 2026-11-23 incident turned on.)
#
# Two limits, both overridable via env:
#   - current usage at/above CLONE_MAX_USED_PERCENT (default 75) -> refuse:
#     time to destroy finished hosts' clones first.
#   - projected usage (current + the template's full provisioned disk size,
#     a worst case for a full clone) above CLONE_MAX_PROJECTED_PERCENT
#     (default 95) -> refuse: this one clone could fill the pool outright.
CLONE_MAX_USED_PERCENT = float(os.environ.get("BRIDGE_CLONE_MAX_USED_PERCENT", "75"))
CLONE_MAX_PROJECTED_PERCENT = float(os.environ.get("BRIDGE_CLONE_MAX_PROJECTED_PERCENT", "95"))

_STORAGE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_DISK_KEY_RE = re.compile(r"^(scsi|sata|virtio|ide|efidisk|tpmstate)\d+$")
_SIZE_RE = re.compile(r"(?:^|,)size=(\d+(?:\.\d+)?)([KMGT]?)", re.IGNORECASE)
_SIZE_MULT = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


def _template_disk_bytes(template_config: dict) -> int:
    """Sum of the template's provisioned disk sizes (CD-ROMs excluded) —
    an upper bound on what a full clone can allocate."""
    total = 0
    for key, value in template_config.items():
        if not _DISK_KEY_RE.match(key) or not isinstance(value, str):
            continue
        if "media=cdrom" in value or value.startswith("none"):
            continue
        m = _SIZE_RE.search(value)
        if m:
            total += int(float(m.group(1)) * _SIZE_MULT[m.group(2).upper()])
    return total


def _storage_report(storage: str, template_config: dict | None = None) -> dict:
    if not _STORAGE_NAME_RE.match(storage):
        raise HTTPException(status_code=400, detail=f"invalid storage name {storage!r}")
    st = pve_request("GET", f"/storage/{storage}/status")
    total = int(st.get("total") or 0)
    used = int(st.get("used") or 0)
    active = bool(st.get("active"))
    used_pct = round(used * 100 / total, 2) if total else None
    report = {
        "storage": storage,
        "type": st.get("type"),
        "active": active,
        "total_gib": round(total / 1024**3, 1),
        "used_gib": round(used / 1024**3, 1),
        "avail_gib": round(int(st.get("avail") or 0) / 1024**3, 1),
        "used_percent": used_pct,
        "clone_max_used_percent": CLONE_MAX_USED_PERCENT,
        "clone_max_projected_percent": CLONE_MAX_PROJECTED_PERCENT,
    }
    reasons = []
    if not active or not total:
        reasons.append(f"storage {storage} is not active or reports no capacity")
    elif used_pct >= CLONE_MAX_USED_PERCENT:
        reasons.append(
            f"{storage} is at {used_pct}% data usage (limit {CLONE_MAX_USED_PERCENT}%). "
            "Destroy finished hosts' working clones (stop + DELETE) before cloning more — "
            "a nearly full thin pool can hang ALL storage operations rather than fail cleanly."
        )
    if template_config is not None and total:
        clone_bytes = _template_disk_bytes(template_config)
        projected = round((used + clone_bytes) * 100 / total, 2)
        report["template_disk_gib"] = round(clone_bytes / 1024**3, 1)
        report["projected_percent"] = projected
        if projected > CLONE_MAX_PROJECTED_PERCENT:
            reasons.append(
                f"cloning this template (up to {report['template_disk_gib']} GiB) could take "
                f"{storage} to {projected}% (limit {CLONE_MAX_PROJECTED_PERCENT}%). Destroy "
                "finished hosts' working clones first."
            )
    report["ok_to_clone"] = not reasons
    report["reason"] = " ".join(reasons) if reasons else "headroom OK"
    return report


@app.get("/storage/{storage}/status")
def storage_status(storage: str, template_vmid: int | None = None):
    """Read-only capacity check for a clone target storage (e.g. hot-ssd).
    Pass ?template_vmid=<golden vmid> to also get the projected usage after
    cloning that template. /vm/clone applies the same check itself and
    refuses with 507 when ok_to_clone would be false."""
    template_config = (
        pve_request("GET", f"/qemu/{template_vmid}/config") if template_vmid is not None else None
    )
    return _storage_report(storage, template_config)


@app.post("/vm/clone")
def clone_vm(req: CloneRequest):
    if req.new_vmid in PVE_GOLDEN_VMIDS:
        raise HTTPException(
            status_code=403,
            detail=(
                f"new_vmid {req.new_vmid} matches a golden image's own "
                "vmid — refusing to clone onto/over a golden image itself."
            ),
        )
    # Proxmox gives a full clone's NIC a fresh, randomly generated MAC by
    # default — confirmed against a real clone here. A clone only reproduces
    # its golden image's network reachability (which the operator verified
    # per golden image, IPv4 and IPv6, at build time) if it carries that
    # golden image's exact MAC:
    #   - most hosts get their address from range-gw's dnsmasq static
    #     reservations (range-gw/dnsmasq.d/range.conf), keyed to each golden
    #     image's OWN original MAC — a regenerated MAC gets no reservation;
    #   - some hosts (e.g. beg-rtu-host) are addressed statically inside the
    #     guest (systemd-networkd) instead; the same rule applies — keep the
    #     golden MAC and the clone behaves exactly as the verified golden did.
    # A guest network config pinned to the MAC (NetworkManager HWADDR/
    # mac-address=, networkd [Match] MACAddress=) can also leave the NIC
    # unconfigured on a different MAC, with no obvious "wrong MAC" symptom
    # from inside the guest.
    #
    # Fix: read the template's own net0 line and force the clone to carry
    # the identical value (same MAC, same model/bridge/etc.). Safe under the
    # existing golden/working invariant (only one working clone of a given
    # golden vmid is ever live at a time), so the MAC is never duplicated on
    # the wire.
    with _clone_guard_lock:
        if req.new_vmid in _clones_in_progress:
            raise HTTPException(
                status_code=409,
                detail=f"a clone onto vmid {req.new_vmid} is already in progress on this bridge.",
            )
        # Range-wide limit, not just per-vmid: the clone source is a single
        # 5400 RPM SMR disk (vmdata) and the target a single USB SSD, so two
        # clones at once only slow each other down — and a parallel burst is
        # what stalled the whole LVM layer for 7+ hours on 2026-11-23.
        # range-assess's SKILL.md already says "never more than one clone in
        # flight"; this enforces it for every client of this bridge (the
        # agent, a pre-clone helper, the windows-binary-analysis skill)
        # instead of relying on each to remember. Set
        # BRIDGE_MAX_CONCURRENT_CLONES higher only if the storage changes.
        if len(_clones_in_progress) >= MAX_CONCURRENT_CLONES:
            busy = ", ".join(str(v) for v in sorted(_clones_in_progress))
            raise HTTPException(
                status_code=409,
                detail=(
                    f"another clone is already in progress on this bridge (vmid {busy}); "
                    f"at most {MAX_CONCURRENT_CLONES} at a time. Nothing was created for "
                    f"vmid {req.new_vmid}. Wait for that clone to finish, then retry."
                ),
            )
        _clones_in_progress.add(req.new_vmid)
    try:
        template_config = pve_request("GET", f"/qemu/{req.template_vmid}/config")
        template_net0 = template_config.get("net0")

        # Capacity gate — see _storage_report above for the real incident.
        # Refuses BEFORE anything is created on Proxmox, so a refused clone
        # leaves nothing behind.
        report = _storage_report(req.storage, template_config)
        if not report["ok_to_clone"]:
            raise HTTPException(status_code=507, detail=report["reason"])

        upid = pve_request(
            "POST",
            f"/qemu/{req.template_vmid}/clone",
            data={
                "newid": req.new_vmid,
                "name": req.name,
                "full": 1,
                "storage": req.storage,
            },
        )
        wait_for_task(upid)

        was_running = _restore_template_net0(req.new_vmid, template_net0) if template_net0 else False
        if was_running:
            raise _early_start_error(req.new_vmid, _mac_of(template_net0))

        # Insurance, not a precondition: if the snapshot fails, the clone is
        # still perfectly usable, so report it instead of failing the call.
        try:
            _take_snapshot(
                req.new_vmid, PRISTINE_SNAPSHOT,
                f"Untouched clone of golden {req.template_vmid} (golden MAC applied, never booted).",
            )
            pristine = True
            pristine_note = None
        except HTTPException as exc:
            pristine = False
            pristine_note = f"pristine snapshot failed (clone is still usable): {exc.detail}"
            print(f"{_log_timestamp()} /vm/clone {req.new_vmid}: {pristine_note}", flush=True)

        result = {
            "vmid": req.new_vmid, "status": "cloned", "mac": _mac_of(template_net0),
            "pristine_snapshot": PRISTINE_SNAPSHOT if pristine else None,
        }
        if pristine_note:
            result["note"] = pristine_note
        return result
    finally:
        with _clone_guard_lock:
            _clones_in_progress.discard(req.new_vmid)


@app.get("/vm/{vmid}/status")
def vm_status(vmid: int):
    # Added after a real gap surfaced: NOTHING in this bridge could
    # previously answer "does this vmid still exist / what's it doing
    # right now" without side effects. An operator manually destroying
    # a working clone directly in Proxmox (outside this bridge, outside
    # range_traversal.py) left the calling agent with a stale
    # working_vmid in its own state and no read-only way to check
    # reality — it fell back to guessing from a GET that doesn't exist
    # (405) and then from an exec call's error, which is ambiguous (a
    # fast failure there looks the same whether the vmid is genuinely
    # gone or the guest agent just isn't up yet). This closes that gap.
    #
    # Deliberately NOT behind guard_not_template: this is read-only and
    # carries zero mutation risk even against a golden vmid — in fact
    # checking a golden image's own running/stopped state (e.g. while
    # manually prepping golden images for first boot) is a legitimate,
    # harmless use of this same endpoint.
    #
    # NEW, UNTESTED CODE — the "doesn't exist" detection below was
    # written from the error text Proxmox returned in a DIFFERENT call
    # (a start attempt on a destroyed vmid: "Configuration file
    # '...950.conf' does not exist"), not yet confirmed against this
    # exact endpoint (GET .../status/current) called on a genuinely
    # nonexistent vmid. Verify the real error text/status code once
    # against a real target before trusting the exists:false branch —
    # if Proxmox phrases it differently here, broaden the match below
    # rather than assuming this already covers it.
    try:
        status = pve_request("GET", f"/qemu/{vmid}/status/current")
    except HTTPException as exc:
        detail_text = str(exc.detail).lower()
        if exc.status_code in (500, 596) and (
            "does not exist" in detail_text or "no such" in detail_text
        ):
            return {"vmid": vmid, "exists": False}
        raise
    in_progress = _clone_in_progress(vmid)
    return {
        "vmid": vmid,
        "exists": True,
        "status": status.get("status"),
        "name": status.get("name"),
        "uptime": status.get("uptime"),
        # "stopped" alone is NOT "ready" — Proxmox creates the config at the
        # start of a clone. These two fields say whether it's still being
        # built: Proxmox's own config lock (e.g. "clone"), and whether this
        # bridge is still inside /vm/clone for it (net0 not yet restored).
        "lock": status.get("lock"),
        "clone_in_progress": in_progress,
        "ready_to_start": (not in_progress) and not status.get("lock")
                          and status.get("status") == "stopped",
    }


_CLONE_WAIT_HINT = {
    "start": "Starting now would boot it on the wrong MAC. Wait for /vm/clone to "
             "return 200, then start.",
    "stop": "There is nothing running to stop yet. Wait for /vm/clone to return.",
    "destroy": "Destroying it now would throw away a clone that is still being "
               "built (and very possibly about to succeed). Wait for /vm/clone to "
               "return; only destroy it if that call comes back non-200.",
    "rollback": "Wait for /vm/clone to return 200 first.",
    "snapshot": "Wait for /vm/clone to return 200 first — it takes the 'pristine' "
                "snapshot itself at the end.",
}


def refuse_if_being_cloned(vmid: int, action: str) -> None:
    """Refuse a mutating call against a vmid whose clone isn't finished.

    start: see _clones_in_progress above for the real incident (wrong-MAC
    boot). destroy/stop: confirmed for real on 2026-11-24 — an agent,
    after 11 failed start attempts on a Windows clone still mid-copy,
    issued DELETE against it 2 minutes before the clone completed
    successfully. Proxmox's own config lock happened to refuse it that
    time, but that lock is released when the disk copy ends, BEFORE this
    bridge restores the golden net0 — so a DELETE landing in that window
    would have succeeded and destroyed a good clone. The in-memory set
    covers that window; the Proxmox lock check is the backstop for a clone
    this bridge isn't tracking (bridge restarted mid-clone, or a clone
    started outside the bridge)."""
    hint = _CLONE_WAIT_HINT[action]
    if _clone_in_progress(vmid):
        raise HTTPException(
            status_code=409,
            detail=(
                f"vmid {vmid} is still being cloned — /vm/clone has not returned yet "
                f"(its golden net0/MAC is applied at the very end). {hint}"
            ),
        )
    config = pve_request("GET", f"/qemu/{vmid}/config")
    if config.get("lock"):
        raise HTTPException(
            status_code=409,
            detail=(
                f"vmid {vmid} is locked by Proxmox ('{config.get('lock')}') — an "
                f"operation such as a clone is still running on it. {hint}"
            ),
        )


@app.post("/vm/{vmid}/start")
def start_vm(vmid: int):
    guard_not_template(vmid)
    refuse_if_being_cloned(vmid, "start")
    upid = pve_request("POST", f"/qemu/{vmid}/status/start")
    wait_for_task(upid)
    return {"vmid": vmid, "status": "started"}


@app.post("/vm/{vmid}/stop")
def stop_vm(vmid: int):
    guard_not_template(vmid)
    refuse_if_being_cloned(vmid, "stop")
    upid = pve_request("POST", f"/qemu/{vmid}/status/stop")
    wait_for_task(upid)
    return {"vmid": vmid, "status": "stopped"}


def _list_snapshots(vmid: int) -> list[dict]:
    snaps = pve_request("GET", f"/qemu/{vmid}/snapshot") or []
    # Proxmox always includes a pseudo-entry "current" (the live state).
    return [
        {"name": s.get("name"), "description": s.get("description", ""),
         "snaptime": s.get("snaptime"), "parent": s.get("parent"),
         "vmstate": bool(s.get("vmstate"))}
        for s in snaps if s.get("name") != "current"
    ]


@app.get("/vm/{vmid}/snapshots")
def list_snapshots(vmid: int):
    # Read-only, so not behind guard_not_template (same reasoning as status).
    return {"vmid": vmid, "snapshots": _list_snapshots(vmid)}


class SnapshotRequest(BaseModel):
    name: str
    description: str = ""


@app.post("/vm/{vmid}/snapshot")
def create_snapshot(vmid: int, req: SnapshotRequest):
    guard_not_template(vmid)
    refuse_if_being_cloned(vmid, "snapshot")
    if not _SNAPNAME_RE.match(req.name):
        raise HTTPException(
            status_code=400,
            detail=f"invalid snapshot name {req.name!r}: must start with a letter, then "
                   "letters/digits/'_'/'-', 2-40 characters (Proxmox's own rule).",
        )
    # Disk-only (no RAM state): works whether the VM is running or stopped,
    # is small, and is what rollback below expects.
    _take_snapshot(vmid, req.name, req.description)
    return {"vmid": vmid, "snapshot": req.name, "status": "created"}


@app.post("/vm/{vmid}/rollback")
def rollback_vm(vmid: int, snapname: str = "clean-tooled", start: bool = False):
    # Default stays `clean-tooled` for the windows-binary-analysis skill's
    # existing calls. Range working clones use `pristine` (taken by
    # /vm/clone) or a snapshot made with POST /vm/{vmid}/snapshot.
    guard_not_template(vmid)
    refuse_if_being_cloned(vmid, "rollback")
    if not _SNAPNAME_RE.match(snapname):
        raise HTTPException(status_code=400, detail=f"invalid snapshot name {snapname!r}")
    names = [s["name"] for s in _list_snapshots(vmid)]
    if snapname not in names:
        raise HTTPException(
            status_code=404,
            detail=f"vmid {vmid} has no snapshot named {snapname!r}. Available: {names or 'none'}.",
        )
    # Rolling a disk-only snapshot back means the running guest's disk is
    # replaced underneath it — stop it cleanly first rather than leave that
    # to Proxmox, so the outcome is the same every time: a stopped VM.
    status = pve_request("GET", f"/qemu/{vmid}/status/current")
    if status.get("status") == "running":
        stop_upid = pve_request("POST", f"/qemu/{vmid}/status/stop")
        wait_for_task(stop_upid)
    upid = pve_request("POST", f"/qemu/{vmid}/snapshot/{snapname}/rollback")
    wait_for_task(upid)
    if start:
        start_upid = pve_request("POST", f"/qemu/{vmid}/status/start")
        wait_for_task(start_upid)
    return {
        "vmid": vmid, "status": f"rolled back to {snapname}",
        "running": start,
        "note": None if start else f"VM is STOPPED — call POST /vm/{vmid}/start when ready.",
    }


@app.delete("/vm/{vmid}")
def destroy_vm(vmid: int):
    guard_not_template(vmid)
    refuse_if_being_cloned(vmid, "destroy")
    status = pve_request("GET", f"/qemu/{vmid}/status/current")
    if status.get("status") == "running":
        stop_upid = pve_request("POST", f"/qemu/{vmid}/status/stop")
        wait_for_task(stop_upid)
    upid = pve_request("DELETE", f"/qemu/{vmid}")
    wait_for_task(upid)
    return {"vmid": vmid, "status": "destroyed"}


# ---------- Guest agent: exec ----------

class ExecRequest(BaseModel):
    command: list[str]


@app.get("/")
def health():
    # Exists so a bare request to the bridge's root doesn't return a
    # confusing 404 that gets misread as "the bridge isn't running." A 404
    # here previously happened for the mundane reason that no root route
    # was defined — it never meant the service was down. Real liveness
    # check: /openapi.json (auto-generated by FastAPI) also works.
    return {"status": "ok", "service": "proxmox-hermes-bridge"}


@app.post("/vm/{vmid}/exec")
def guest_exec(vmid: int, req: ExecRequest, timeout: int = 1800):
    guard_not_template(vmid)
    # Default raised from 60s to 1800s (30 min). The original 60s default
    # was too short for real analysis commands — Ghidra's analyzeHeadless
    # in particular can run well past a minute, especially on a first run
    # (one-time indexing). A command that legitimately exceeds even this
    # generous default surfaces as a QEMU-level "qga command
    # 'guest-exec-status' failed - got timeout" error, which is easy to
    # misread as "the guest agent isn't running" — it isn't; a specific
    # long-running command just outran the poll window.
    result = pve_request(
        "POST",
        f"/qemu/{vmid}/agent/exec",
        data=[("command", part) for part in req.command],
    )
    pid = result["pid"]

    deadline = time.time() + timeout
    while time.time() < deadline:
        status = pve_request("GET", f"/qemu/{vmid}/agent/exec-status", params={"pid": pid})
        if status.get("exited"):
            return {
                "exitcode": status.get("exitcode"),
                "stdout": status.get("out-data", ""),
                "stderr": status.get("err-data", ""),
            }
        time.sleep(0.5)
    # Timed out waiting for the process to exit — it's still running in the
    # guest (a hung binary, e.g. one waiting on stdin that never got closed
    # correctly). Best-effort kill it (and any children — /T covers a case
    # like `powershell ... | some.exe`, which spawns a child under this pid)
    # so retries don't silently accumulate zombie processes in the VM.
    # Confirmed happening in practice: multiple orphaned instances of a
    # hanging binary piled up in Task Manager across repeated exec attempts
    # before this fix existed.
    try:
        pve_request(
            "POST",
            f"/qemu/{vmid}/agent/exec",
            data=[("command", part) for part in ["cmd", "/c", "taskkill", "/F", "/T", "/PID", str(pid)]],
        )
    except Exception:
        pass  # best-effort cleanup; don't let a failed kill mask the real timeout error

    raise HTTPException(
        status_code=504,
        detail=f"exec timed out waiting for guest (pid {pid} — attempted cleanup kill, verify with tasklist if needed)",
    )


# ---------- Guest agent: file transfer ----------

class FileWriteRequest(BaseModel):
    path: str
    content_base64: str


@app.post("/vm/{vmid}/file")
def guest_file_write(vmid: int, req: FileWriteRequest):
    guard_not_template(vmid)
    pve_request(
        "POST",
        f"/qemu/{vmid}/agent/file-write",
        data={"file": req.path, "content": req.content_base64, "encode": 0},
    )
    return {"path": req.path, "status": "written"}


@app.get("/vm/{vmid}/file")
def guest_file_read(vmid: int, path: str):
    guard_not_template(vmid)
    result = pve_request("GET", f"/qemu/{vmid}/agent/file-read", params={"file": path})
    # Proxmox's API returns decoded text here, not base64 (unlike QEMU GA's
    # own guest-file-read spec, which returns base64). Verified empirically
    # with a 256-byte all-values binary test file + SHA256 comparison:
    # Proxmox maps each byte to the Unicode codepoint of the same value
    # (Latin-1 / ISO-8859-1), which is lossless and invertible — unlike
    # UTF-8, which would corrupt bytes >= 0x80.
    #
    # Callers reconstructing the original bytes MUST decode this string as
    # latin-1, not utf-8:
    #   raw_bytes = response_json["content"].encode("latin-1")
    return {"path": path, "content": result.get("content")}


# ---------- Large file transfer (bypasses agent/file-write's hard 61440-byte
# limit) ----------
#
# Confirmed via Proxmox's own support forum: the file-write API's `content`
# parameter is validated against a hardcoded 61440-byte ceiling regardless
# of the general POST body size limit (raised to 512KB in PVE 8.4) — this
# is a real, currently-unfixed constraint in Proxmox itself, not something
# addressable from this bridge's own request handling.
#
# Workaround: build a real ISO9660 image around the file (via macOS's
# built-in hdiutil — this bridge runs on the Mac Studio, not the Proxmox
# host), upload it through Proxmox's dedicated storage-upload endpoint
# (built for large files, no 61440-byte constraint), attach it as a
# CD-ROM, and let the guest copy the file off via a normal exec call.
#
# IMPORTANT: use ide3 specifically, not ide2. Confirmed via direct manual
# testing: QEMU does NOT hotplug a brand-new IDE device into a running VM
# under any circumstances (waited well past any reasonable timing window —
# the device simply never appears to the guest, config or no config).
# Swapping the MEDIA on an ide slot that already exists as a device,
# however, is instant and reliable. ide3 exists as a device on every clone
# because the golden template's virtio-win reference (used during template
# build) was never removed before the final snapshot — this leftover
# device is exactly what makes ide3 usable for hot media-swapping.
# ide2, by contrast, was deliberately deleted from the golden template
# before snapshotting (see README_Proxmox.md's toolkit section), so on any
# clone it does not exist as a device — attaching to it hits the
# non-hotpluggable "new device" case and never becomes visible to the
# guest, regardless of wait time.

class CdromAttachRequest(BaseModel):
    volid: str
    ide_slot: str = "ide3"


class StorageUploadRequest(BaseModel):
    filename: str
    content_base64: str
    storage: str = "local"


@app.post("/storage/upload")
def storage_upload(req: StorageUploadRequest):
    # Takes file CONTENT, not a path. The bridge runs on the Mac Studio
    # host; host.docker.internal is a network route, not a filesystem
    # bridge, so a path from inside the sandbox container (e.g.
    # /workspace/samples/x.exe) is meaningless here — the bridge process
    # can't open it. The caller (running inside the container, where the
    # file IS readable) must read and base64-encode it first, same pattern
    # as the small-file direct-write path already uses.
    raw = base64.b64decode(req.content_base64)

    with tempfile.TemporaryDirectory() as tmp:
        staging = os.path.join(tmp, "iso_root")
        os.makedirs(staging)
        with open(os.path.join(staging, req.filename), "wb") as f:
            f.write(raw)

        iso_path = os.path.join(tmp, "payload.iso")
        try:
            subprocess.run(
                ["hdiutil", "makehybrid", "-iso", "-joliet", "-o", iso_path, staging],
                check=True, capture_output=True, text=True,
            )
        except FileNotFoundError:
            raise HTTPException(
                status_code=500,
                detail="hdiutil not found — this endpoint assumes the bridge runs on macOS. "
                       "On Linux, install genisoimage and swap the subprocess call.",
            )
        except subprocess.CalledProcessError as e:
            raise HTTPException(status_code=500, detail=f"hdiutil failed: {e.stderr}")

        volid_name = f"bridge-{int(time.time())}-{req.filename}.iso"
        with open(iso_path, "rb") as f:
            resp = requests.post(
                f"{PVE_HOST}/api2/json/nodes/{PVE_NODE}/storage/{req.storage}/upload",
                headers=HEADERS,
                verify=VERIFY_TLS,
                files={"filename": (volid_name, f, "application/octet-stream")},
                data={"content": "iso"},
                timeout=300,
            )
        if not resp.ok:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        data = resp.json().get("data")
        if isinstance(data, str) and data.startswith("UPID:"):
            wait_for_task(data, timeout=300)

    return {"storage": req.storage, "volid": f"{req.storage}:iso/{volid_name}"}


@app.post("/vm/{vmid}/cdrom")
def attach_cdrom(vmid: int, req: CdromAttachRequest):
    guard_not_template(vmid)
    pve_request(
        "PUT", f"/qemu/{vmid}/config",
        data={req.ide_slot: f"{req.volid},media=cdrom"},
    )
    return {"vmid": vmid, "ide_slot": req.ide_slot, "volid": req.volid, "status": "attached"}


@app.delete("/vm/{vmid}/cdrom")
def detach_cdrom(vmid: int, ide_slot: str = "ide3", storage: str | None = None, volid: str | None = None):
    guard_not_template(vmid)
    pve_request("PUT", f"/qemu/{vmid}/config", data={"delete": ide_slot})
    if storage and volid:
        try:
            content_id = volid.split(":", 1)[1]
            pve_request("DELETE", f"/storage/{storage}/content/{content_id}")
        except HTTPException:
            pass  # best-effort cleanup; don't fail the whole detach over it
    return {"vmid": vmid, "ide_slot": ide_slot, "status": "detached"}
