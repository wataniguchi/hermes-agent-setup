#!/usr/bin/env python3
"""Correlate agent actions with Hermes context-compression events, read
directly from a Hermes session-export markdown file (true action
granularity, not the ~30s periodic tail-snapshots in range-sweep.log).

The session-export .md is a plain sequence of sections:

    ### Assistant — 2026-11-22T04:04:09.004474Z
    <optional prose>
    ## Tool calls
    ```json
    [ { "function": {"name": "...", "arguments": "..."} }, ... ]
    ```

    ### Tool — <tool_name> — 2026-11-22T04:04:09.014059Z
    {"output": ...} / {"error": ...}

Each Assistant entry's tool call(s) are paired, in order, with the Tool
result entries that immediately follow it (before the next Assistant
entry). For each call this script reports:

  - the exact timestamp
  - the tool name and a short preview of its key argument (the `command`
    for `terminal`, the `content` for `write_file`, truncated before any
    compression marker)
  - model_latency: time from the previous tool RESULT to this call being
    ISSUED (i.e. how long the model took to decide on this action)
  - tool_duration: time from this call being issued to its own result
    (i.e. how long the tool itself took to run)
  - compression: the (omitted, total) char counts if a
    `HERMES-CONTEXT-COMPRESSION` marker is present in this call's own
    arguments (never another call's - this is attributed per-call, exactly)
  - refused: whether the matching Tool result was a
    `suspected_pruned_tool_arguments` refusal

Usage:
    range-sweep-session-correlate.py <session_export.md>
    range-sweep-session-correlate.py --workspace <dir> --hermes-home <dir> --profile <name>
        (scans EVERY session-export file under
         <hermes-home>/profiles/<profile>/session-exports/, not just the
         watchdog's current one - a long-running sweep spans many restarts,
         each getting its own export file, and
         <workspace>/.range-sweep-current-session-id only ever points at the
         latest one. Files are processed in chronological order (by their
         own filename timestamp prefix) and --start/--end filter across all
         of them, so a window like "--start 2026-11-23T11:17:57" spanning
         several restarts now sees everything, not just the newest session.
         Pass an explicit <session_export.md> instead to inspect one file
         only.)

--start/--end (JST, inclusive) accept three forms, and can mix forms
(e.g. --start with a bare time, --end with a full date+time):
    HH:MM[:SS]                  time-of-day; the date defaults to the
                                 export's own earliest day for --start,
                                 its own latest day for --end - so on a
                                 session spanning more than one calendar
                                 day, a bare time alone no longer matches
                                 that time on EVERY day in the file.
    YYYY-MM-DD                  a whole day: 00:00:00 for --start,
                                 23:59:59.999999 for --end.
    YYYY-MM-DD[T ]HH:MM[:SS]    an exact moment, unambiguous across days
                                 (e.g. "2026-11-22 22:00" or
                                 "2026-11-23T08:00:00").

Exit codes: 0 = ran; 1 = could not find/read the export file, or no
sections in it, or a --start/--end value didn't match any accepted form.
"""
import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone


JST = timezone(timedelta(hours=9))

HEADER_RE = re.compile(r"^### (Assistant|Tool) — (.+)$", re.M)
COMPRESSION_RE = re.compile(
    r"⟪HERMES-CONTEXT-COMPRESSION:\s*([\d,]+)\s*of\s*([\d,]+)\s*chars omitted"
)
REFUSAL_RE = re.compile(r'"error":\s*"suspected_pruned_tool_arguments"')
JSON_FENCE_RE = re.compile(r"```json\s*\n(.*?)\n```", re.S)
KEY_PREVIEW_RE = re.compile(r'"(command|content)"\s*:\s*"')


def parse_ts(s):
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
)
DATE_ONLY_FORMATS = ("%Y-%m-%d",)
TIME_ONLY_FORMATS = ("%H:%M:%S", "%H:%M")


