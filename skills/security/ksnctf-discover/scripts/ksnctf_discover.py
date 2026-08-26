"""
ksnctf_discover.py — discover the full list of ksnctf problems from its
homepage, the way a human operator would: one page, one list, no
per-problem visits needed.

Confirmed via direct HTML inspection (see CTF_GENERALIZATION_DESIGN.md):
the homepage's problem-navigation dropdown is genuinely static,
server-rendered HTML containing every problem as
<a class="dropdown-item" href="/problem/N">N: Title</a> — no JavaScript
or API layer involved, unlike the flag-submission mechanism on this same
site, which turned out to be entirely JS-driven. This was checked
directly rather than assumed, given the two turned out to work
completely differently — worth remembering as a general lesson, not
just a fact about this one page.

Deliberately does NOT sort by any difficulty proxy (points, etc.) — see
CTF_GENERALIZATION_DESIGN.md for why an earlier draft that did this was
reconsidered and dropped. Problems are returned in the platform's own
natural listing order, exactly as found on the page. The traversal
engine that consumes this output is responsible for deciding what order
to actually attempt problems in, if any ordering beyond "as discovered"
is wanted at all.

Usage:
    python3 ksnctf_discover.py list

Outputs a JSON object: {"problems": [{"id", "title", "url"}, ...], "count": N}
"""
import sys
import re
import json
import requests

try:
    from bs4 import BeautifulSoup
    HAVE_BS4 = True
except ImportError:
    HAVE_BS4 = False

HOMEPAGE_URL = "https://ksnctf.sweetduet.info/"


def cmd_list():
    resp = requests.get(HOMEPAGE_URL, timeout=30)
    resp.raise_for_status()
    html = resp.text

    problems = []
    seen_ids = set()

    if HAVE_BS4:
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", class_="dropdown-item", href=True):
            m = re.match(r"^/problem/(\d+)$", a["href"])
            if not m:
                continue
            problem_id = m.group(1)
            if problem_id in seen_ids:
                # Defensive dedup: only one such list was directly
                # confirmed, but if the same problem appears in more
                # than one nav context on the page, don't double-count it.
                continue
            seen_ids.add(problem_id)

            text = a.get_text(strip=True)
            # Confirmed real link text format: "N: Title" — strip the
            # leading "N: " to get just the title.
            title_match = re.match(rf"^{problem_id}:\s*(.+)$", text)
            title = title_match.group(1) if title_match else text

            problems.append({
                "id": problem_id,
                "title": title,
                "url": f"{HOMEPAGE_URL.rstrip('/')}/problem/{problem_id}",
            })
    else:
        # Cruder fallback if bs4 isn't available — less reliable than
        # the DOM-based approach above.
        for m in re.finditer(
            r'<a class="dropdown-item" href="(/problem/(\d+))">(\d+):\s*([^<]*)</a>',
            html,
        ):
            problem_id = m.group(2)
            if problem_id in seen_ids:
                continue
            seen_ids.add(problem_id)
            problems.append({
                "id": problem_id,
                "title": m.group(4).strip(),
                "url": f"{HOMEPAGE_URL.rstrip('/')}/problem/{problem_id}",
            })

    result = {"problems": problems, "count": len(problems)}

    if not problems:
        result["_warning"] = (
            "No problems found at all. This likely means the homepage's "
            "structure has changed since this script was written, or "
            "bs4 isn't installed and the regex fallback didn't match — "
            "inspect the raw HTML directly rather than trust an empty "
            "result silently."
        )
    if not HAVE_BS4:
        result["_note"] = (
            "beautifulsoup4 is not installed — used a cruder regex "
            "fallback for HTML parsing, which is less reliable."
        )

    print(json.dumps(result, indent=2))


def main():
    if len(sys.argv) != 2 or sys.argv[1] != "list":
        print(__doc__)
        sys.exit(1)
    cmd_list()


if __name__ == "__main__":
    main()
