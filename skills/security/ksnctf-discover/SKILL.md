---
name: ksnctf-discover
description: Discover the full list of ksnctf problems from its homepage — one fetch, natural order, no sorting
version: 1.0.0
metadata:
  hermes:
    tags: [security, ctf, reconnaissance]
    category: security
---

## What this is

Fetches ksnctf's homepage and extracts the full list of problems from
its confirmed real, static HTML navigation dropdown — the same way a
human operator browsing the site would find them. One fetch, no
authentication, no JavaScript involved.

**Deliberately does not sort or filter by difficulty.** An earlier
design considered ordering problems by point value as a difficulty
proxy; reconsidered and dropped, since it assumed a "points" field that
won't generalize to other platforms and required a whole second
discovery stage (visiting every problem page individually) just to
establish the sort. This script returns problems in the platform's own
natural order — whatever ordering, if any, is wanted on top of that is
the traversal engine's job, not this skill's.

## Usage

```
python3 .../ksnctf_discover.py list
```

Outputs:
```json
{
  "problems": [
    {"id": "1", "title": "Test Problem", "url": "https://ksnctf.sweetduet.info/problem/1"},
    ...
  ],
  "count": 41
}
```

## Confirmed structure (verified directly, not assumed)

The homepage's problem-navigation dropdown is genuinely static,
server-rendered HTML — `<a class="dropdown-item" href="/problem/N">N:
Title</a>` for every problem. This was directly checked rather than
assumed, precisely because the same site's flag-submission mechanism
turned out to be entirely JavaScript-driven via a hidden API — the two
mechanisms could easily have worked the same way, and didn't. Worth
remembering as a general lesson for any future platform integration:
check each mechanism independently rather than assume one finding
generalizes to the rest of a site.

## What's genuinely untested here

This script has been written but not yet run against the live site.
Before trusting it:
1. Run `list` and confirm it returns all 41 known problems (or however
   many currently exist — the count may have grown since this was
   written).
2. Spot-check a few entries against known problems already validated
   elsewhere in this project — `id: "11"` should be titled "Riddle",
   `id: "35"` should be "Simple Auth II", `id: "13"` should be
   "Proverb".
3. If the count comes back suspiciously low or the `_warning` field
   fires, inspect the raw homepage HTML directly rather than assume the
   script's regex/selector logic is definitely right — page structures
   do change over time.