def parse_bound(s, default_date, end_of_day_if_date_only=False):
    """Parse a --start/--end value into a JST-aware datetime.

    Accepts, in order of preference:
      - a full date+time: "2026-11-23 07:30", "2026-11-23T07:30:00"
      - a bare date: "2026-11-23" (00:00:00 for --start, 23:59:59.999999
        for --end when end_of_day_if_date_only is True)
      - a bare time: "07:30" or "07:30:00" (original behavior - the date
        is filled in from `default_date`, which the caller sets to the
        session export's own earliest JST date for --start and latest
        JST date for --end, so a time-only bound still does something
        sane on a session that spans more than one calendar day)
    """
    for fmt in DATETIME_FORMATS:
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=JST)
        except ValueError:
            pass
    for fmt in DATE_ONLY_FORMATS:
        try:
            d = datetime.strptime(s, fmt).replace(tzinfo=JST)
            if end_of_day_if_date_only:
                d = d.replace(hour=23, minute=59, second=59, microsecond=999999)
            return d
        except ValueError:
            pass
    for fmt in TIME_ONLY_FORMATS:
        try:
            t = datetime.strptime(s, fmt)
            return default_date.replace(
                hour=t.hour, minute=t.minute, second=t.second, microsecond=0
            )
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(
        f"expected HH:MM[:SS], YYYY-MM-DD, or YYYY-MM-DD[T ]HH:MM[:SS] (JST), got {s!r}"
    )


def in_range(ts_jst, start, end):
    if start is not None and ts_jst < start:
        return False
    if end is not None and ts_jst > end:
        return False
    return True


FILENAME_TS_RE = re.compile(r"^(\d{8}_\d{6})")
# The timestamp + session-id prefix, WITHOUT the trailing "-<slug>.md".
# Hermes can re-save the export for the SAME session under a new
# auto-generated slug as a long session continues - those files share
# this prefix and must be de-duplicated (keep the freshest only), not
# treated as separate sessions.
SESSION_KEY_RE = re.compile(r"^(\d{8}_\d{6}_[0-9a-zA-Z]+)")


def _filename_sort_key(path):
    m = FILENAME_TS_RE.match(os.path.basename(path))
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y%m%d_%H%M%S")
        except ValueError:
            pass
    # Fall back to mtime for any file that doesn't follow the usual
    # <timestamp>_<id>-<slug>.md naming (shouldn't normally happen).
    return datetime.fromtimestamp(os.path.getmtime(path))


def _session_key(path):
    m = SESSION_KEY_RE.match(os.path.basename(path))
    return m.group(1) if m else os.path.basename(path)


def resolve_export_paths(args):
    """Return every DISTINCT session's export file to scan, oldest first.

    A single explicit export_file argument means "just this one file".
    Otherwise, scan every export file for the profile - not just the
    watchdog's current session - since a long sweep spans many restarts,
    each with its own export file, and nothing else tracks that history.

    Several files can share the same session id (same timestamp+id
    prefix, different auto-generated slug) when hermes re-saves the same
    still-running session's export under a new title. Those are NOT
    separate sessions - keep only the most recently modified file per
    session key (the fullest snapshot), or the same calls get printed
    once per stale snapshot found.
    """
    if args.export_file:
        return [args.export_file]
    pattern = os.path.join(
        args.hermes_home, "profiles", args.profile, "session-exports", "*.md",
    )
    matches = glob.glob(pattern)
    if not matches:
        raise FileNotFoundError(f"no session-export files matching {pattern}")
    best_by_key = {}
    for m in matches:
        key = _session_key(m)
        if key not in best_by_key or os.path.getmtime(m) > os.path.getmtime(best_by_key[key]):
            best_by_key[key] = m
    deduped = list(best_by_key.values())
    deduped.sort(key=_filename_sort_key)
    return deduped


