"""Turn CSV exports of the box's digin.db tables into a digin.db-shaped SQLite file.

    python3 -m ml_eval.import_box_csv --lookups data/box-exports/box_raw_lookups.csv --flows data/box-exports/box_raw_flows.csv \\
        --sessions data/box-exports/box_sessions.csv --out data/sessions/box1.db --keep Wikipedia BASELINE_IDLE

--keep names the sessions to import (default: all). --tagged-only drops lookups and flows that
belong to no kept session (phone traffic between sessions, unknown app). Devices get a prefix
(default "box:") so their flows are never matched to another source's lookups of the same phone.
"""
import argparse
import csv
import sys

from ml_eval.import_dnsmasq import create_db


def _f(v):
    return float(v) if v not in (None, "") else None


def _i(v):
    return int(float(v)) if v not in (None, "") else None


def import_csvs(conn, lookups, flows, sessions, keep=None, tagged_only=False, prefix="box:"):
    keep_l = {k.lower() for k in keep} if keep else None
    ids = {}
    with open(sessions, newline="") as f:
        for r in csv.DictReader(f):
            if keep_l is None or r["app_name"].lower() in keep_l:
                cur = conn.execute("INSERT INTO sessions(device, app_name, start_ts, end_ts) VALUES (?,?,?,?)",
                                   (prefix + (r["device"] or ""), r["app_name"], _f(r["start_ts"]), _f(r["end_ts"])))
                ids[r["session_id"]] = cur.lastrowid
    n = {"sessions": len(ids), "lookups": 0, "flows": 0}
    with open(lookups, newline="") as f:
        for r in csv.DictReader(f):
            sid = ids.get(r["session_id"])
            if tagged_only and sid is None:
                continue
            conn.execute("INSERT INTO raw_lookups(ts, device, session_id, qname, qtype, cname_chain, answer_ips, "
                         "blocked, block_reason) VALUES (?,?,?,?,?,?,?,?,?)",
                         (_f(r["ts"]), prefix + r["device"], sid, r["qname"], r["qtype"], r["cname_chain"] or "[]",
                          r["answer_ips"] or "[]", _i(r["blocked"]) or 0, r["block_reason"] or None))
            n["lookups"] += 1
    with open(flows, newline="") as f:
        for r in csv.DictReader(f):
            sid = ids.get(r["session_id"])
            if tagged_only and sid is None:
                continue
            conn.execute("INSERT INTO raw_flows(ts, device, session_id, dst_ip, dst_port, proto, bytes_up, bytes_down, "
                         "pkts_up, pkts_down, duration_s) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (_f(r["ts"]), prefix + r["device"], sid, r["dst_ip"], _i(r["dst_port"]), r["proto"],
                          _i(r["bytes_up"]), _i(r["bytes_down"]), _i(r["pkts_up"]), _i(r["pkts_down"]),
                          _f(r["duration_s"])))
            n["flows"] += 1
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lookups", required=True)
    ap.add_argument("--flows", required=True)
    ap.add_argument("--sessions", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--keep", nargs="*")
    ap.add_argument("--tagged-only", action="store_true")
    ap.add_argument("--prefix", default="box:")
    a = ap.parse_args(argv)
    conn = create_db(a.out)
    with conn:
        print(import_csvs(conn, a.lookups, a.flows, a.sessions, a.keep, a.tagged_only, a.prefix))
    return 0


if __name__ == "__main__":
    sys.exit(main())
