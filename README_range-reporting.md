# Range assessment — operator reporting & inspection tools

Three read-only, operator-facing tools for inspecting a range-assess
sweep. None of them writes anything or touches the sweep; run them any
time, including mid-run.

| Tool | Answers |
|---|---|
| `range-status-report.py` | Where does the whole sweep stand? (coverage, the vulnerability funnel, attempts, timing, integrity) |
| `range-vuln-list.py` | Which specific findings were proven exploitable, and what state is each in? |
| `range-vuln-show.py` | For one finding: what was found, how it was proven, how it was fixed, and whether the fix held? |

**Common to all three:**
- Read-only. They only read the sweep's on-disk files; they never modify
  state, VMs, or anything else.
- `--workspace <dir>` points them at the sweep's workspace (the directory
  holding `.range_traversal_state.json` and the logs). It defaults to
  `/workspace` (the path inside the sandbox), so from the Mac you almost
  always pass `--workspace ~/hermes-agent-setup/workspace`.
- `--json` emits the same data as machine-readable JSON.
- Standard library only; nothing to install.

The worked examples below come from a real sweep 7h in, with the first 2 of
21 hosts assessed.

---

# 1. `range-status-report.py` — overall sweep status

Where `range_traversal.py status` gives the agent a terse machine summary,
this is the operator's dashboard: coverage, a vulnerability funnel,
attempt-log stats, timing, and a data-integrity cross-check.

## Usage

```
python3 scripts/range-status-report.py --workspace ~/hermes-agent-setup/workspace
python3 scripts/range-status-report.py --workspace <ws> --json
```

It reads `.range_traversal_state.json` (authoritative state),
`.range_poc_log.json` / `.range_remediate_log.json` (attempt logs),
`result_*.json` / `remediation_result_*.json` (canonical result files,
cross-checked), and `.range-sweep-watchdog-output.log` (for the start
time). A missing log is a warning; only a missing state file is fatal.

## Reading each section

### Sweep runtime
```
-- Sweep runtime (JST) --
  Started    2026-11-24T23:47:13+09:00  (init)
  Elapsed    7h 13m
```
Real wall-clock since the current sweep's `init`. `(init)` = anchored to a
genuine fresh start; `(resume …)` = only a resumed attempt was found. This
is the true total runtime — distinct from the attempt-timing section lower
down, which only spans logged attempts.

### Host coverage
```
  Total hosts                 21
  Network scan status         not_started=19, scanned=2
  Local audit status          audited=2, not_started=19
  Hosts reset at least once   0 (0 resets total)
```
How many hosts have cleared each phase (`scanned` → `audited`).
`scan_failed`/`audit_failed` show here too. A rising reset count means
working clones are having to be rebuilt — worth a look.

### Findings
Every tracked finding by scope, raw status, and category. The raw material;
the funnel below interprets it.

### Vulnerability funnel (the main event)
```
-- Vulnerability funnel --
  Proven vulnerabilities                   16
      reverified_closed                 7   fixed & re-verified closed
      remediation_applied               4   fixed, reverify not yet run
      reverify_inconclusive             0   fixed, reverify couldn't confirm
      reverify_failed                   0   fix applied but STILL exploitable
      remediation_planned               0   remediation attempted, not applied
      poc_confirmed                     5   proven, no remediation yet
      closed_manually                   0   operator closed by hand
      risk_accepted                     0   operator accepted the risk
   Remediation coverage of proven vulns:
     Fix applied        11/16 (69%)
     Verified closed     7/16 (44%)
     Needs attention     5   (poc_confirmed / remediation_planned / reverify_failed)
   For context (NOT vulnerabilities):
      poc_not_reachable                81   tested, not exploitable
      poc_inconclusive                  3   tested, couldn't determine
      discovered                        0   risk finding, PoC still pending
      informational                    83   inventory (never a vuln claim)
```

**Why a funnel, not just a count.** Each finding has one *current* status,
but the lifecycle is a path, and a later stage implies passing the earlier
ones:
```
discovered → poc_confirmed → remediation_applied → reverified_closed
          ↘ poc_not_reachable / poc_inconclusive        (dead ends: not a vuln)
                                 ↘ reverify_failed       (fix didn't hold)
informational  → inventory, never enters this path at all
```

