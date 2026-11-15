#!/usr/bin/env python3
"""
Data Receipt stress-test scorer (tests 3, 5 and 6).

Reads a dnsmasq query log, maps every looked-up name to a company and
category using Disconnect's open tracker list, and prints a receipt plus
pass/fail checks.

Usage:
  python3 receipt_test.py /tmp/dns.log services.json
  python3 receipt_test.py /tmp/dns.log services.json --client 10.42.0.57
  python3 receipt_test.py /tmp/dns.log services.json --last 60     # last 60 s only
  python3 receipt_test.py /tmp/dns.log services.json --watch       # live, refresh every 2 s
  python3 receipt_test.py tiktok.csv services.json --export          # PCAPdroid / any domain list

Get services.json once (while online):
  curl -L -o services.json https://raw.githubusercontent.com/disconnectme/disconnect-tracking-protection/master/services.json
"""
import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime

# dnsmasq lines look like:
#   Nov 14 14:05:01 dnsmasq[1234]: query[A] graph.facebook.com from 10.42.0.57
LINE = re.compile(
    r"^(?:(?P<ts>\w{3}\s+\d+\s+\d\d:\d\d:\d\d))?.*?query\[(?P<qtype>[^\]]+)\]\s+"
    r"(?P<name>\S+)\s+from\s+(?P<client>\S+)"
)

# Names every phone looks up that are not trackers; shown separately.
SYSTEM_HINTS = (
    "connectivitycheck.gstatic.com", "connectivitycheck.android.com",
    "clients3.google.com", "captive.apple.com", "time.android.com",
    "android.pool.ntp.org", "mtalk.google.com",
)

# Never count these as third parties (the demo apps' own domains go here too).
FIRST_PARTY_DEFAULT = ()


PRIORITY = ["Advertising", "Analytics", "FingerprintingInvasive",
            "FingerprintingGeneral", "Social", "Cryptomining",
            "Anti-fraud", "ConsentManagers", "EmailAggressive", "Email",
            "Content"]
RANK = {c: i for i, c in enumerate(PRIORITY)}


def load_tracker_map(path):
    """domain -> (company, category). Uses the most serious category seen."""
    data = json.load(open(path))
    rank = RANK
    out = {}
    for category, entries in data["categories"].items():
        for entry in entries:
            for company, sites in entry.items():
                for _home, domains in sites.items():
                    if not isinstance(domains, list):
                        continue
                    for d in domains:
                        d = d.lower().strip(".")
                        old = out.get(d)
                        if old is None or rank.get(category, 99) < rank.get(old[1], 99):
                            out[d] = (company, category)
    return out


# Mobile ad SDKs that Disconnect's (web-focused) list misses. Seen in real
# Android captures at MadHacks. Add more lines as you find them.
EXTRA = {
    "unity3d.com": ("Unity Ads", "Advertising"),
    "unityads.unity3d.com": ("Unity Ads", "Advertising"),
    "vungle.com": ("Liftoff (Vungle)", "Advertising"),
    "moloco.com": ("Moloco", "Advertising"),
    "bidmachine.io": ("BidMachine", "Advertising"),
    "inner-active.mobi": ("Digital Turbine (Fyber)", "Advertising"),
    "fyber.com": ("Digital Turbine (Fyber)", "Advertising"),
    "mtgglobals.com": ("Mintegral", "Advertising"),
    "inmobi.com": ("InMobi", "Advertising"),
    "superawesome.tv": ("SuperAwesome", "Advertising"),
    "kwcdn.com": ("Kwai (Kuaishou) Ads", "Advertising"),
    "applovin.com": ("AppLovin", "Advertising"),
    "applvn.com": ("AppLovin", "Advertising"),
    "ironsrc.mobi": ("ironSource (Unity)", "Advertising"),
    "supersonicads.com": ("ironSource (Unity)", "Advertising"),
    "chartboost.com": ("Chartboost", "Advertising"),
    "pangle.io": ("Pangle (ByteDance)", "Advertising"),
    "pangleglobal.com": ("Pangle (ByteDance)", "Advertising"),
    "googlesyndication.com": ("Google", "Advertising"),
    "googleadservices.com": ("Google", "Advertising"),
    "googletagservices.com": ("Google", "Advertising"),
    "doubleclick.net": ("Google", "Advertising"),
    "firebaselogging.googleapis.com": ("Google", "Analytics"),
    "firebaselogging-pa.googleapis.com": ("Google", "Analytics"),
    "app-measurement.com": ("Google", "Analytics"),
    "crashlytics.com": ("Google", "Analytics"),
    "appsflyer.com": ("AppsFlyer", "Analytics"),
    "adjust.com": ("Adjust (AppLovin)", "Analytics"),
    "branch.io": ("Branch", "Analytics"),
    "helpshift.com": ("Helpshift", "Content"),
    "graph.facebook.com": ("Meta", "Advertising"),
}


def lookup(name, tmap):
    """Match name or any parent domain: a.b.doubleclick.net -> doubleclick.net."""
    parts = name.lower().strip(".").split(".")
    for i in range(len(parts) - 1):
        cand = ".".join(parts[i:])
        if cand in EXTRA:
            return EXTRA[cand]
        if cand in tmap:
            return tmap[cand]
    return None


