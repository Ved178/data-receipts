"""Turn a box dnsmasq log (tail of /tmp/dns.log) into a digin.db-shaped SQLite file, so
training and evaluation run on the same tables as the box (raw_lookups, sessions).

    python3 -m ml_eval.import_dnsmasq data/digin_run1_log.txt --out data/sessions/digin_run1.db \\
        --pid 1688 --year 2026 \\
        --session "Instagram=23:36-23:41" --session "Chrome=23:37-23:41" \\
        --session "Block Blast=23:41-23:45"

One raw_lookups row per A/AAAA query. The CNAME chain is rebuilt from the reply lines:
`reply X is <CNAME>` means the next reply line is the next hop, and `config X is 0.0.0.0`
marks a lookup answered by our blocklist (blocked=1; no IPs or chain exist for it).
Times in the log are UTC; --session windows are HH:MM-HH:MM in UTC on the log's date.
Lookups that fall in overlapping session windows get session_id NULL (ambiguous app)
unless --overlap latest is given. Only domains, timings and answers are stored.
"""
import argparse
import calendar
import json
import os
import re
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = os.path.join(HERE, "..", "db", "schema.sql")

LINE = re.compile(r"^(\w{3}\s+\d+\s+\d\d:\d\d:\d\d)\s+dnsmasq\[(\d+)\]:\s+(.*)$")
QUERY = re.compile(r"^query\[(\w+)\]\s+(\S+)\s+from\s+(\S+)$")
REPLY = re.compile(r"^(reply|cached|config)\s+(\S+)\s+is\s+(\S+)$")
MONTHS = {m: i for i, m in enumerate(calendar.month_abbr) if m}
NON_ANSWERS = ("NODATA", "NXDOMAIN", "SERVFAIL", "REFUSED", "<")


def _epoch(stamp, year):
    mon, day, hms = stamp.split()
    h, m, s = (int(x) for x in hms.split(":"))
    return float(calendar.timegm((year, MONTHS[mon], int(day), h, m, s)))


def _norm(name):
    return name.lower().strip(".")


def parse_log(lines, year, pid=None):
    """Yield one dict per A/AAAA query: ts, device, qname, qtype, cname_chain, answer_ips,
    blocked, block_reason. Only lines from dnsmasq process `pid` (default: the last one)."""
    parsed = []
    for raw in lines:
        m = LINE.match(raw.rstrip("\n"))
        if m:
            parsed.append((m.group(1), int(m.group(2)), m.group(3)))
    if not parsed:
        return []
    if pid is None:
        pid = parsed[-1][1]
    lookups, last_by_name = [], {}
    active, pending = None, False  # pending: previous reply was a CNAME hop
    for stamp, p, msg in parsed:
        if p != pid:
            continue
        q = QUERY.match(msg)
        if q:
            qtype, name, dev = q.group(1), _norm(q.group(2)), q.group(3)
            if qtype in ("A", "AAAA"):
                rec = {"ts": _epoch(stamp, year), "device": dev, "qname": name, "qtype": qtype,
                       "cname_chain": [], "answer_ips": [], "blocked": 0, "block_reason": None}
                lookups.append(rec)
                last_by_name[name] = rec
            active, pending = None, False
            continue
        r = REPLY.match(msg)
        if not r:
            continue
        kind, name, val = r.group(1), _norm(r.group(2)), r.group(3)
        if pending and active is not None:
            hop = name  # this line names the next hop of the chain
            if hop != active["qname"] and hop not in active["cname_chain"]:
                active["cname_chain"].append(hop)
        elif name in last_by_name:
            active = last_by_name[name]
        else:
            active = None
            pending = False
            continue
        if kind == "config":
            if val == "0.0.0.0":
                active["blocked"] = 1
                active["block_reason"] = "list" if name == active["qname"] else "cname"
            pending = False
        elif val == "<CNAME>":
            pending = True
        else:
            pending = False
            if not val.startswith(NON_ANSWERS) and val not in active["answer_ips"]:
                active["answer_ips"].append(val)
    return lookups


def parse_sessions(specs, year, date_of):
    """'App=HH:MM-HH:MM' (UTC, on date_of's day) -> [(app, start_ts, end_ts)]."""
    day0 = date_of - (date_of % 86400)
    out = []
    for s in specs:
        app, _, win = s.rpartition("=")
        a, _, b = win.partition("-")
        t = []
        for x in (a, b):
            h, m = (int(v) for v in x.split(":"))
            t.append(day0 + h * 3600 + m * 60)
        if not app or t[1] <= t[0]:
            raise ValueError(f"bad --session {s!r}; want 'App=HH:MM-HH:MM'")
        out.append((app, t[0], t[1]))
    return out


def assign(ts, sessions, overlap):
    hits = [i for i, (_, a, b) in enumerate(sessions) if a <= ts < b]
    if not hits:
        return None
    if len(hits) == 1 or overlap == "latest":
        return max(hits, key=lambda i: sessions[i][1])
    return None


def create_db(path):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(open(SCHEMA).read())
    return conn


def import_log(conn, lines, year, pid, session_specs, overlap="null"):
    lookups = parse_log(lines, year, pid)
    if not lookups:
        return {"lookups": 0}
    sess = parse_sessions(session_specs, year, lookups[0]["ts"])
    nxt = (conn.execute("SELECT MAX(session_id) FROM sessions").fetchone()[0] or 0) + 1
    ids = {}
    device = lookups[0]["device"]
    for i, (app, a, b) in enumerate(sess):
        conn.execute("INSERT INTO sessions(session_id, device, app_name, start_ts, end_ts) "
                     "VALUES (?,?,?,?,?)", (nxt, device, app, a, b))
        ids[i] = nxt
        nxt += 1
    stats = {"lookups": 0, "blocked": 0, "with_chain": 0, "no_session": 0, "ambiguous": 0}
    for r in lookups:
        hits = [i for i, (_, a, b) in enumerate(sess) if a <= r["ts"] < b]
        k = assign(r["ts"], sess, overlap)
        stats["no_session"] += not hits
        stats["ambiguous"] += len(hits) > 1 and overlap != "latest"
        conn.execute(
            "INSERT INTO raw_lookups(ts, device, session_id, qname, qtype, cname_chain, "
            "answer_ips, blocked, block_reason) VALUES (?,?,?,?,?,?,?,?,?)",
            (r["ts"], r["device"], ids.get(k), r["qname"], r["qtype"],
             json.dumps(r["cname_chain"]), json.dumps(r["answer_ips"]),
             r["blocked"], r["block_reason"]))
        stats["lookups"] += 1
        stats["blocked"] += r["blocked"]
        stats["with_chain"] += bool(r["cname_chain"])
    stats["sessions"] = len(sess)
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log")
    ap.add_argument("--out", required=True)
    ap.add_argument("--pid", type=int, help="dnsmasq pid to keep (default: the last in the file)")
    ap.add_argument("--year", type=int, default=time.gmtime().tm_year)
    ap.add_argument("--session", action="append", default=[], metavar="APP=HH:MM-HH:MM")
    ap.add_argument("--overlap", choices=("null", "latest"), default="null")
    a = ap.parse_args(argv)
    with open(a.log, errors="replace") as f:
        lines = f.readlines()
    conn = create_db(a.out)
    with conn:
        st = import_log(conn, lines, a.year, a.pid, a.session, a.overlap)
    print(json.dumps(st))
    return 0


if __name__ == "__main__":
    sys.exit(main())