**Proven vulnerabilities = findings demonstrated exploitable** (ever reached
`poc_confirmed`). In the example that's **16** of 183. The rest were never
vulnerabilities: `informational` (83, inventory, never PoC'd),
`poc_not_reachable` (81, tested and safe), `poc_inconclusive` (3), and
`discovered` (0, PoC pending). Reconcile: 16 + 81 + 3 + 0 + 83 = 183 — the
funnel always accounts for every finding.

**Three sub-statuses to read first:**
- `reverified_closed` — the gold standard: proven, fixed, and
  *re-demonstrated as closed*.
- `reverify_failed` — the one to worry about: fixed but **still**
  exploitable.
- `poc_confirmed` — proven, not yet remediated: the backlog.

**Remediation coverage** restates that as ratios over the 16 proven: *Fix
applied* (a change is on the box), *Verified closed* (fixed and confirmed),
*Needs attention* (genuinely open). `closed_manually` / `risk_accepted`
appear only after you run `resolve-review`; the agent never sets them.

### PoC / Remediation attempts
```
  Findings with >=1 attempt   100
  Average attempts/finding    1.11
  At/above cap (3)            9601:f-a2171ef30b
```
Attempt-log view. Average ~1.0 means most findings resolve in a single
attempt. A key under **At/above cap** has hit the 3-report limit and gets
no more PoC attempts; its last status (usually `poc_inconclusive`) is final.
The remediation block's `Applied` should match `Fix applied` in the funnel.

### Attempt timing — NOT total runtime
Spans only first-to-last *logged attempt*; excludes clone/boot/scan/audit
time, so it reads shorter than the real runtime. For wall-clock, use
**Sweep runtime** at the top.

### Data integrity
Cross-checks state against the result files on disk. `(none)` on all three
is healthy; an entry means a finding's status says it was PoC'd/remediated
but its result file is missing or misnamed.

---

# 2. `range-vuln-list.py` — list the proven vulnerabilities

Lists every finding that was **proven exploitable**, grouped by its current
status (the eight proven statuses). Inventory, not-reachable, and
not-yet-tested findings are excluded — use the funnel above for those
counts.

## Usage

```
python3 scripts/range-vuln-list.py --workspace ~/hermes-agent-setup/workspace
python3 scripts/range-vuln-list.py --workspace <ws> --status reverify_failed
python3 scripts/range-vuln-list.py --workspace <ws> --json
```

- `--status <name>` restricts to one of the eight proven statuses.
- `--json` emits the rows as JSON.

## Example and how to read it

```
=== Proven vulnerabilities ===
Total proven: 16
Summary by status:
  poc_confirmed            5
  remediation_applied      4
  reverified_closed        7
  reverify_failed          0
  … (all eight listed, including zeros)

-- reverified_closed  (fixed & re-verified closed)  [7] --
  9101  ecs              sudoers              f-aaa1111111  sudoers: gt ALL=(ALL) NOPASSWD:ALL
  9101  ecs              suid_binaries        f-bbb2222222  suid_binaries: /usr/local/bin/backup
  → inspect any of the above:
      python3 range-vuln-show.py --workspace <ws> --vmid 9101 f-aaa1111111
```

Each row is: **golden_vmid · host · category · finding_id · short
description**. Each status block ends with a ready-to-run `range-vuln-show`
command (with the correct `--vmid` already filled in) so you can drill into
any finding by copy-paste.

Read `reverify_failed` first (a fix that didn't hold), then `poc_confirmed`
(the un-remediated backlog), then `reverified_closed` to spot-check that the
closed ones are genuinely fixed.

---

# 3. `range-vuln-show.py` — one finding in full

The complete dossier for a single finding, in one report: identity, what
the scan/audit found, the PoC that proved it, the remediation applied, the
reverification outcome, any operator review, and the result files on disk.

## Usage

```
python3 scripts/range-vuln-show.py --workspace <ws> <finding_id>
python3 scripts/range-vuln-show.py --workspace <ws> --vmid <golden_vmid> <finding_id>
```

**A finding ID is not unique across hosts.** It's a hash of
category+command, not of the host, so the same `f-…` ID can appear on two
unrelated hosts. If the ID you pass matches more than one, the tool lists
the matches and asks you to add `--vmid <golden_vmid>`:

```
Finding f-shared9999 exists on 2 hosts — add --vmid <golden_vmid> to pick one:
  --vmid 9101   ecs            status=remediation_applied
  --vmid 9102   velociraptor   status=reverify_failed
```

(The `range-vuln-list.py` inspect-hint always includes the right `--vmid`,
so copy-pasting from the list is never ambiguous.)

## What it shows

Sections, in order:

1. **Identity** — host, golden vmid, OS, address, working clone, scope,
   category/kind, and the `working_vmid:finding_id` key the logs and result
   files use.
2. **What was found** — for a local finding, the audit command and its
   output; for a network finding, the service/version/port. This is the
   condition that *existed*, before any exploitation.
3. **Proof of concept** — the recorded `poc_method` and `poc_evidence`, the
   **full attempt log** (each attempt's `reachable` verdict — so a
   `true` PoC followed by a `false` reverify is visible as two lines), and
   the canonical `result_<wv>_<fid>.json` file.
4. **Remediation** — the `remediation_summary`, attempt count,
   `needs_operator_decision`, the remediation attempt log, and the
   `remediation_result_<wv>_<fid>.json` file.
5. **Reverification** — a plain-English verdict derived from the status
   (CLOSED / FAILED / INCONCLUSIVE / PENDING / NOT REACHED).
6. **Operator review** — shown only if `resolve-review` touched this
   finding (resolution, prior status, note).
7. **Files on disk** — which canonical result files are present vs missing.

Long fields (audit output, evidence) are capped at 4000 chars with a
pointer to the file, so the dossier stays readable in one scroll.

A `reverified_closed` finding, for example, shows the sudoers grant in
section 2, the `su - gt; sudo -n id → uid=0(root)` PoC in section 3, the
"removed the NOPASSWD grant" fix in section 4, and the reverify's
`reachable: false` ("gt is not in the sudoers file") as the closing proof —
everything you need to judge whether the fix is sound, in one view.

---

# Typical workflow

1. **`range-status-report.py`** — get the shape of the sweep. Read three
   lines: host coverage, *Proven vulnerabilities* + *Verified closed*, and
   *Needs attention* + `reverify_failed`.
2. **`range-vuln-list.py`** — turn the funnel counts into actual finding
   IDs. Start with `--status reverify_failed`, then `poc_confirmed`.
3. **`range-vuln-show.py`** — open the findings that matter, copy-pasting
   the inspect command straight from the list, and judge each PoC and fix on
   its own evidence.