def parse_log(path, client=None, last=None, year=None):
    year = year or datetime.now().year
    now = datetime.now()
    rows = []
    with open(path, errors="replace") as f:
        for line in f:
            m = LINE.search(line)
            if not m:
                continue
            if client and m["client"] != client:
                continue
            if last and m["ts"]:
                try:
                    ts = datetime.strptime(f"{year} {m['ts']}", "%Y %b %d %H:%M:%S")
                    if (now - ts).total_seconds() > last:
                        continue
                except ValueError:
                    pass
            rows.append((m["ts"], m["qtype"], m["name"].lower().strip("."), m["client"]))
    return rows


DOMAIN = re.compile(r"\b((?:[a-z0-9-]+\.)+[a-z]{2,24})\b", re.I)


def parse_domains(path, app=None):
    """PCAPdroid CSV (uses Info + App columns) or any text list of domains."""
    import csv
    rows = []
    with open(path, errors="replace") as f:
        head = f.readline()
        f.seek(0)
        if "Info" in head and "App" in head:
            for r in csv.DictReader(f):
                d = (r.get("Info") or "").lower().strip(".")
                if not d or not DOMAIN.fullmatch(d):
                    continue
                if app and app.lower() not in (r.get("App") or "").lower():
                    continue
                rows.append(("", r.get("Proto", ""), d, r.get("App", "?")))
            return rows
        for line in f:
            for d in DOMAIN.findall(line):
                d = d.lower()
                if d.endswith((".arpa", ".local", ".lan")):
                    continue
                rows.append(("", "export", d, "phone"))
    return rows


def report(rows, tmap, first_party):
    names = Counter(r[2] for r in rows)
    clients = Counter(r[3] for r in rows)
    companies = defaultdict(lambda: {"category": "", "hits": 0, "domains": set()})
    unknown, system = Counter(), Counter()

    for name, n in names.items():
        if any(name == s or name.endswith("." + s) for s in SYSTEM_HINTS):
            system[name] += n
            continue
        if any(name == fp or name.endswith("." + fp) for fp in first_party):
            continue
        hit = lookup(name, tmap)
        if hit:
            co, cat = hit
            cur = companies[co]["category"]
            if not cur or RANK.get(cat, 99) < RANK.get(cur, 99):
                companies[co]["category"] = cat
            companies[co]["hits"] += n
            companies[co]["domains"].add(name)
        else:
            unknown[name] += n

    print("=" * 60)
    print(f"Lookups logged: {len(rows)}   distinct names: {len(names)}")
    print("Clients seen:", ", ".join(f"{c} ({n})" for c, n in clients.most_common()))
    print("-" * 60)

    by_cat = defaultdict(list)
    for co, info in companies.items():
        by_cat[info["category"]].append((info["hits"], co, info["domains"]))
    print(f"RECEIPT: contacted {len(companies)} known tracking/third-party companies")
    for cat in sorted(by_cat, key=lambda c: -sum(h for h, _, _ in by_cat[c])):
        items = sorted(by_cat[cat], reverse=True)
        print(f"\n  {cat} ({len(items)})")
        for hits, co, doms in items:
            print(f"    {co:<28} x{hits:<4} {', '.join(sorted(doms))[:60]}")

    if unknown:
        print("\n  Not in the database (first party or unknown):")
        for name, n in unknown.most_common(15):
            print(f"    {name:<45} x{n}")
    if system:
        print("\n  Phone system checks (ignored):", ", ".join(system))

    third = len(companies) + len(unknown)
    known_names = sum(len(i["domains"]) for i in companies.values())
    total_names = known_names + len(unknown)
    rate = known_names / total_names * 100 if total_names else 0.0

    print("\n" + "-" * 60)
    print("CHECKS")
    print(f"  Test 3  lookups appear (10+):           "
          f"{'PASS' if len(rows) >= 10 else 'FAIL'}  ({len(rows)})")
    print(f"  Test 5  names matched to a company:     "
          f"{rate:.0f}% of {total_names} non-system names "
          "(add the app's own domains with --first-party, then aim for 70%+)")
    print(f"  Test 6  companies in this session (8+): "
          f"{'PASS' if len(companies) >= 8 else 'not yet'}  ({len(companies)})")
    if not rows:
        print("\n  No lookups. Check: phone on DigIn? mobile data OFF? "
              "Private DNS set to Automatic or Off? log-queries enabled?")
    print("=" * 60)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log")
    ap.add_argument("services")
    ap.add_argument("--client", help="only this phone's IP, e.g. 10.42.0.57")
    ap.add_argument("--last", type=int, help="only the last N seconds")
    ap.add_argument("--first-party", default="",
                    help="comma list of the app's own domains, e.g. tiktokv.com,tiktok.com")
    ap.add_argument("--watch", action="store_true", help="refresh every 2 s")
    ap.add_argument("--app", help="with --export: only this app, e.g. Subway")
    ap.add_argument("--export", action="store_true",
                    help="LOG is a phone export (PCAPdroid CSV or any text list of domains)")
    a = ap.parse_args()

    tmap = load_tracker_map(a.services)
    fp = tuple(d.strip().lower() for d in a.first_party.split(",") if d.strip()) or FIRST_PARTY_DEFAULT

    while True:
        rows = parse_domains(a.log, a.app) if a.export else parse_log(a.log, a.client, a.last)
        if a.watch:
            print("\033[2J\033[H", end="")
        report(rows, tmap, fp)
        if not a.watch:
            break
        time.sleep(2)


if __name__ == "__main__":
    sys.exit(main())
