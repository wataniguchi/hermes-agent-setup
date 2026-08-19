# Proxmox CTF / Security Analysis Lab

Extends [hermes-agent-setup](README.md) with an isolated Windows (and future
non-Windows) binary analysis environment running on Proxmox VE, driven by
Hermes over a narrow HTTP bridge. Covers what the Mac Studio's Docker sandbox
can't: Docker there only runs Linux containers, so Windows PE analysis, CTF
pwn challenges targeting Windows, and Windows malware triage need a real x86
KVM environment instead.

**Status:** golden template (`win10-golden`, JIS-layout Windows 10, full
toolkit) built and snapshotted. Bridge service (`proxmox/proxmox_bridge.py`)
proven end-to-end: clone → start → guest-exec → destroy, tested both locally
on the Proxmox host and cross-machine from the Mac Studio. Remaining: wire
into an actual Hermes skill (see [Open items](#open-items)).

## Repo layout addition

```
hermes-agent-setup/
├── README_Proxmox.md
├── proxmox/
│   ├── proxmox_bridge.py          # FastAPI service: Proxmox lifecycle + guest-exec
│   ├── requirements.txt
│   └── .env.example                # copy to .env, fill in real token/host
├── skills/
│   └── security/
│       └── windows-binary-analysis/
│           ├── SKILL.md
│           └── scripts/
│               └── analyze_windows_binary.py   # start/exec/pull/destroy CLI, wraps the bridge
└── scripts/
    └── start-proxmox-bridge.sh    # venv setup + launch, mirrors the other scripts/*.sh
```

`proxmox/.env` (not `.env.example`) holds the real API token secret —
gitignored, same convention as `config/.env` in the main repo.

Install the skill by copying it into Hermes's actual skill directory (the
canonical source of truth per Hermes's own docs — this repo just keeps a
versioned copy, same pattern as `config/config.yaml` → `~/.hermes/config.yaml`):

```bash
cp -r skills/security/windows-binary-analysis ~/.hermes/skills/security/
```

## Hardware

| Component | Spec |
|---|---|
| Host | iMac Retina 5K, 27", Late 2015 (no T2 chip — no Secure Boot/external-media restrictions apply) |
| CPU | 3.2GHz quad-core Intel Core i5 |
| RAM | 24GB 1600MHz DDR3 |
| Hypervisor | Proxmox VE (bare metal) |

## Storage architecture

Three tiers, split by write pattern. The HDD is a **5400 RPM SMR** drive
(Seagate ST8000DM004) — tolerates sequential/read-mostly access well, degrades
badly under sustained random writes. This is why templates and working copies
live on different storage.

| Tier | Device | Proxmox storage ID | Type | Role |
|---|---|---|---|---|
| Host | Internal 128GB SSD | `local` | dir | Proxmox OS, ISOs, config only |
| Hot | External 240GB USB SSD (Buffalo, UAS) | `hot-ssd` | LVM-thin | Active/working VM disks — anything under snapshot/rollback churn |
| Cold | Internal 8TB HDD (SMR) | `vmdata` | ZFS pool | Golden templates (write-once, read-many), samples, backups |

### Per-exercise workflow

```bash
qm clone 9000 <new-id> --full 1 --storage hot-ssd --name exercise-<new-id>
# ... run the exercise, snapshot/rollback freely on hot-ssd ...
qm stop <new-id>
qm destroy <new-id>   # discard when done — no cumulative drift
```

**Full clones (not linked)** are required here since template (`vmdata`) and
working copy (`hot-ssd`) live on different storage backends. Expect the clone
itself to take a while — observed **30-60+ minutes for a 64GB template**
copying off the SMR source, even though it's the drive's best-case sequential
access pattern. This is why `proxmox_bridge.py`'s task-wait timeout is set to
2 hours, not a shorter default.

## Network architecture

Two bridges, kept structurally separate — `vmbr1` has **no physical uplink
at all**, not just a firewall rule:

```
# /etc/network/interfaces
auto vmbr1
iface vmbr1 inet manual
    bridge-ports none
    bridge-stp off
    bridge-fd 0
```

| Bridge | Attached to | Purpose |
|---|---|---|
| `vmbr0` | Physical NIC | Proxmox host management (web UI, API, SSH) |
| `vmbr1` | Nothing | Isolated network for analysis VMs — no route to LAN or internet |

Any VM analyzing untrusted binaries or CTF material attaches to `vmbr1` only.

## Golden template: `win10-golden` (VMID 9000)

**Japanese-edition Windows 10 22H2 x64** — not English + language pack. An
earlier attempt to retrofit JIS keyboard support onto an English install via
registry overrides, driver swaps, and offline `.cab` language packs all
failed to stick reliably; the native JP-edition ISO ships JIS as the default,
no retrofitting needed. If future templates don't need Japanese input, an
English ISO is simpler — this detour is specific to this operator's needs.

```bash
qm create 9000 \
  --name win10-golden \
  --memory 8192 --cores 4 --cpu host \
  --machine q35 --bios ovmf \
  --efidisk0 vmdata:0,efitype=4m,pre-enrolled-keys=0 \
  --scsihw virtio-scsi-pci \
  --scsi0 vmdata:64,cache=writeback,discard=on \
  --ide2 local:iso/<Windows-JP-ISO-filename>.iso,media=cdrom \
  --ide3 local:iso/virtio-win-<version>.iso,media=cdrom \
  --net0 virtio,bridge=vmbr1 \
  --vga std \
  --agent enabled=1,fstrim_cloned_disks=1 \
  --ostype win10
qm set 9000 --boot order=ide2;scsi0;net0   # ide2 first so a blank disk boots the installer
```

**q35 IDE limitation:** only two IDE optical slots are actually usable
(whatever's assigned to `ide2`/`ide3`) — a third (`ide0`, etc.) is silently
accepted into the config but never presents a device to the guest. Reuse
`ide2`/`ide3` for later media swaps (e.g. toolkit delivery ISO) rather than
adding new slots.

**OVMF boot-entry quirk:** a freshly created VM with an empty `scsi0` can
show `BdsDxe: failed to start Boot0002 "UEFI QEMU DVD-ROM..."` and fall
through to the interactive boot-device picker even with a valid, correctly
attached ISO. In practice this resolved itself once `scsi0` had no competing
bootable disk to fall back to — if it recurs, **Boot Maintenance Manager →
Boot From File → `\EFI\BOOT\BOOTX64.EFI`** boots directly, bypassing the
stale entry.

Toolkit installed via one-shot ISO transfer (build on the Proxmox host,
attach as temporary CD-ROM, copy in, detach):

```bash
genisoimage -o /var/lib/vz/template/iso/toolkit.iso -J -R /path/to/staged/installers
qm set 9000 --ide2 local:iso/toolkit.iso,media=cdrom
# install inside guest, then:
qm set 9000 --delete ide2
```

| Tool | Notes |
|---|---|
| Sysinternals Suite | Portable, no installer. **Accept each tool's EULA once interactively before snapshotting** — it's a blocking modal that would hang unattended `guest-exec` calls otherwise |
| x64dbg (snapshot build) | Portable. Confirm Windows Defender isn't flagging it before snapshotting (unsigned-binary heuristic false positives happen occasionally) |
| PE-bear | Portable; vs17 build needs VC++ 2015-2022 Redistributable installed alongside it |
| Ghidra | Needs a JDK first (Temurin/Eclipse Adoptium, matching whatever version the specific Ghidra release states) — **the release zip extracts with an extra top-level folder** (`ghidra_X.X_PUBLIC/`); move contents up one level or `ghidraRun.bat` won't be where expected. **Does not handle .NET/MSIL binaries well** — use ilspycmd instead for those (see below). Ghidra 12.x also changed its Python scripting runtime: `.py` postScripts written in classic Jython style need `# @runtime Jython` as the first line, and even then require the Jython extension to actually be installed separately — PyGhidra (the new default) needs its own headless bootstrap that isn't configured out of the box. In practice, plain `strings.exe` or `ilspycmd` got useful results faster than fighting Ghidra's scripting setup |
| .NET SDK + ilspycmd | For decompiling .NET (MSIL/CIL) binaries back to readable C# — see the `decompile` skill subcommand. Install the .NET SDK (not just the runtime) via the offline installer, then `dotnet tool install --global ilspycmd --tool-path C:\Tools\ilspy` (use `--tool-path`, not a plain `--global` install — the default global-tools location is under the per-user profile, which SYSTEM-context `guest-exec` calls can't see, same PATH pitfall as Python earlier). Add `C:\Tools\ilspy` to the machine-wide PATH |
| Python | Install **for all users**, not per-user — Hermes's later `guest-exec` calls run in the **SYSTEM** account context, which never sees a per-user `PATH` entry. `python-3.13.15-amd64.exe /passive InstallAllUsers=1 PrependPath=1` |

All tool directories added to the **machine-wide** `Path` (System variables,
not User variables) — same SYSTEM-context reasoning as Python above.

**Guest agent must be confirmed working before snapshotting** — this is what
`proxmox_bridge.py` depends on entirely:

```bash
qm agent 9000 ping
qm agent 9000 get-osinfo   # should return real Windows version info, not an error
```

If `qm config 9000 | grep agent` shows `enabled=1` but the guest doesn't
respond, a **full VM restart** (`qm shutdown` + `qm start`, not a
Windows-side reboot) is required — this is a QEMU launch-time device, not
hot-pluggable.

Once confirmed and toolkit installed:

```bash
qm shutdown 9000
qm snapshot 9000 clean-tooled
```

**`ide3` (the virtio-win reference) must be deliberately KEPT on the golden
template — do not delete it, even though it looks like leftover cruft from
template build.** Its presence as an *existing device* is required
infrastructure: the Hermes skill's large-file transfer path
(`analyze_windows_binary.py`'s ISO-attach mechanism) works by swapping
`ide3`'s media on a running clone, which is instant and reliable — but
QEMU does not hotplug a brand-new IDE device into a running VM under any
circumstances, confirmed by direct testing. If `ide3` doesn't exist on a
clone, the large-file push path breaks with no new drive letter ever
appearing, no matter how long you wait. Only `ide2` (the Windows installer
reference) should be deleted before the final snapshot — see the toolkit
section below.

## Bridge service (`proxmox/proxmox_bridge.py`)

Runs on the **Mac Studio**, not the Proxmox host — Hermes's Docker sandbox
reaches it via `host.docker.internal:8811`. Two channels to Proxmox, kept
deliberately separate:

- **VM lifecycle** (clone/start/stop/snapshot/rollback/destroy) → Proxmox
  REST API, authenticated via a scoped API token
- **In-guest exec + file transfer** → QEMU Guest Agent, relayed through
  Proxmox's `/agent/*` endpoints over virtio-serial — **never** the guest's
  own network interface, which stays air-gapped on `vmbr1` throughout

### One-time Proxmox-side setup

Create a scoped token — **do not reuse `root@pam`**:

```bash
pveum user add hermes-bridge@pve
pveum aclmod / -user hermes-bridge@pve -role PVEVMAdmin
pveum user token add hermes-bridge@pve bridge-token --privsep 0
```

The `value` field printed by the last command is `PVE_TOKEN_SECRET` —
**shown once, not retrievable afterward**, only regeneratable.

**`PVEVMAdmin` at `/` is not sufficient by itself** — three additional grants
were needed before a clone actually succeeded, each surfaced one at a time as
a separate `403`:

```bash
# Datastore.AllocateSpace — needed on clone source/destination
pveum aclmod /storage/hot-ssd -user hermes-bridge@pve -role PVEDatastoreUser
pveum aclmod /storage/vmdata  -user hermes-bridge@pve -role PVEDatastoreUser

# Datastore.AllocateTemplate — a distinct, more specific privilege than
# AllocateSpace, required for /storage/upload (the ISO-attach large-file
# transfer path writes here). PVEDatastoreUser does NOT include this —
# needs the broader PVEDatastoreAdmin role on this one storage pool.
pveum aclmod /storage/local -user hermes-bridge@pve -role PVEDatastoreAdmin

# SDN.Use — PVEVMAdmin does not carry this privilege under any path;
# needs a dedicated role
pveum role add SDNUser -privs "SDN.Use"
pveum aclmod /sdn/zones -user hermes-bridge@pve -role SDNUser
```

Confirm the final ACL set with `pveum acl list` before moving on.

**Firewall — restrict who can reach the API at all:** Datacenter → Firewall
→ allow TCP/8006 from the Mac Studio's IP only, default-drop otherwise. Do
this before running the bridge against anything but `localhost`.

### Running it

```bash
cp proxmox/.env.example proxmox/.env
# edit proxmox/.env — real PVE_HOST, PVE_TOKEN_SECRET, etc.
./scripts/start-proxmox-bridge.sh
```

### Verifying end-to-end

```bash
curl -X POST http://127.0.0.1:8811/vm/clone \
  -H "Content-Type: application/json" \
  -d '{"template_vmid": 9000, "new_vmid": 9101, "name": "bridge-test"}'
# expect a long wait — see clone-duration note above

curl -X POST http://127.0.0.1:8811/vm/9101/start
curl -X POST http://127.0.0.1:8811/vm/9101/exec \
  -H "Content-Type: application/json" \
  -d '{"command": ["cmd", "/c", "where", "x64dbg.exe"]}'
curl -X DELETE http://127.0.0.1:8811/vm/9101   # auto-stops first if still running
```

### Concurrency (multiple Hermes subagents, one shared VM)

Multiple subagents calling `exec`/`file` concurrently against **the same
`vmid`** is safe — each `/vm/{vmid}/exec` call spawns an independent
QEMU Guest Agent process, and uvicorn handles each request in its own
thread, so nothing in the bridge serializes concurrent calls against one
vmid. The primary should `clone`/`start` once and share that `vmid` with
subagents — never have each subagent clone its own VM (see the skill's
Pitfalls section for why: cost multiplies, and concurrent clones from the
same SMR source likely make each other slower, not just duplicate the
30-60 min wait).

**Known gap:** the skill script's default vmid picker
(`9100 + time.time() % 400`) has no collision check — fine for one shared
vmid across subagents of a single task, but two independent analysis
*sessions* started around the same moment could theoretically collide.
Pass an explicit `--vmid` if running more than one session concurrently.

## Open items

- [x] Wire the bridge into an actual Hermes skill — see
      `skills/security/windows-binary-analysis/`, install with
      `cp -r skills/security/windows-binary-analysis ~/.hermes/skills/security/`
- [x] Test `/vm/{vmid}/file` write/read endpoints directly — confirmed working
      both directions, including binary content; read side requires decoding
      the `content` field as `latin-1` (see troubleshooting table)
- [x] Confirm `host.docker.internal:8811` is reachable from inside a running
      `hermes-sandbox` container — verified via `curl -s
      http://host.docker.internal:8811/openapi.json` returning real JSON
- [ ] Consider a second golden template for Linux-based pwn challenges,
      also on `vmdata`, also cloned to `hot-ssd` per exercise
- [ ] Consider pre-cloning a small standby pool of ready VMs to avoid the
      30-60 min clone wait sitting in the critical path of every exercise

## First real use

Ask Hermes (chat or Discord) something like: *"There's a Windows pwn
challenge binary at `/workspace/samples/chall.exe` — analyze it in the
isolated VM and tell me what you find."* The `windows-binary-analysis` skill
should trigger automatically based on its description; it'll warn you about
the clone wait before starting.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| OVMF console shows `BdsDxe: failed to start ... DVD-ROM` on a fresh VM, falls to boot-device picker | Stale/generic boot entry, not a real device/ISO problem | Boot Maintenance Manager → Boot From File → `\EFI\BOOT\BOOTX64.EFI`; resolves once the OS is actually installed |
| Windows installer says "No signed device drivers were found" loading VirtIO SCSI driver | Browsed to the wrong folder depth | Path must be exactly `\vioscsi\w10\amd64`, not a level above |
| Attaching a third install-media ISO on a q35 VM silently does nothing | q35 only exposes 2 usable IDE optical slots | Reuse existing `ide2`/`ide3`, don't add `ide0`/`ide1` for optical media |
| `where <tool>.exe` fails inside guest even after `setx ... /M` | `setx` (or the modern Settings-app language/keyboard flow) needs internet in some code paths, or a stale environment in an already-running guest-agent service | For PATH specifically: edit via System Properties → Environment Variables → **System variables** GUI directly, or confirm with `reg query "HKLM\SYSTEM\...\Environment" /v Path` before assuming a failure |
| `qm agent <vmid> ping` returns "No QEMU guest agent configured" | `--agent enabled=1` not set, or set but VM not restarted | `qm set <vmid> --agent enabled=1`, then full `qm shutdown`/`qm start` — config-only changes to hardware need a real relaunch, not a guest-side reboot |
| Bridge clone call returns `403 Datastore.AllocateSpace` | Token's role doesn't include storage allocation | `pveum aclmod /storage/<name> -user hermes-bridge@pve -role PVEDatastoreUser` on both source and destination storage |
| Bridge clone call returns `403 SDN.Use` even after granting `PVEVMAdmin` on the SDN path | `PVEVMAdmin` doesn't carry `SDN.Use` under any path — it's simply not in that role | `pveum role add SDNUser -privs "SDN.Use"` then `pveum aclmod /sdn/zones -user hermes-bridge@pve -role SDNUser` |
| Bridge `exec` call fails with `Failed to execute child process (No such file or directory)` | Command list was joined into one string before sending — Proxmox's `/agent/exec` expects a repeated array parameter, not a single joined string | Already fixed in `proxmox_bridge.py` — send `[("command", part) for part in command_list]`, not `" ".join(...)` |
| Bridge `exec` call 500s with `binascii.Error: Incorrect padding` | Script attempted to base64-decode `out-data`, but Proxmox's API already returns it as plain decoded text | Already fixed in `proxmox_bridge.py` — use `status.get("out-data")` directly |
| Bridge `DELETE /vm/{vmid}` fails with "VM is running - destroy failed" | Proxmox refuses to destroy a running VM | Already fixed in `proxmox_bridge.py` — destroy endpoint auto-stops first if needed |
| `sc stop QEMU-GA & sc start QEMU-GA` via `qm guest exec` fails with `Agent error: PID ... does not exist` | **Fundamental QEMU Guest Agent limitation, not fixable in code**: a command that stops the agent service, issued *through that same agent*, kills the channel before it can complete or report back — there is no way to safely restart QGA remotely via guest-exec | Never attempt to restart the guest agent service via `guest exec`. Use a full VM restart instead whenever the guest environment needs to pick up something the running agent process cached at its own startup (e.g. a PATH change) — `qm shutdown 9000` (or `qm stop` if shutdown also fails to respond) then `qm start 9000`, then `qm agent 9000 ping` until it responds |
| Attaching a payload ISO to `ide2` via the bridge never becomes visible in the guest — no new drive letter appears, no matter how long you wait, even after a forced reboot | **Confirmed via direct manual testing**: QEMU does not hotplug a brand-new IDE device into a running VM at all — not a timing issue, a hard limitation. `ide2` doesn't exist as a device on any clone (deliberately deleted from the golden template before the final snapshot, see the toolkit section above), so attaching to it hits this non-hotpluggable "new device" case every time | Use `ide3` instead — it exists as a device on every clone because the golden template deliberately keeps it (see the golden-template section above — do not delete it). **Swapping media on an existing device is instant and reliable**, confirmed by direct testing. Fixed in `proxmox_bridge.py` (default `ide_slot` changed to `ide3`) and `analyze_windows_binary.py` (reboot cycle and letter-diffing removed entirely, no longer needed) |
| `/storage/upload` (from the ISO-attach large-file path) fails with `requests.exceptions.ConnectionError: RemoteDisconnected` — no HTTP status, connection just dies mid-request | Missing `Datastore.AllocateTemplate` on `/storage/local` specifically — this is a distinct, more specific privilege than `Datastore.AllocateSpace`, not covered by `PVEDatastoreUser`. pveproxy's own access log shows `403` for the upload, but the client never sees it as a clean error because pveproxy closes the connection mid-multipart-stream rather than returning a normal response body | `pveum aclmod /storage/local -user hermes-bridge@pve -role PVEDatastoreAdmin` (a superset role that includes `Datastore.AllocateTemplate`). **Diagnostic tip:** when a bridge call fails with a bare connection-reset and no response body, reproduce with a direct `curl` carrying the same token — unlike the bridge's own error handling, `curl` will show you Proxmox's actual permission-check message (`tail -20` on `curl -v` output), which is far more specific than what makes it back through a dropped connection |
| `GET /vm/{vmid}/file` response's `content` field doesn't look like the original bytes for binary files | Proxmox's `/agent/file-read` returns bytes mapped 1:1 onto Unicode codepoints (Latin-1/ISO-8859-1), not UTF-8 and not base64 despite QEMU GA's own spec saying base64 | Verified with a 256-byte all-byte-values test file + SHA256 comparison — decode the `content` field as **`latin-1`**, not `utf-8`: `raw_bytes = response_json["content"].encode("latin-1")` reconstructs the original file exactly |
| A full clone from `vmdata` (HDD) to `hot-ssd` takes 30-60+ minutes | Expected — SMR source, full (not linked) clone across different storage backends, copies the entire provisioned 64GB regardless of actual used data | Not a bug; `proxmox_bridge.py`'s task-wait timeout is set to 7200s to accommodate this |
| Skill's `start` times out at "Waiting for guest agent" even though the clone itself succeeded | Windows's first boot after a fresh clone can take longer than the original 300s wait — observed in practice, not just theoretically possible | `analyze_windows_binary.py`'s `wait_for_guest` timeout raised to 600s. If it still times out, check `qm status <vmid>` / the console directly before assuming something's broken |
| `POST /vm/{vmid}/file` (pushing a sample) returns `400 Bad Request` for any file over roughly 45KB | Proxmox's `agent/file-write` API validates its `content` parameter against a **hardcoded 61,440-byte limit**, independent of the general POST body size limit — confirmed on Proxmox's own support forum, not fixable from this bridge's side. Real CTF binaries routinely exceed this | `analyze_windows_binary.py`'s `push`/`start` auto-detect file size and route anything over 40,000 bytes through an ISO-attach path instead (`/storage/upload` + `/vm/{vmid}/cdrom`) — builds a real ISO9660 image via macOS's `hdiutil`, uploads it through Proxmox's storage-upload endpoint (no size ceiling), attaches as CD-ROM on `ide2`, has the guest copy the file off (auto-detecting the drive letter, not assuming `D:`), then detaches and cleans up |
| `/storage/upload` returns `400 No such file on bridge host: /workspace/...` | The script initially passed a **path** to the bridge for large files — but the bridge runs on the Mac Studio host, and `host.docker.internal` is a network route, not a filesystem bridge. The bridge process has no visibility into the sandbox container's filesystem, so a container-side path is meaningless to it | Fixed: `/storage/upload` now takes file **content** (base64), same pattern as the small-file `/vm/{vmid}/file` path. The helper script reads the file itself (it runs inside the container, where the file IS readable) and sends the bytes — no path is ever passed across the host/container boundary |

## Security notes

- `proxmox/.env` is gitignored — never commit it; `.env.example` has
  placeholders only, same convention as `config/.env.example`
- The `hermes-bridge@pve` token is scoped to VM lifecycle + the two specific
  storage pools + SDN zones — not `root@pam`, not a broader admin role
- The Proxmox API (port 8006) should be firewalled to the Mac Studio's IP
  only, same principle as `.env` never leaving the host
- Analysis VMs never have a network path to anything — `vmbr1` has no
  physical uplink, so this holds even if the bridge script itself were
  compromised
- If a "Windows binary" assignment starts looking like real malware rather
  than a scoped CTF challenge (persistence attempts, anti-VM evasion,
  unexpected outbound attempts even though the network is cut), stop,
  snapshot for offline analysis, and don't let automation continue
  unsupervised
