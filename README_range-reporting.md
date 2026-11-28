# Range assessment — operator reporting & inspection tools

Three read-only inspection tools for a range-assess sweep, plus a
one-command bundle that runs all three and collects their output into a
dated directory. None of them writes anything to the sweep or touches it;
run them any time, including mid-run.

| Tool | Answers |
|---|---|
| `range-status-report.py` | Where does the whole sweep stand? (coverage, the vulnerability funnel, the deployment-decision axis, attempts, timing, integrity) |
| `range-vuln-list.py` | Which specific findings were proven exploitable, and what state is each in? |
| `range-vuln-show.py` | For one finding: what was found, how it was proven, how it was fixed, and whether the fix held? |
| `range-report-bundle.py` | Run all three of the above and collect their output into one dated directory under the workspace. |

**Common to the three inspection tools:**
- Read-only. They only read the sweep's on-disk files; they never modify
  state, VMs, or anything else.
- `--workspace <dir>` points them at the sweep's workspace (the directory
  holding `.range_traversal_state.json` and the logs). It defaults to
  `/workspace` (the path inside the sandbox), so from the Mac you almost
  always pass `--workspace ~/hermes-agent-setup/workspace`.
- `--json` emits the same data as machine-readable JSON.
- Standard library only; nothing to install.

**Two things to know before reading any of them:**
- **Everything runs on disposable clones.** The whole assess→PoC→remediate→
  reverify loop happens on a throwaway clone of each host, never on the live
  host the golden image was built from. So `reverified_closed` does **not**
  mean "the live host is fixed" — it means *a remediation was proven to close
  the finding on the clone, and is ready to deploy.* Whether to deploy it is a
  separate, human decision. "closed"/"applied" everywhere below is always
  on-clone.
- **Two independent axes.** Axis 1 — *is there a proven fix?* (`reverified_closed`
  = yes; `remediation_planned` and the other non-closed statuses = not yet).
  Axis 2 — *does deploying it need a conscious operator call?* — carried on each
  finding as `needs_operator_decision` and shown as `⚑`. The two cross into the
  three tiers the status report and vuln-list now surface (see below).

