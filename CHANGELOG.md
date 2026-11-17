# Setup history — model, context, and concurrency decisions

This is history, not current-state documentation — it exists for "why
is it configured this way?" questions. It is not where to look for
whether something works *today*; `README.md` and `config/config.yaml`
state only present-tense facts and point here for the backstory. (For
`range-assess`-specific history, see
`CHANGELOG.range-assess-extension.md` instead — this file covers the
Hermes model/delegation setup shared across skills.)

Entries are in the order they happened.

**Delegation model: `gemma4:e4b` → `qwen3.8:27b-delegate`.** The
original delegation model, `gemma4:e4b`, was replaced entirely by a
smaller quant of the same primary model family (`qwen3.8:27b`),
motivated by wanting real reasoning on delegated PoC/remediate work
rather than a fast-but-shallow model, while still fitting comfortably
alongside the 21GB primary. Measured (not guessed) at the time, with
`num_ctx=16384`: 14GB resident, flat regardless of 3-way, 6-way, or
16-way concurrent requests (confirmed via real wall-clock overlap
analysis, not inferred from request duration); real decode throughput
also flat, ~17-19 tok/s aggregate, across that same range — this
hardware time-slices concurrent generations rather than batching them,
so more parallel slots don't buy more tokens/sec on their own; the
actual case for concurrent delegation is overlapping GPU-idle time
during a subagent's own tool-call waits (SSH/nmap/sqlmap round-trips),
not a throughput multiplier. `delegation.max_concurrent_children` was
raised from `3` to `6` on this basis — concurrency showed no ceiling,
so `6` was chosen for I/O-overlap headroom, not because measurement
found a limit there.

**`16384` context broke Hermes's own delegation requirement — not
discovered until the first real concurrency validation (2026-11-16).**
Every `delegate_task` call had been silently failing: Hermes refuses
delegation outright against any model served with a context window
under 64K, which `qwen3.8:27b-delegate` at `16384` did not meet. This
had not been documented anywhere, by Hermes or in this repo, before it
surfaced as a hard failure mid-validation. Fixed by re-tagging
`qwen3.8:27b-delegate` at `num_ctx=65536`. Once fixed, the same
validation session ran 3 genuinely concurrent `delegate_task`
subagents against `range_traversal.py`'s `next-batch`/claim mechanism
for real (not synthetic) — confirmed disjoint claims across 3 hosts,
each claim auto-cleared by its own `record-scan`/`record-audit` call,
and genuine wall-clock overlap (63 min total vs. 139 min sequential,
with all 3 working clones live simultaneously). See
`skills/security/range-assess/SKILL.md`'s own validation notes for
that mechanism-level detail.

**Re-measured at the real `65536` context (same day):** resident
memory came back at 16GB — not the ~4x jump a naive KV-cache scaling
estimate might suggest from the `16384`-ctx figure — flat across
6-way and 10-way genuinely concurrent requests, with zero swap growth
under load at either level (`sysctl vm.swapusage` unchanged from
idle). Aggregate decode throughput also stayed flat, 14-19 tok/s,
matching the original `16384`-ctx pattern. Neither 6-way nor 10-way
showed any sign of an actual ceiling. `delegation.max_concurrent_children`
was settled at `10` — a deliberately conservative stopping point once
10-way was confirmed as clean as 6-way, not a measured hardware limit;
testing stopped there rather than chasing the real ceiling, since 10
was already comfortably past near-term need.
