#!/usr/bin/env python3
"""
dns/detector.py - rules-v1 "whack-a-mole" detector (owner: Aarav). Stdlib only, runs on the Uno Q.

Idea: when Shield blocks an app's usual ad companies, the app quietly falls back to other ad
networks that no list names. So:

  MAIN SIGNAL (required): a domain that NEVER appeared in any Shield-OFF session of the same app,
  but shows up in a Shield-ON session AFTER Shield blocked an ad company.
  SUPPORTING (raise the score, never flag on their own):
    - ad words as whole name parts: ssp, vast, impression, creative, ads, track, pixel, rtb
    - seen across unrelated apps
    - CNAME chain ends at a known tracker
  NEVER FLAG: names already on a list (Shield handles those), the allowlist, the app's own company,
  phone background traffic, plain CDNs / cloud hosts (cloudfront, akamai, fastly, ...) unless their
  CNAME points to a tracker.

A session counts as Shield-ON if Shield blocked at least one lookup in it (sessions has no shield
column). Domains are compared by their registrable name (ntusus.bidease.com -> bidease.com), so
first-party subdomains (*.afafb.com) seen with Shield OFF are never "new".

Writes ml_flags with model_version 'rules-v1', status 'suspected', score 0-1, plain-English reasons.
Never overwrites rows from other model versions; keeps a person's Block/Dismiss choice on our rows.

  python3 dns/detector.py --db digin.db --dry-run            # print what it would flag
  python3 dns/detector.py --db digin.db --session 5 --dry-run
  python3 dns/detector.py --db digin.db --loop 5             # live on the box
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "dashboard"))

import company  # noqa: E402  (dashboard/company.py: background apps, name parts)

try:
    from ml import lists as _lists
except Exception:  # noqa: BLE001
    _lists = None

MODEL_VERSION = "rules-v1"
DEFAULT_DB = os.environ.get("DIGIN_DB", "/home/arduino/digin/digin.db")
ALLOWLIST_PATH = os.path.join(HERE, "allowlist.txt")

AD_WORDS = {"ssp", "vast", "impression", "creative", "ads", "track", "pixel", "rtb"}
# Plain CDNs and cloud hosts: anyone's content can sit behind them.
INFRA = ("cloudfront.net", "akamai.net", "akamaiedge.net", "akamaized.net", "akamaihd.net", "akadns.net",
         "edgekey.net", "edgesuite.net", "fastly.net", "fastlylb.net", "cdn77.org", "cloudflare.net",
         "cloudflare.com", "azureedge.net", "trafficmanager.net", "amazonaws.com", "elasticbeanstalk.com",
         "googleusercontent.com", "googleapis.com", "gstatic.com", "aliyuncs.com", "aliyuncsslbintl.com",
         "alicdn.com", "jsdelivr.net", "llnwd.net", "b-cdn.net",
         # Microsoft Azure (Knife Hit's own API on kapublicapiprod.azurewebsites.net was a false flag)
         "azurewebsites.net", "azure.com", "cloudapp.net", "windows.net", "azurefd.net", "msedge.net",
         "azure-api.net", "core.windows.net",
         "firebaseapp.com", "web.app", "herokuapp.com", "digitaloceanspaces.com", "oraclecloud.com")
TWO_PART_SUFFIX = {"co", "com", "net", "org", "gov", "ac", "edu"}


def base(name):
    """Registrable name: ntusus.bidease.com -> bidease.com, a.b.co.uk -> b.co.uk."""
    p = name.lower().strip(".").split(".")
    if len(p) >= 3 and p[-2] in TWO_PART_SUFFIX and len(p[-1]) == 2:
        return ".".join(p[-3:])
    return ".".join(p[-2:])


def parents(name):
    p = name.split(".")
    return [".".join(p[i:]) for i in range(len(p) - 1)]


def is_infra(name):
    return any(name == s or name.endswith("." + s) for s in INFRA)


# Names that can't reach the internet (no data can leave): e.g. Subway Surfers' Moloco SDK tried
# sdk.moloco.local when its real servers were blocked; the lookup got no answer.
NON_PUBLIC = ("local", "lan", "home", "internal", "localhost", "invalid", "arpa", "localdomain", "home.arpa")


def is_non_public(name):
    return any(name == s or name.endswith("." + s) for s in NON_PUBLIC)


def read_allow(path=ALLOWLIST_PATH):
    out = set()
    try:
        for line in open(path):
            d = line.split("#")[0].strip().lower()
            if d:
                out.add(d)
    except OSError:
        pass
    return out


class Lists:
    """Thin, failure-proof wrapper over ml/lists.py (honours demo_holdout)."""
    def __init__(self, conn):
        self.L = None
        if _lists is not None:
            try:
                self.L = _lists.load_live(conn)
            except Exception:  # noqa: BLE001
                try:
                    self.L = _lists.load()
                except Exception:  # noqa: BLE001
                    self.L = None

    def _call(self, fn, *a):
        try:
            return getattr(self.L, fn)(*a) if self.L is not None else None
        except Exception:  # noqa: BLE001
            return None

    def tracker(self, name):
        return bool(self._call("is_tracker", name))

    def company(self, name):
        return self._call("company", name)

    def allowlisted(self, name):
        return bool(self._call("is_allowlisted", name))

    def same_company(self, name, app):
        return bool(app) and bool(self._call("same_company", name, app))


def own_company(name, app, L):
    """The app's own company: lists.same_company, or the app's own domains from apps.json."""
    if L.same_company(name, app):
        return True
    try:
        info = (L.L.apps or {}).get((app or "").strip().lower()) if L.L is not None else None
        doms = (info or {}).get("domains", [])
        return any(name == d or name.endswith("." + d) for d in doms)
    except Exception:  # noqa: BLE001
        return False


def load(conn):
    sessions = {sid: {"app": (app or "").strip(), "start": st, "end": en}
                for sid, app, st, en in conn.execute("SELECT session_id, app_name, start_ts, end_ts FROM sessions")}
    rows = defaultdict(list)  # session_id -> [(ts, qname, chain, blocked)]
    for sid, ts, q, ch, b in conn.execute(
            "SELECT session_id, ts, qname, cname_chain, blocked FROM raw_lookups"
            " WHERE session_id IS NOT NULL ORDER BY ts"):
        try:
            chain = json.loads(ch) if ch else []
        except ValueError:
            chain = []
        rows[sid].append((ts, q.lower().strip("."), [c.lower().strip(".") for c in chain], b))
    return sessions, rows


def detect(conn, only_session=None):
    """-> list of flag dicts: domain, score, reasons, app, examples."""
    L = Lists(conn)
    allow = read_allow()
    sessions, rows = load(conn)

    by_app = defaultdict(list)
    for sid, s in sessions.items():
        by_app[s["app"].lower()].append(sid)
    shield_on = {sid for sid, r in rows.items() if any(b for _, _, _, b in r)}

    # every base domain -> set of apps it appeared in (for the "unrelated apps" clue)
    apps_of = defaultdict(set)
    for sid, r in rows.items():
        for _, q, _, _ in r:
            apps_of[base(q)].add(sessions.get(sid, {}).get("app", "").lower())

    found = {}
    for app_key, sids in by_app.items():
        off = [s for s in sids if s not in shield_on and rows.get(s)]
        if not off:
            continue  # no Shield-OFF baseline for this app: the main signal can't be judged
        seen_off = {base(q) for s in off for _, q, _, _ in rows[s]}
        for sid in sids:
            if sid not in shield_on or (only_session is not None and sid != only_session):
                continue
            app = sessions[sid]["app"]
            first_block = None  # first time Shield blocked an AD company in this session
            for ts, q, chain, b in rows[sid]:
                if b:
                    co = L.company(q)
                    if co and co[1] == "Advertising":
                        first_block = (ts, co[0])
                        break
            if not first_block:
                first_block = next(((ts, q) for ts, q, _, b in rows[sid] if b), None)
            if not first_block:
                continue
            for ts, q, chain, b in rows[sid]:
                if b or ts < first_block[0]:
                    continue
                d = base(q)
                if d in seen_off:
                    continue
                names = [q] + chain
                if is_non_public(q) or company.is_system(q) or company.background(names, app):
                    continue
                if any(p in allow for p in parents(q)) or L.allowlisted(q):
                    continue
                if L.tracker(q) or L.tracker(d):
                    continue  # already on a list: Shield's job, not a new catch
                if own_company(q, app, L) or own_company(d, app, L):
                    continue
                co = L.company(q) or L.company(d)
                if co and company.by_function([q], co[1]) == "Content":
                    continue  # a known, non-tracking company (e.g. Microsoft cloud) without ad words
                tracker_hop = next((c for c in chain if L.tracker(c)), None)
                if is_infra(q) and not tracker_hop:
                    continue
                f = found.setdefault(d, {"domain": d, "app": app, "sessions": set(), "examples": [],
                                         "delay": ts - first_block[0], "blocked_first": first_block[1],
                                         "off_count": len(off), "tracker_hop": None})
                f["sessions"].add(sid)
                if q not in f["examples"]:
                    f["examples"].append(q)
                f["tracker_hop"] = f["tracker_hop"] or tracker_hop

    out = []
    for d, f in found.items():
        score = 0.5
        n_off = f["off_count"]
        reasons = [f"never seen in {n_off} Shield-OFF {f['app']} session{'s' if n_off > 1 else ''}; "
                   f"showed up {f['delay']:.0f} s after Shield blocked {f['blocked_first']}"]
        words = sorted(set().union(*(company.parts(e) for e in f["examples"])) & AD_WORDS)
        if words:
            score += 0.2
            reasons.append("ad words in its name: " + ", ".join(words))
        other_apps = sorted(a for a in apps_of[d] if a and a != f["app"].lower())
        if other_apps:
            score += 0.15
            reasons.append(f"also seen in {len(other_apps)} unrelated app{'s' if len(other_apps) > 1 else ''}"
                           f" ({', '.join(other_apps[:3])})")
        if f["tracker_hop"]:
            score += 0.2
            reasons.append(f"its CNAME points to a known tracker ({f['tracker_hop']})")
        if len(f["sessions"]) > 1:
            score += 0.05
            reasons.append(f"came back in {len(f['sessions'])} Shield-ON sessions")
        out.append({"domain": d, "score": round(min(score, 0.95), 2), "reasons": reasons,
                    "app": f["app"], "examples": f["examples"][:4], "sessions": sorted(f["sessions"])})
    out.sort(key=lambda x: -x["score"])
    return out


def write_flags(conn, flags, prune=True):
    """Upsert our rows only. Rows from other model versions are never touched; on our own rows a
    person's status choice (auto_blocked / dismissed) is kept."""
    now = time.time()
    written = 0
    with conn:
        for f in flags:
            cur = conn.execute(
                "INSERT INTO ml_flags(domain, score, reasons, status, model_version, flagged_ts, updated_ts)"
                " VALUES (?, ?, ?, 'suspected', ?, ?, ?)"
                " ON CONFLICT(domain) DO UPDATE SET score=excluded.score, reasons=excluded.reasons,"
                " updated_ts=excluded.updated_ts WHERE ml_flags.model_version=?",
                (f["domain"], f["score"], json.dumps(f["reasons"]), MODEL_VERSION, now, now, MODEL_VERSION))
            written += cur.rowcount
        # Drop our own stale suggestions that the rules no longer produce (e.g. after a fix).
        # Rows a person acted on (auto_blocked / dismissed) and other models' rows stay.
        if prune:
            keep = [f["domain"] for f in flags]
            sql = "DELETE FROM ml_flags WHERE model_version=? AND status='suspected'"
            if keep:
                sql += f" AND domain NOT IN ({','.join('?' * len(keep))})"
            conn.execute(sql, (MODEL_VERSION, *keep))
    return written


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--session", type=int, help="only judge this Shield-ON session")
    p.add_argument("--dry-run", action="store_true", help="print flags, don't write ml_flags")
    p.add_argument("--loop", type=float, help="run every N seconds")
    a = p.parse_args()
    me = os.path.abspath(__file__)
    started = os.path.getmtime(me)
    while True:
        if a.loop and os.path.getmtime(me) != started:  # code updated: reload without needing sudo
            print("[detector] code changed, reloading", flush=True)
            os.execv(sys.executable, [sys.executable, me] + sys.argv[1:])
        t0 = time.time()
        try:
            conn = sqlite3.connect(a.db, timeout=3)
            conn.execute("PRAGMA busy_timeout=3000")
            flags = detect(conn, a.session)
            if a.dry_run:
                for f in flags:
                    print(f"{f['score']:.2f}  {f['domain']:28} e.g. {', '.join(f['examples'][:2])}")
                    for r in f["reasons"]:
                        print(f"        - {r}")
                print(f"{len(flags)} flagged ({time.time() - t0:.2f} s)")
            else:
                n = write_flags(conn, flags, prune=a.session is None)  # never prune from a partial run
                if not a.loop or n:
                    print(f"[detector] {len(flags)} flagged, {n} rows written ({time.time() - t0:.2f} s)", flush=True)
            conn.close()
        except sqlite3.Error as e:
            print(f"[detector] db busy/err, retrying: {e}", flush=True)
        if not a.loop:
            break
        time.sleep(a.loop)


if __name__ == "__main__":
    main()