The worked examples below are illustrative; counts are drawn from assorted
sweeps to show the shape of each section, not to reconcile across sections.

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
   (PROVEN exploitable findings only — informational inventory and
    not-reachable findings are NOT vulnerabilities; see below)
   Note: 'closed'/'applied' below means on the disposable clone, NOT on any
   live host — each is a proven, ready-to-deploy fix; deploying it is the
   operator's decision.
  Proven vulnerabilities                   55
      reverified_closed                53   fix proven effective on clone (re-verified)
      remediation_applied               0   fix applied on clone, re-verify not yet run
      reverify_inconclusive             0   fix applied on clone, re-verify inconclusive
      reverify_failed                   0   fix tried on clone but did NOT close it
      remediation_planned               2   remediation proposed, not proven
      poc_confirmed                     0   proven, no remediation proposed yet
      closed_manually                   0   operator closed by hand
      risk_accepted                     0   operator accepted the risk
   Remediation coverage of proven vulns:
    Fix tested on clone                    53/55 (96%)
    Fix proven effective                   53/55 (96%)
    Needs attention                        2   (poc_confirmed / remediation_planned / reverify_failed)
   Deployment decision — 2nd axis (⚑ = applying the fix is the operator's call):
      ⚑ proven fix, operator-decision       23   reverified_closed + flagged
      ⚑ no proven fix yet, operator-dec.     2   proposed/other + flagged
        proven fix, AI-judged routine       30   reverified_closed, not flagged
   For context (NOT vulnerabilities):
      poc_not_reachable               738   tested, not exploitable
      poc_inconclusive                  0   tested, couldn't determine
      discovered                        0   risk finding, PoC still pending
      informational                  2758   inventory (never a vuln claim)
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
`poc_confirmed`). In the example that's **55** of 3551. The rest were never
vulnerabilities: `informational` (2758, inventory, never PoC'd),
`poc_not_reachable` (738, tested and safe), `poc_inconclusive` (0), and
`discovered` (0, PoC pending). Reconcile: 55 + 738 + 0 + 0 + 2758 = 3551 — the
funnel always accounts for every finding.

**Three sub-statuses to read first:**
- `reverified_closed` — the gold standard: proven, fixed on the clone, and
  *re-demonstrated as closed*. A proven remediation ready to deploy — not a
  live host that is already patched.
- `reverify_failed` — the one to worry about: a fix was tried on the clone but
  it is **still** exploitable.
- `poc_confirmed` — proven, not yet remediated: the backlog.

**Remediation coverage** restates that as ratios over the proven findings:
*Fix tested on clone* (a remediation was applied on the clone), *Fix proven
effective* (the reverify re-ran the PoC and it no longer worked), *Needs
attention* (genuinely open — `poc_confirmed` / `remediation_planned` /
`reverify_failed`). `closed_manually` / `risk_accepted` appear only after you
run `resolve-review`; the agent never sets them.

**Deployment decision — the 2nd axis.** Having a proven fix (axis 1) is not the
same as that fix being safe to push unattended (axis 2). Each finding carries
`needs_operator_decision`; this block crosses it with "is the fix proven?" to
give the three tiers you actually act on:
- `⚑ proven fix, operator-decision` — a remediation is proven to close it, but
  deploying it is a conscious call (it changes intended design, or has
  consequences to weigh). E.g. a `%wheel NOPASSWD` grant that may be the real
  admin model, or SMB/445 on a domain controller that cannot simply be shut.
- `⚑ no proven fix yet, operator-decision` — flagged *and* still unproven
  (`remediation_planned`): genuinely pending, a human decides how to proceed.
- `proven fix, AI-judged routine` — proven fix, not flagged: the agent judged
  it safe to apply, though the operator still owns the final call.
(A fourth line, *no proven fix, not flagged*, prints only when a fix attempt
failed without being flagged — normally absent.)

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
python3 scripts/range-vuln-list.py --workspace <ws> --needs-decision
python3 scripts/range-vuln-list.py --workspace <ws> --json
```

- `--status <name>` restricts to one of the eight proven statuses.
- `--needs-decision` restricts to findings flagged `needs_operator_decision`
  (the `⚑` rows) — the ones where deploying the proven fix is a conscious
  operator call. Combine with `--status` to narrow further.
- `--json` emits the rows as JSON (each row carries `needs_operator_decision`).

## Example and how to read it

```
=== Proven vulnerabilities ===
Total proven: 53
Note: none of these have been applied beyond the disposable clone they were
proven on — each is a proven, ready-to-deploy fix; deploying it is the
operator's decision.  ⚑ = that deployment is a conscious operator call
(needs_operator_decision).
Summary by status:
  reverified_closed        53

-- reverified_closed  (fix proven effective on clone (re-verified))  [53] --
    9101  host9101         sudoers              f-68216aa23f  sudoers: ec2-user ALL=(ALL) NOPASSWD:ALL
  ⚑ 9105  host9105         sudoers              f-586205ce54  sudoers: %sudo ALL=(ALL) NOPASSWD: ALL
  ⚑ 9201  host9201         network              f-d0f8475e30  microsoft-ds on port 445/tcp
  → inspect any of the above:
      python3 range-vuln-show.py --workspace <ws> --vmid 9101 f-68216aa23f
```

Each row is: **`⚑`-flag · golden_vmid · host · category · finding_id · short
description**. A leading `⚑` marks `needs_operator_decision` — deploying that
proven fix is a conscious operator call; an unmarked row is one the agent
judged routine to apply. Each status block ends with a ready-to-run
`range-vuln-show` command (with the correct `--vmid` already filled in) so you
can drill into any finding by copy-paste.

Read `reverify_failed` first (a fix that didn't hold), then `poc_confirmed`
(the un-remediated backlog), then the `⚑` rows (`--needs-decision`) — the
proven fixes awaiting your deploy call — and finally the unflagged
`reverified_closed` to spot-check that the routine ones are genuinely fixed.

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
  --vmid 9101   host9101       status=remediation_applied
  --vmid 9102   host9102       status=reverify_failed
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
5. **Reverification** — a plain-English verdict derived from the status. For a
   `reverified_closed` finding it reads "FIX PROVEN ON CLONE — … ready to
   deploy; deploying it is the operator's decision," and the other statuses map
   to FAILED / INCONCLUSIVE / PENDING / NOT PROVEN / NOT REACHED. If the finding
   is flagged `needs_operator_decision`, a `⚑` callout here spells out that
   deploying the fix is a conscious operator call.
6. **Operator review** — shown only if `resolve-review` touched this
   finding (resolution, prior status, note).
7. **Files on disk** — which canonical result files are present vs missing.

Long fields (audit output, evidence) are capped at 4000 chars with a
pointer to the file, so the dossier stays readable in one scroll.

A `reverified_closed` finding, for example (the `maintainer`/`backup.sh` case
on `host9104`), shows the sudoers grant and the group-writable script in
section 1, the `su www-data … -w backup.sh → writable` PoC in section 3, the
fix in section 4 (chmod the script `0755` — killing the tamper vector while
leaving the legitimate `NOPASSWD: backup.sh` design in place), and the
reverify's `reachable: false` ("`su www-data test -w → closed`") as the closing
proof — all on the clone, everything you need to judge whether the fix is sound
and whether to deploy it, in one view.

---

# 4. `range-report-bundle.py` — the whole set in one command

A convenience wrapper that runs the three tools above and collects their
output into one dated directory, so a full report set is a single command
rather than many copy-pasted `range-vuln-show` invocations — handy for an
archive, a hand-off, or offline reading. Read-only on the sweep (it only
reads the workspace *through* the three tools) and standard-library only.

## Usage

```
python3 scripts/range-report-bundle.py --workspace ~/hermes-agent-setup/workspace
python3 scripts/range-report-bundle.py --workspace <ws> --out <dir>
python3 scripts/range-report-bundle.py --workspace <ws> --json
```

- `--workspace <ws>` — same as the other tools; defaults to `/workspace`.
- `--out <dir>` — override the output directory. Default
  `<workspace>/reports/range-report_<timestamp>/`, timestamped so repeated
  runs never clobber each other.
- `--scripts-dir <dir>` — where the three tools live; defaults to the
  directory holding `range-report-bundle.py` itself, so dropping all four
  into `scripts/` just works.
- `--json` — also save the JSON forms of the status report and the
  needs-decision list.

## What it writes

Into `<workspace>/reports/range-report_<timestamp>/`:

| File | From |
|---|---|
| `status_report.txt` | `range-status-report.py` |
| `vuln_list.txt` | `range-vuln-list.py` |
| `vuln_list_needs_decision.txt` | `range-vuln-list.py --needs-decision` |
| `vuln_list.json` | `range-vuln-list.py --json` — the manifest the dossiers are enumerated from |
| `vulns/<vmid>_<host>_<fid>.txt` | `range-vuln-show.py --vmid <vmid> <fid>`, one per **proven vulnerability** |
| `INDEX.md` | a table of everything generated, each vuln with its host / status / ⚑-flag and a link to its dossier |

The dossier subdirectory is `vulns/`, not `findings/`: it holds only the
proven vulnerabilities, whereas "findings" in the status report is the whole
population (including `informational` inventory and `poc_not_reachable`).

## Exit status

- `0` — every tool call succeeded.
- `1` — the workspace or one of the three tools is missing; nothing is generated.
- `2` — the bundle was produced but one or more individual tool calls failed;
  see `_errors.log` in the output directory. One failed `range-vuln-show`
  does not abort the rest.

---

# Typical workflow

1. **`range-status-report.py`** — get the shape of the sweep. Read four
   lines: host coverage, *Proven vulnerabilities* + *Fix proven effective*,
   *Needs attention* + `reverify_failed`, and the *Deployment decision* split
   (how many proven fixes await your deploy call vs are AI-judged routine).
2. **`range-vuln-list.py`** — turn the funnel counts into actual finding
   IDs. Start with `--status reverify_failed`, then `poc_confirmed`, then
   `--needs-decision` for the proven fixes that need your deploy call.
3. **`range-vuln-show.py`** — open the findings that matter, copy-pasting
   the inspect command straight from the list, and judge each PoC and fix on
   its own evidence — and, for a `⚑` finding, whether to deploy it.

To capture the whole set at once — for an archive, a hand-off, or offline
reading — run **`range-report-bundle.py`** instead; it produces every report
above into a dated directory with an `INDEX.md` that links each proven vuln to
its dossier.