def extract_preview(args_raw, limit=90):
    """Best-effort preview of a tool call's key argument value, stopping
    before any compression marker or after `limit` chars, whichever first.
    Falls back to a plain prefix of the raw arguments string if no known
    key is found (unknown tool, or structure too mangled to anchor on).
    """
    m = KEY_PREVIEW_RE.search(args_raw)
    start = m.end() if m else 0
    chunk = args_raw[start:start + limit * 2]
    comp_pos = chunk.find("⟪HERMES-CONTEXT-COMPRESSION")
    if comp_pos != -1:
        chunk = chunk[:comp_pos]
    chunk = chunk[:limit]
    # Light unescaping for readability only - not a full JSON decode,
    # since the string may be truncated/corrupted mid-token.
    chunk = chunk.replace("\\n", " ").replace("\\\"", '"').replace("\\u0026", "&")
    return chunk.rstrip()


def parse_calls(assistant_body):
    """Return a list of (tool_name, args_raw, compression_hit) for every
    call in this Assistant section's tool-calls JSON block, if any.
    compression_hit is (omitted, total) ints, or None.
    """
    fence = JSON_FENCE_RE.search(assistant_body)
    if not fence:
        return []
    raw_json = fence.group(1)
    calls = []
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError:
        # The outer array can itself be malformed if compression landed
        # right at a structural boundary. Fall back to regex-scanning
        # individual "name"/"arguments" pairs rather than giving up.
        parsed = None
    if parsed is not None:
        for item in parsed:
            fn = item.get("function", {})
            name = fn.get("name", "?")
            args_raw = fn.get("arguments", "")
            calls.append((name, args_raw))
    else:
        for m in re.finditer(
            r'"name":\s*"([^"]+)"\s*,\s*"arguments":\s*"(.*?)"\s*\}', raw_json, re.S
        ):
            calls.append((m.group(1), m.group(2)))

    out = []
    for name, args_raw in calls:
        cm = COMPRESSION_RE.search(args_raw)
        comp_hit = (cm.group(1), cm.group(2)) if cm else None
        out.append((name, args_raw, comp_hit))
    return out


