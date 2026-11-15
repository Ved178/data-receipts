"""CNAME uncloaking + the rule flags (cut line, docs/team-brief.md §7 Samarth #6).

A "cloaked" tracker hides behind the site's own name: metrics.someapp.com is a
CNAME to someapp.sc.omtrdc.net (Adobe). Lists keyed on names miss it; the chain gives
it away.

Library:
    uncloak(qname, cname_chain, L, flagged=())  -> Hit or None
    ad_network_ips(answer_ips, L)               -> [(ip, asn, company, category)]
    rule_flags(conn, L)                         -> {domain: (score, [reasons])}

CLI (runs on the box next to score.py; safe to run alongside it):
    python3 -m ml.cname --db /home/arduino/digin/digin.db            # one pass
    python3 -m ml.cname --db ... --loop 3                            # every 3 s
    python3 -m ml.cname check metrics.example.com a.b.omtrdc.net     # test a chain

Rule rows use model_version 'rules-v1' and status 'suspected'. They never overwrite a
row written by the model (model_version not starting with 'rules'), nor a row a human
set to dismissed/allowlisted/approved. Domains already on a list are skipped: Shield handles
those; ml_flags is for what the lists miss.
"""
import argparse
import json
import sqlite3
import sys
import time
from collections import defaultdict, namedtuple

from ml import lists as lists_mod

RULES_VERSION = "rules-v1"
Hit = namedtuple("Hit", "qname target company category")

# Rule scores. Kept below 0.97 so rules alone never auto-block.
SCORE_CNAME = 0.90
SCORE_ASN = 0.75
SCORE_BOTH = 0.95


def _loads(s):
    if not s:
        return []
    try:
        v = json.loads(s)
        return v if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


def uncloak(qname, cname_chain, L, flagged=()):
    """Hit when the chain from qname ends at (or passes through) a known or
    model-flagged tracker and qname itself is not on a list."""
    q = (qname or "").lower().strip(".")
    if L.is_known(q):
        return None
    chain = [q] + [c.lower().strip(".") for c in (cname_chain or []) if c and c.lower().strip(".") != q]
    hit = L.cname_tracker(chain, flagged)
    if not hit:
        return None
    target, co, cat = hit
    return Hit(q, target, co, cat)


def ad_network_ips(answer_ips, L):
    out = []
    for ip in answer_ips or []:
        o = L.ip_owner(ip)
        if o:
            out.append((ip,) + o)
    return out


def rule_flags(conn, L, since_ts=0.0, flagged=()):
    """Scan raw_lookups -> {domain: (score, reasons, first_ts)} for names not on any list."""
    by_dom = defaultdict(lambda: {"cname": None, "asn": set(), "ts": None})
    rows = conn.execute(
        "SELECT qname, cname_chain, answer_ips, ts FROM raw_lookups WHERE ts >= ?",
        (since_ts,))
    for qname, chain, ips, ts in rows:
        q = (qname or "").lower().strip(".")
        if not q or L.is_known(q) or L.is_allowlisted(q):
            continue
        d = by_dom[q]
        if d["ts"] is None or ts < d["ts"]:
            d["ts"] = ts
        if d["cname"] is None:
            h = uncloak(q, _loads(chain), L, flagged)
            if h:
                d["cname"] = h
        for _ip, _asn, co, _cat in ad_network_ips(_loads(ips), L):
            d["asn"].add(co)
    out = {}
    for dom, d in by_dom.items():
        reasons = []
        h = d["cname"]
        if h:
            who = f"{h.company} ({h.category.lower()})" if h.company else "a suspected tracker"
            reasons.append(f"Hides behind a CNAME to {h.target}, run by {who}")
        if d["asn"]:
            reasons.append("Its servers sit on an ad network: " + ", ".join(sorted(d["asn"])))
        if not reasons:
            continue
        score = SCORE_BOTH if h and d["asn"] else (SCORE_CNAME if h else SCORE_ASN)
        out[dom] = (score, reasons, d["ts"])
    return out


def write_flags(conn, flags, now=None):
    now = now or time.time()
    n = 0
    with conn:
        for dom, (score, reasons, first_ts) in flags.items():
            row = conn.execute("SELECT status, model_version FROM ml_flags WHERE domain=?",
                               (dom,)).fetchone()
            if row and (row[0] in ("dismissed", "allowlisted", "approved")
                        or not row[1].startswith("rules")):
                continue
            conn.execute(
                "INSERT INTO ml_flags(domain, score, reasons, status, model_version, flagged_ts, updated_ts) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(domain) DO UPDATE SET "
                "score=excluded.score, reasons=excluded.reasons, updated_ts=excluded.updated_ts",
                (dom, score, json.dumps(reasons), "suspected", RULES_VERSION,
                 first_ts or now, now))
            n += 1
    return n


def clear_excluded_rule_flags(conn, L):
    """Rule rows must not survive an exclusion change that removed their evidence."""
    with conn:
        conn.execute("DELETE FROM ml_flags WHERE model_version LIKE 'rules%' AND status='suspected'")


def connect(path):
    conn = sqlite3.connect(path, timeout=3)
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


def run_once(conn):
    L = lists_mod.load_live(conn)
    flagged = [r[0] for r in conn.execute(
        "SELECT domain FROM ml_flags WHERE status IN ('suspected','auto_blocked') "
        "AND model_version NOT LIKE 'rules%'")]
    return write_flags(conn, rule_flags(conn, L, flagged=flagged))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("check", nargs="*", help="check QNAME CNAME... (no DB needed)")
    ap.add_argument("--db", default="/home/arduino/digin/digin.db")
    ap.add_argument("--loop", type=float, default=0, help="repeat every N seconds")
    a = ap.parse_args(argv)
    if a.check:
        names = a.check[1:] if a.check[0] == "check" else a.check
        print(uncloak(names[0], names[1:], lists_mod.load()))
        return 0
    conn = connect(a.db)
    last_holdout = None
    while True:
        holdout = lists_mod.demo_holdout(conn)
        if holdout != last_holdout and last_holdout is not None:
            clear_excluded_rule_flags(conn, None)
        last_holdout = holdout
        t = time.time()
        n = run_once(conn)
        print(f"rules: wrote {n} flags in {time.time() - t:.2f}s (holdout={holdout})", flush=True)
        if not a.loop:
            return 0
        time.sleep(a.loop)


if __name__ == "__main__":
    sys.exit(main())
