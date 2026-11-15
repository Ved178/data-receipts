"""Merge digin.db-shaped SQLite files (importer outputs) into one, so sources with different
strengths (dnsmasq logs: CNAME chains; PCAPdroid: byte counts) can train one model.

    python3 -m ml_eval.merge_dbs --out data/sessions/all.db data/sessions/combined2.db pcap.db

session_id values are renumbered; each source keeps its own device names.
"""
import argparse
import os
import sqlite3
import sys

from ml_eval.import_dnsmasq import create_db


def merge(out_path, paths):
    conn = create_db(out_path)
    for p in paths:
        with conn:
                conn.execute("ATTACH DATABASE ? AS src", (os.path.abspath(p),))
                off = conn.execute("SELECT COALESCE(MAX(session_id),0) FROM sessions").fetchone()[0]
                conn.execute("INSERT INTO sessions(session_id, device, app_name, start_ts, end_ts) "
                             "SELECT session_id+?, device, app_name, start_ts, end_ts FROM src.sessions", (off,))
                conn.execute("INSERT INTO raw_lookups(ts, device, session_id, qname, qtype, cname_chain, "
                             "answer_ips, blocked, block_reason) SELECT ts, device, "
                             "CASE WHEN session_id IS NULL THEN NULL ELSE session_id+? END, qname, qtype, "
                             "cname_chain, answer_ips, blocked, block_reason FROM src.raw_lookups", (off,))
                conn.execute("INSERT INTO raw_flows(ts, device, session_id, dst_ip, dst_port, proto, bytes_up, "
                             "bytes_down, pkts_up, pkts_down, duration_s) SELECT ts, device, "
                             "CASE WHEN session_id IS NULL THEN NULL ELSE session_id+? END, dst_ip, dst_port, "
                             "proto, bytes_up, bytes_down, pkts_up, pkts_down, duration_s FROM src.raw_flows", (off,))
        conn.execute("DETACH DATABASE src")
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("sessions", "raw_lookups", "raw_flows")}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("db", nargs="+")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if os.path.exists(a.out):
        raise SystemExit(f"{a.out} exists; remove it first")
    print(merge(a.out, a.db))
    return 0


if __name__ == "__main__":
    sys.exit(main())
