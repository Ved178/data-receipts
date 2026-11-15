"""Turn PCAPdroid CSV exports into a digin.db-shaped SQLite file, so training and
evaluation run on the same tables as the box (raw_lookups, raw_flows, sessions).

    python3 -m ml_eval.import_pcapdroid data/pcapdroid/*.csv --out data/sessions/pcapdroid.db

Each (file, App) pair becomes one session. Each connection becomes one raw_flows row
and, when it has a hostname in Info, one raw_lookups row (qname=Info, answer_ips=[DstIp]).
PCAPdroid has no CNAME chains, so cname_chain stays NULL for imported rows.
"""
import argparse
import csv
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = os.path.join(HERE, "..", "db", "schema.sql")


def create_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(open(SCHEMA).read())
    return conn


def _ts(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        # PCAPdroid CSVs use ISO strings, e.g. 2026-11-14T14:11:14.889-06:00
        try:
            from datetime import datetime
            return datetime.fromisoformat(str(v).strip()).timestamp()
        except ValueError:
            return None
    return x / 1000.0 if x > 1e11 else x


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def import_csv(conn, path, next_session, background="drop"):
    device = os.path.splitext(os.path.basename(path))[0]
    sessions = {}
    n_l = n_f = 0
    with open(path, errors="replace", newline="") as f:
        rows = list(csv.DictReader(f))
    if background == "drop" and rows:
        # One capture = one app. Rows from other apps (Google Play services, Meta Services...)
        # are phone background traffic; keeping them would fake cross-app and co-occurrence signals.
        key = next((k for k in rows[0] if k and k.strip().lower() == "app"), None)
        if key:
            from collections import Counter
            main = Counter((r[key] or "?").strip() for r in rows).most_common(1)[0][0]
            rows = [r for r in rows if (r[key] or "?").strip() == main]
    # map headers case-insensitively
    def g(r, *keys):
        for k in keys:
            for rk in r:
                if rk and rk.strip().lower() == k:
                    return r[rk]
        return None
    for r in rows:
        app = (g(r, "app") or "?").strip()
        ts = _ts(g(r, "firstseen", "first_seen")) or 0.0
        sessions.setdefault(app, []).append(ts)
    sid = {}
    for app, tss in sessions.items():
        sid[app] = next_session
        conn.execute("INSERT INTO sessions(session_id, device, app_name, start_ts, end_ts) "
                     "VALUES (?,?,?,?,?)", (next_session, device, app, min(tss), max(tss)))
        next_session += 1
    for r in rows:
        app = (g(r, "app") or "?").strip()
        ts = _ts(g(r, "firstseen", "first_seen")) or 0.0
        last = _ts(g(r, "lastseen", "last_seen"))
        ip = (g(r, "dstip", "dst_ip") or "").strip()
        host = (g(r, "info") or "").strip().lower().strip(".")
        if host and "." in host and not host.replace(".", "").isdigit():
            conn.execute("INSERT INTO raw_lookups(ts, device, session_id, qname, qtype, answer_ips) "
                         "VALUES (?,?,?,?,?,?)",
                         (ts, device, sid[app], host, "A", json.dumps([ip] if ip else [])))
            n_l += 1
        if ip:
            conn.execute(
                "INSERT INTO raw_flows(ts, device, session_id, dst_ip, dst_port, proto, bytes_up, "
                "bytes_down, pkts_up, pkts_down, duration_s) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (ts, device, sid[app], ip, _int(g(r, "dstport", "dst_port")),
                 g(r, "proto") or g(r, "ipproto"), _int(g(r, "bytessent")),
                 _int(g(r, "bytesrcvd")), _int(g(r, "pktssent")), _int(g(r, "pktsrcvd")),
                 (last - ts) if last and ts else None))
            n_f += 1
    return next_session, n_l, n_f, len(sid)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--background", choices=("drop", "keep"), default="drop",
                    help="drop (default): keep only each file's main app; keep: import all apps")
    a = ap.parse_args(argv)
    conn = create_db(a.out)
    nxt = (conn.execute("SELECT MAX(session_id) FROM sessions").fetchone()[0] or 0) + 1
    with conn:
        for p in a.csv:
            nxt, nl, nf, ns = import_csv(conn, p, nxt, a.background)
            print(f"{p}: {ns} sessions, {nl} lookups, {nf} flows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
