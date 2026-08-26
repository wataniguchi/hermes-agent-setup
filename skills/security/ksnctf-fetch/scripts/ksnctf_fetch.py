"""
challenge_fetch.py — fetch a CTF challenge page, classify its delivery
mode(s), download any files found, and check any live-target URL against
an explicitly-provided scope. Built against ksnctf's confirmed real
structure (see CTF_GENERALIZATION_DESIGN.md) but genuinely untested
against live HTML — validate with a real dry run before trusting this
for actual challenge work.

Scope is NEVER hardcoded or auto-derived from the page. It must be
explicitly passed via --scope, sourced by the agent from AGENTS.md's
"Current CTF challenge scope" section. This script does not read
AGENTS.md itself — that's a deliberate design choice (see the design
doc): free-form prose is fragile to machine-parse, so the agent (which
already reads AGENTS.md every session) is responsible for extracting the
current scope and passing it along explicitly.

Usage:
    python3 challenge_fetch.py fetch <problem_url> [--scope <allowed_host>] [--output-dir <dir>]

Outputs structured JSON: title/points/release-date metadata, which
delivery mode(s) were detected, any downloaded file paths, any extracted
web-app target URL (flagged if it doesn't match --scope), and any
extracted SSH connection details.
"""
import sys
import os
import re
import json
import argparse
from urllib.parse import urljoin, urlparse

import requests

try:
    from bs4 import BeautifulSoup
    HAVE_BS4 = True
except ImportError:
    HAVE_BS4 = False

# Confirmed real file extensions seen across actual ksnctf problems —
# not just executables. See CTF_GENERALIZATION_DESIGN.md for the
# specific examples this was grounded against.
DOWNLOADABLE_EXTENSIONS = {
    ".exe", ".zip", ".tar.gz", ".tgz", ".bin",
    ".pcap", ".pcapng", ".docx", ".apk", ".cpp",
    ".html", ".txt", ".pdf", ".jpg", ".png", ".gif",
}

# Confirmed consistent real template: "ssh <user>@<host> -p <port>"
# followed by a "Password: <pw>" line.
SSH_PATTERN = re.compile(
    r"ssh\s+(?P<user>\S+)@(?P<host>\S+)\s+-p\s+(?P<port>\d+)"
    r".*?Password:\s*(?P<password>\S+)",
    re.DOTALL,
)


def fetch_page(url: str) -> str:
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text


def extract_metadata(html: str, page_title: str) -> dict:
    # CONFIRMED BUG, found via real dry-run testing: points and release
    # date live in separate HTML elements with no literal character
    # joining them (a <span> inside an <h2> for points; a wholly
    # separate <div> for the date) — the middle-dot originally assumed
    # to connect them came from a rendered/text-extracted view of the
    # page, not the actual source HTML. Using proper DOM-based
    # extraction instead of a cross-tag regex, since it's more robust
    # against the next page having slightly different structure too.
    points = None
    released = None

    if HAVE_BS4:
        soup = BeautifulSoup(html, "html.parser")
        for span in soup.find_all("span"):
            text = span.get_text(strip=True)
            m = re.match(r"(\d+)\s*points", text)
            if m:
                points = int(m.group(1))
                break
        for tag in soup.find_all(string=re.compile(r"Released at:")):
            m = re.search(r"Released at:\s*([\d/]+)", tag)
            if m:
                released = m.group(1)
                break
    else:
        m = re.search(r"(\d+)\s*points", html)
        if m:
            points = int(m.group(1))
        m = re.search(r"Released at:\s*([\d/]+)", html)
        if m:
            released = m.group(1)

    return {"title": page_title, "points": points, "released": released}


def extract_downloadable_files(html: str, base_url: str) -> list:
    urls = []
    if HAVE_BS4:
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if any(href.lower().endswith(ext) for ext in DOWNLOADABLE_EXTENSIONS):
                urls.append(urljoin(base_url, href))
    else:
        # Fallback if bs4 isn't available — cruder, less reliable.
        for href in re.findall(r'href=["\']([^"\']+)["\']', html):
            if any(href.lower().endswith(ext) for ext in DOWNLOADABLE_EXTENSIONS):
                urls.append(urljoin(base_url, href))
    return list(dict.fromkeys(urls))  # de-dupe, preserve order


def extract_web_app_target(html: str, scope: str) -> dict:
    result = {"found": False, "url": None, "in_scope": None}
    if not scope:
        return result

    # Look for any URL in the page whose host matches the given scope —
    # deliberately does NOT try to guess a target without an explicit
    # scope to check against, per the design decision to never
    # auto-derive scope from page content.
    for url in re.findall(r'https?://[^\s"\'<>]+', html):
        host = urlparse(url).netloc
        if host == scope:
            result["found"] = True
            result["url"] = url
            result["in_scope"] = True
            return result
    return result


def extract_ssh_access(html: str) -> dict:
    match = SSH_PATTERN.search(html)
    if not match:
        return {"found": False}
    return {
        "found": True,
        "user": match.group("user"),
        "host": match.group("host"),
        "port": int(match.group("port")),
        "password": match.group("password"),
    }


def download_files(urls: list, output_dir: str) -> list:
    os.makedirs(output_dir, exist_ok=True)
    downloaded = []
    for url in urls:
        filename = os.path.basename(urlparse(url).path) or "downloaded_file"
        dest = os.path.join(output_dir, filename)
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        with open(dest, "wb") as f:
            f.write(resp.content)
        downloaded.append(dest)
    return downloaded


def cmd_fetch(url: str, scope: str, output_dir: str):
    html = fetch_page(url)

    page_title = url
    if HAVE_BS4:
        soup = BeautifulSoup(html, "html.parser")
        if soup.title:
            page_title = soup.title.string

    result = {
        "url": url,
        "metadata": extract_metadata(html, page_title),
        "modes_detected": [],
    }

    file_urls = extract_downloadable_files(html, url)
    if file_urls:
        result["modes_detected"].append("downloadable_file")
        result["downloaded_files"] = download_files(file_urls, output_dir)

    web_app = extract_web_app_target(html, scope)
    if web_app["found"]:
        result["modes_detected"].append("embedded_web_app")
        result["web_app_target"] = web_app

    ssh_access = extract_ssh_access(html)
    if ssh_access["found"]:
        result["modes_detected"].append("direct_ssh_access")
        result["ssh_access"] = ssh_access

    if not result["modes_detected"]:
        result["_note"] = (
            "No delivery mode detected via current heuristics — this "
            "could mean an untested page structure, missing bs4, or a "
            "genuinely unusual challenge. Inspect the raw page content "
            "directly rather than assuming nothing is there."
        )

    if not HAVE_BS4:
        result["_warning"] = (
            "beautifulsoup4 is not installed — used a cruder regex "
            "fallback for HTML parsing, which is less reliable. Add "
            "beautifulsoup4 to the Docker image for full functionality."
        )

    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_fetch = sub.add_parser("fetch")
    p_fetch.add_argument("url")
    p_fetch.add_argument("--scope", default=None,
                          help="Allowed attack-target host, sourced from "
                               "AGENTS.md's current scope section — never "
                               "hardcoded or guessed")
    p_fetch.add_argument("--output-dir", default="/workspace/samples")

    args = parser.parse_args()

    if args.command == "fetch":
        cmd_fetch(args.url, args.scope, args.output_dir)


if __name__ == "__main__":
    main()
