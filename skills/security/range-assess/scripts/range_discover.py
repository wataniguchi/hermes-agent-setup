"""
range_discover.py — scan one target from inside the range and report open
ports/services. Stateless, same role as ksnctf_discover.py: it only
reports what it finds, in the platform's/range's own terms — it does not
decide what to do with the result or track anything across calls. That's
range_traversal.py's job (it calls this as a subprocess, the same way
ctf_traversal.py calls ksnctf_discover.py).

NEW, UNTESTED CODE — unlike ksnctf_discover.py and submit_flag.py, nothing
here has been run against a real target yet. In particular:
  - the nmap XML parsing path has not been exercised against this range's
    actual hosts (Windows hosts especially — expect different/noisier
    service banners than the Linux ones);
  - the stdlib fallback (no nmap) does a raw TCP connect only, with no
    service/version detection at all — treat its output as "port is open",
    nothing more.
Validate against one known host first (e.g. a Linux box you can also
check with your own `nmap` run by hand) before trusting this for the
whole range.

Usage:
    python3 range_discover.py scan --target <ip> --ports <spec> [--nmap-path nmap]

Outputs: {"target": "...", "reachable": bool, "scan_method": "...",
          "findings": [{"port": N, "proto": "tcp", "service": "...", "version": "..."}]}

A target that fails even a quick reachability check is reported with
reachable: false and an empty findings list — this does NOT mean the
host is down for good; the range's golden image may simply not be
cloned/started as a working VM yet. Check that before concluding
anything about the host itself.
"""
import argparse
import ipaddress
import json
import shutil
import socket
import subprocess
import sys
import xml.etree.ElementTree as ET

DEFAULT_PORTS = (
    "22,23,25,53,80,88,110,111,135,139,143,389,443,445,464,636,"
    "993,995,1433,1521,2049,3268,3269,3306,3389,5000,5432,5900,"
    "5985,5986,8080,8443,9389"
)


def quick_reachable(target: str, ports_to_try=(22, 80, 443, 445, 3389, 5985), timeout=1.5) -> bool:
    family = socket.AF_INET6 if ":" in target else socket.AF_INET
    for port in ports_to_try:
        try:
            with socket.socket(family, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                if s.connect_ex((target, port)) == 0:
                    return True
        except OSError:
            continue
    return False


def run_nmap(target: str, ports: str, nmap_path: str) -> str:
    cmd = [nmap_path, "-Pn", "-sV", "-T4", "-p", ports, "-oX", "-", target]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0:
        raise RuntimeError(f"nmap exited {proc.returncode}: {proc.stderr[:4000]}")
    return proc.stdout


def parse_nmap_xml(xml_text: str):
    findings = []
    root = ET.fromstring(xml_text)
    for host_el in root.findall("host"):
        ports_el = host_el.find("ports")
        if ports_el is None:
            continue
        for port_el in ports_el.findall("port"):
            state_el = port_el.find("state")
            if state_el is None or state_el.get("state") != "open":
                continue
            service_el = port_el.find("service")
            service = service_el.get("name", "") if service_el is not None else ""
            product = service_el.get("product", "") if service_el is not None else ""
            ver = service_el.get("version", "") if service_el is not None else ""
            version = " ".join(p for p in (product, ver) if p)
            findings.append({
                "port": int(port_el.get("portid")),
                "proto": port_el.get("protocol"),
                "service": service,
                "version": version,
            })
    return findings


def fallback_connect_scan(target: str, ports: str, timeout=1.0):
    findings = []
    family = socket.AF_INET6 if ":" in target else socket.AF_INET
    for token in ports.split(","):
        token = token.strip()
        if "-" in token:
            lo, hi = (int(x) for x in token.split("-"))
            port_range = range(lo, hi + 1)
        else:
            port_range = [int(token)]
        for port in port_range:
            try:
                with socket.socket(family, socket.SOCK_STREAM) as s:
                    s.settimeout(timeout)
                    if s.connect_ex((target, port)) == 0:
                        findings.append({"port": port, "proto": "tcp", "service": "", "version": ""})
            except OSError:
                continue
    return findings


def cmd_scan(target: str, ports: str, nmap_path: str):
    try:
        ipaddress.ip_address(target)
    except ValueError:
        print(json.dumps({"error": f"{target!r} is not a valid IP literal"}, indent=2))
        sys.exit(1)

    if not quick_reachable(target):
        print(json.dumps({
            "target": target, "reachable": False, "scan_method": None, "findings": [],
            "note": "Quick reachability check failed. If this is a range host, confirm "
                    "its working VM (<host>-ctf) is actually cloned and started via "
                    "proxmox_bridge.py before concluding the host itself is down.",
        }, indent=2))
        return

    resolved_nmap = shutil.which(nmap_path)
    if resolved_nmap:
        xml_text = run_nmap(target, ports, resolved_nmap)
        findings = parse_nmap_xml(xml_text)
        scan_method = "nmap -sV"
    else:
        findings = fallback_connect_scan(target, ports)
        scan_method = "stdlib TCP connect (nmap not found on PATH — no service/version detection)"

    print(json.dumps({
        "target": target, "reachable": True, "scan_method": scan_method, "findings": findings,
    }, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan")
    p_scan.add_argument("--target", required=True, help="IPv4 or IPv6 literal — the working VM's own address")
    p_scan.add_argument("--ports", default=DEFAULT_PORTS)
    p_scan.add_argument("--nmap-path", default="nmap")

    args = parser.parse_args()
    if args.command == "scan":
        cmd_scan(args.target, args.ports, args.nmap_path)


if __name__ == "__main__":
    main()
