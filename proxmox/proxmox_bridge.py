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
import subprocess
import tempfile
import time
from datetime import datetime, timezone

import requests
import urllib3
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PVE_HOST = os.environ["PVE_HOST"].rstrip("/")
PVE_NODE = os.environ["PVE_NODE"]
PVE_TOKEN_ID = os.environ["PVE_TOKEN_ID"]
PVE_TOKEN_SECRET = os.environ["PVE_TOKEN_SECRET"]
# The golden template — never a valid target for start/stop/exec/file
# operations from a client. Only /vm/clone may ever reference it, and only
# as the SOURCE of a clone. Confirmed necessary: an agent attempted direct
# exec calls against this vmid in practice. Protecting it here, in the
# bridge itself, means this holds regardless of what any client — correct,
# confused, or malicious — asks for; it does not depend on any model
# remembering a rule stated in a prompt or skill doc.
PVE_TEMPLATE_VMID = int(os.environ.get("PVE_TEMPLATE_VMID", "9000"))


def guard_not_template(vmid: int) -> None:
    if vmid == PVE_TEMPLATE_VMID:
        raise HTTPException(
            status_code=403,
            detail=(
                f"vmid {vmid} is the golden template — it is never a valid "
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


@app.middleware("http")
async def log_with_timestamp(request, call_next):
    # Logging only on completion (the original version of this) is silent
    # for the ENTIRE duration of a long-running request like /vm/clone —
    # up to an hour of no output at all, which is exactly backwards for
    # the request type where "is this actually progressing or silently
    # stuck" is the question that matters most. Log on both start and
    # finish instead.
    start = time.monotonic()
    ts_start = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + "Z"
    print(f"{ts_start} {request.method} {request.url.path} started", flush=True)

    response = await call_next(request)

    duration_ms = (time.monotonic() - start) * 1000
    ts_end = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + "Z"
    print(
        f"{ts_end} {request.method} {request.url.path} -> {response.status_code} "
        f"({duration_ms:.0f}ms)",
        flush=True,
    )
    return response


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


@app.post("/vm/clone")
def clone_vm(req: CloneRequest):
    if req.new_vmid == PVE_TEMPLATE_VMID:
        raise HTTPException(
            status_code=403,
            detail=(
                f"new_vmid {req.new_vmid} matches the golden template's own "
                "vmid — refusing to clone onto/over the template itself."
            ),
        )
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
    return {"vmid": req.new_vmid, "status": "cloned"}


@app.post("/vm/{vmid}/start")
def start_vm(vmid: int):
    guard_not_template(vmid)
    upid = pve_request("POST", f"/qemu/{vmid}/status/start")
    wait_for_task(upid)
    return {"vmid": vmid, "status": "started"}


@app.post("/vm/{vmid}/stop")
def stop_vm(vmid: int):
    guard_not_template(vmid)
    upid = pve_request("POST", f"/qemu/{vmid}/status/stop")
    wait_for_task(upid)
    return {"vmid": vmid, "status": "stopped"}


@app.post("/vm/{vmid}/rollback")
def rollback_vm(vmid: int, snapname: str = "clean-tooled"):
    guard_not_template(vmid)
    upid = pve_request("POST", f"/qemu/{vmid}/snapshot/{snapname}/rollback")
    wait_for_task(upid)
    return {"vmid": vmid, "status": f"rolled back to {snapname}"}


@app.delete("/vm/{vmid}")
def destroy_vm(vmid: int):
    guard_not_template(vmid)
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
