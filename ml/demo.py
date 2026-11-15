"""Demo mode: remove one real tracker company from every list so the model has to
catch it live (CLAUDE.md §1 "the one moment", §6.1).

    python3 -m ml.demo set InMobi        # demo_mode=on, demo_holdout=InMobi, clears its old flags
    python3 -m ml.demo status            # what's held out + is it caught yet?
    python3 -m ml.demo watch             # live: prints the held-out company's lookups and flags
    python3 -m ml.demo clear             # back to normal

Everything else follows settings.demo_holdout through ml.lists.load_live(conn):
digin_dns.py (Shield stops blocking it), score.py (it's now unlabeled, so it gets
scored), the dashboard receipt (it shows as unknown until flagged), ml.cname rules.

Pick a company the demo app really contacts (check `status` after a test run). A
company the box never sees can't be caught, and we never fake it.
"""
import argparse
import json
import sqlite3
import sys
import time

from ml import lists as lists_mod


def connect(path):
    conn = sqlite3.connect(path, timeout=3)
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


def _set(conn, key, value):
    conn.execute("INSERT INTO settings(key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def holdout_domains(company):
    """Ground truth for the held-out company, from the FULL lists."""
    return lists_mod.load().domains_of(company)


def _belongs(name, doms):
    name = name.lower().strip(".")
    return any(name == d or name.endswith("." + d) for d in doms)


def set_holdout(conn, companies, reset_flags=True):
    full = lists_mod.load()
    known = full.companies()
    for c in companies:
        if not full.domains_of(c):
            raise SystemExit(f"no list domains for {c!r}. Known companies include: "
                             + ", ".join(k for k in known if c.lower()[:3] in k.lower())[:300])
    with conn:
        _set(conn, "demo_mode", "on")
        _set(conn, "demo_holdout", ",".join(companies))
        if reset_flags:
            doms = [d for c in companies for d in holdout_domains(c)]
            for (dom,) in conn.execute("SELECT domain FROM ml_flags").fetchall():
                if _belongs(dom, doms):
                    conn.execute("DELETE FROM ml_flags WHERE domain=?", (dom,))
    return status(conn)


def clear(conn):
    with conn:
        _set(conn, "demo_mode", "off")
        _set(conn, "demo_holdout", "")


def status(conn, since_ts=None):
    companies = lists_mod.demo_holdout(conn)
    out = {"demo_holdout": companies, "companies": []}
    live = lists_mod.load_live(conn)
    for c in companies:
        doms = holdout_domains(c)
        q = "SELECT qname, COUNT(*), MIN(ts) FROM raw_lookups"
        args = ()
        if since_ts:
            q += " WHERE ts >= ?"
            args = (since_ts,)
        seen = {}
        for qname, n, ts in conn.execute(q + " GROUP BY qname", args):
            if qname and _belongs(qname, doms):
                seen[qname.lower().strip(".")] = (n, ts)
        flags = {}
        for dom, score, reasons, st, mv in conn.execute(
                "SELECT domain, score, reasons, status, model_version FROM ml_flags"):
            if dom in seen:
                flags[dom] = {"score": round(score, 3), "status": st, "model": mv,
                              "reasons": json.loads(reasons or "[]")}
        out["companies"].append({
            "company": c,
            "list_domains": len(doms),
            "still_on_a_list": [d for d in seen if live.is_known(d)],   # should be []
            "seen": len(seen),
            "flagged": len(flags),
            "caught": sorted(flags),
            "missed": sorted(set(seen) - set(flags)),
            "flags": flags,
        })
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["set", "clear", "status", "watch"])
    ap.add_argument("company", nargs="*")
    ap.add_argument("--db", default="/home/arduino/digin/digin.db")
    ap.add_argument("--keep-flags", action="store_true",
                    help="with set: don't delete existing flags for the held-out company")
    a = ap.parse_args(argv)
    conn = connect(a.db)
    if a.cmd == "set":
        if not a.company:
            ap.error("set needs a company, e.g. set InMobi")
        print(json.dumps(set_holdout(conn, [" ".join(a.company)], not a.keep_flags), indent=2))
    elif a.cmd == "clear":
        clear(conn)
        print("demo mode off")
    elif a.cmd == "status":
        print(json.dumps(status(conn), indent=2))
    else:
        start = time.time()
        while True:
            s = status(conn, since_ts=start)
            print("\033[2J\033[H", end="")
            for c in s["companies"]:
                print(f"{c['company']}: seen {c['seen']} domains, caught {c['flagged']}")
                for d, f in c["flags"].items():
                    print(f"  FLAGGED {d}  {f['score']}  {'; '.join(f['reasons'])}")
                for d in c["missed"]:
                    print(f"  missed  {d}")
                if c["still_on_a_list"]:
                    print("  LEAK: still on a list:", c["still_on_a_list"])
            time.sleep(1)


if __name__ == "__main__":
    sys.exit(main())