def read_sections(path):
    """Parse one export file into (kind, ts, name, body) section tuples.
    Returns None (after printing a warning) if the file can't be read or
    has no recognizable Assistant/Tool sections.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as e:
        print(f"# warning: could not read {path}: {e}", file=sys.stderr)
        return None

    matches = list(HEADER_RE.finditer(text))
    sections = []
    for i, m in enumerate(matches):
        kind = m.group(1)
        rest = m.group(2)
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end]
        if kind == "Tool":
            tool_name, _, ts_str = rest.rpartition(" — ")
            ts = parse_ts(ts_str)
            sections.append(("tool", ts, tool_name.strip(), body))
        else:
            ts = parse_ts(rest)
            sections.append(("assistant", ts, None, body))

    if not sections:
        print(f"# warning: no Assistant/Tool sections found in {path}", file=sys.stderr)
        return None
    return sections


def process_sections(sections, start, end, args):
    """Print correlated call lines for one file's sections, honoring the
    already-resolved global start/end bounds. model_latency resets at the
    start of each file, since it's a fresh hermes process each time.
    """
    last_tool_ts = None
    i = 0
    while i < len(sections):
        kind, ts, name, body = sections[i]
        if kind != "assistant":
            i += 1
            continue
        calls = parse_calls(body)
        # Gather the run of Tool sections immediately following, up to
        # the next Assistant section, to pair with these calls in order.
        j = i + 1
        tool_results = []
        while j < len(sections) and sections[j][0] == "tool":
            tool_results.append(sections[j])
            j += 1

        ts_jst = ts.astimezone(JST)

        for idx, (call_name, args_raw, comp_hit) in enumerate(calls):
            if not in_range(ts_jst, start, end):
                continue
            result = tool_results[idx] if idx < len(tool_results) else None
            refused = bool(result and REFUSAL_RE.search(result[3]))
            model_latency = (ts - last_tool_ts).total_seconds() if last_tool_ts else None
            tool_duration = (result[1] - ts).total_seconds() if result else None

            has_signal = bool(comp_hit) or refused
            if args.only_compression and not has_signal:
                continue

            preview = extract_preview(args_raw)
            ml = f"{model_latency:6.1f}s" if model_latency is not None else "   n/a"
            td = f"{tool_duration:6.1f}s" if tool_duration is not None else "   n/a"
            line = (
                f"{ts_jst.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]} JST  "
                f"{call_name:12s}  model_latency={ml}  "
                f"tool_duration={td}  \"{preview}\""
            )
            if has_signal:
                comp_str = f"{comp_hit[0]} of {comp_hit[1]} chars" if comp_hit else "-"
                flags = []
                if comp_hit:
                    flags.append("COMPRESSION")
                if refused:
                    flags.append("REFUSED")
                line += f"  compression=[{comp_str}]  {','.join(flags)}"
            print(line)

        if tool_results:
            last_tool_ts = tool_results[-1][1]
        else:
            last_tool_ts = ts
        i = j


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("export_file", nargs="?", default=None)
    ap.add_argument("--workspace")
    ap.add_argument("--hermes-home", default=os.path.expanduser("~/.hermes"))
    ap.add_argument("--profile", default="qwen-experiment")
    ap.add_argument("--only-compression", action="store_true",
                     help="print only calls with a compression marker and/or refusal")
    ap.add_argument("--start", default=None,
                     help="JST lower bound, inclusive: HH:MM[:SS] (date defaults to the "
                          "earliest day across ALL scanned exports), YYYY-MM-DD, or "
                          "YYYY-MM-DD[T ]HH:MM[:SS]")
    ap.add_argument("--end", default=None,
                     help="JST upper bound, inclusive: HH:MM[:SS] (date defaults to the "
                          "latest day across ALL scanned exports), YYYY-MM-DD, or "
                          "YYYY-MM-DD[T ]HH:MM[:SS]")
    args = ap.parse_args()

    try:
        paths = resolve_export_paths(args)
    except Exception as e:
        print(f"error resolving export file(s): {e}", file=sys.stderr)
        return 1

    per_file_sections = {}
    all_ts_jst = []
    for path in paths:
        sections = read_sections(path)
        if sections is None:
            continue
        per_file_sections[path] = sections
        all_ts_jst.extend(s[1].astimezone(JST) for s in sections)

    if not per_file_sections:
        print("no usable session-export files found", file=sys.stderr)
        return 1

    earliest_jst, latest_jst = min(all_ts_jst), max(all_ts_jst)

    try:
        start = (
            parse_bound(args.start, default_date=earliest_jst)
            if args.start else None
        )
        end = (
            parse_bound(args.end, default_date=latest_jst, end_of_day_if_date_only=True)
            if args.end else None
        )
    except argparse.ArgumentTypeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if start is not None:
        print(f"# --start resolved to {start.isoformat()}", file=sys.stderr)
    if end is not None:
        print(f"# --end resolved to {end.isoformat()}", file=sys.stderr)

    # paths is already oldest-first (resolve_export_paths sorts by the
    # export filename's own timestamp prefix); process in that order so
    # output stays chronological across a multi-restart sweep.
    for path in paths:
        sections = per_file_sections.get(path)
        if sections is None:
            continue
        # Skip files entirely outside the requested window - cheap check
        # using this file's own min/max, before printing its source line.
        file_ts_jst = [s[1].astimezone(JST) for s in sections]
        file_min, file_max = min(file_ts_jst), max(file_ts_jst)
        if end is not None and file_min > end:
            continue
        if start is not None and file_max < start:
            continue
        print(f"# source: {path}", file=sys.stderr)
        process_sections(sections, start, end, args)

    return 0


if __name__ == "__main__":
    sys.exit(main())
