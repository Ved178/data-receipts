#!/usr/bin/env python3
"""
dashboard/app.py - Data Receipt dashboard (owner: Aarav). Stdlib only, works offline.

  sudo python3 dashboard/app.py                 # http://10.42.0.1 (port 80)
  python3 dashboard/app.py --port 8080 --db /tmp/seed.db   # laptop testing

Reads digin.db (frozen schema, db/schema.sql). Endpoints:
  GET  /                 the page
  GET  /api/state?session=<id>   receipt, totals, recent lookups, sessions, shield
  GET  /api/web          Tracker Web: which companies appear in which apps
  GET  /api/flags        ml_flags (Suspected trackers)
  POST /api/shield       {"state": "on"|"off"}
  POST /api/app          {"app": "Block Blast"}   remembers the app for the Start button
  POST /api/session/start {"app": "..."}  /api/session/end
  POST /api/flag         {"domain": "...", "status": "auto_blocked"|"dismissed"}
  GET  /health           DNS alive, last row age, DB size, CPU temp
"""
import argparse
import json
import os
import socket
import sqlite3
import sys
import time
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import company  # noqa: E402

DB = os.environ.get("DIGIN_DB", "/home/arduino/digin/digin.db")
SCHEMA = os.path.join(REPO, "db", "schema.sql")

# Plain-language "what they typically get" lines for the receipt (from published ad-tech research).
WHAT_THEY_GET = {
    "Advertising": "Usually receive your phone model, rough location from your IP, and an ad ID if allowed.",
    "Analytics": "Usually receive how you use the app: sessions, screens, play time, purchases.",
    "Social": "Can link this app's activity to a social profile.",
    "FingerprintingInvasive": "Collect device details to recognize your phone without an ad ID.",
    "FingerprintingGeneral": "Collect device details that help recognize your phone.",
    "Suspected": "Not on any list. Flagged by our on-device model from how it behaves.",
}


def db():
    conn = sqlite3.connect(DB, timeout=3)
    conn.execute("PRAGMA busy_timeout=3000")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs(os.path.dirname(DB) or ".", exist_ok=True)
    conn = sqlite3.connect(DB, timeout=5)
    with open(SCHEMA) as f:
        conn.executescript(f.read())
    conn.close()


def setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    conn = db()
    conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (key, value))
    conn.commit()


def flags_map(conn):
    out = {}
    for r in conn.execute("SELECT * FROM ml_flags"):
        try:
            reasons = json.loads(r["reasons"])
        except (TypeError, ValueError):
            reasons = [r["reasons"]]
        out[r["domain"]] = {"domain": r["domain"], "score": r["score"], "reasons": reasons,
                            "status": r["status"], "model_version": r["model_version"],
                            "flagged_ts": r["flagged_ts"]}
    return out


def classify(qname, flags, chain=None):
    """-> (company, category) | (domain, 'Suspected') | None. Also uncloaks CNAME chains.
    The category follows what the domain does: an ad server is Advertising even when its owner
    is filed under Content (e.g. Amazon)."""
    if company.is_system(qname):
        return None
    chain = chain_list(chain)
    names = [qname] + chain
    hit = company.company_for(qname)
    if hit:
        return (company.display(hit[0]), company.by_function(names, hit[1]))
    # CNAME uncloaking: only a trail that ends at a TRACKER changes who we credit. A trail to a
    # cloud host (thinkingdata.cn -> Alibaba load balancer) must not relabel the domain as the host.
    for target in chain:
        hit = company.company_for(target)
        if hit and hit[1] in TRACKING:
            return (company.display(hit[0]), hit[1])
    # The detector flags registrable names (bidease.com); match any parent of the lookup.
    p = qname.lower().strip(".").split(".")
    f = next((flags[".".join(p[i:])] for i in range(len(p) - 1) if ".".join(p[i:]) in flags), None)
    if f and f["status"] in ("suspected", "auto_blocked"):
        return (f["domain"], "Suspected")
    return None


TRACKING = ("Advertising", "Analytics", "Social", "FingerprintingInvasive", "FingerprintingGeneral",
            "Suspected", "Cryptomining")


def is_tracking(hit):
    return bool(hit) and hit[1] in TRACKING


def chain_list(chain):
    if not chain:
        return []
    try:
        return json.loads(chain) if isinstance(chain, str) else list(chain)
    except ValueError:
        return []


def state(session_param):
    conn = db()
    sessions = [dict(r) for r in conn.execute(
        "SELECT session_id, device, app_name, start_ts, end_ts FROM sessions ORDER BY start_ts DESC LIMIT 50")]
    current = next((s for s in sessions if s["end_ts"] is None), None)
    if session_param and session_param not in ("current", "all"):
        sel = next((s for s in sessions if str(s["session_id"]) == session_param), None)
    elif session_param == "all":
        sel = None
    else:
        sel = current or (sessions[0] if sessions else None)

    if sel:
        rows = conn.execute("SELECT * FROM raw_lookups WHERE session_id=? ORDER BY id", (sel["session_id"],)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM (SELECT * FROM raw_lookups ORDER BY id DESC LIMIT 5000)"
                            " ORDER BY id").fetchall()

    flags = flags_map(conn)
    app = sel["app_name"] if sel else None
    entries = {}          # (company, category) -> row: a company can sit in two categories (Amazon ads + shop)
    unknown = defaultdict(int)
    background = defaultdict(lambda: {"hits": 0, "domains": set()})
    blocked_lookups = 0
    for r in rows:
        q = r["qname"]
        if r["blocked"]:
            blocked_lookups += 1
        bg = company.background([q] + chain_list(r["cname_chain"]), app)
        if bg:
            background[bg]["hits"] += 1
            background[bg]["domains"].add(q)
            continue
        hit = classify(q, flags, r["cname_chain"])
        if not hit:
            if not company.is_system(q):
                unknown[q] += 1
            continue
        co, cat = hit
        c = entries.setdefault((co, cat), {"company": co, "category": cat, "hits": 0, "blocked": 0, "domains": set()})
        c["hits"] += 1
        c["blocked"] += 1 if r["blocked"] else 0
        c["domains"].add(q)

    groups = defaultdict(list)
    for c in entries.values():
        c["domains"] = sorted(c["domains"])
        groups[c["category"]].append(c)
    receipt = []
    for cat, items in sorted(groups.items(), key=lambda kv: -sum(i["hits"] for i in kv[1])):
        items.sort(key=lambda i: -i["hits"])
        receipt.append({"category": cat, "note": WHAT_THEY_GET.get(cat, ""), "companies": items})
    companies = {c["company"] for c in entries.values()}
    companies_blocked = {c["company"] for c in entries.values() if c["blocked"]}

    duration = None
    if sel:
        duration = (sel["end_ts"] or time.time()) - sel["start_ts"]
    recent = []
    for r in list(rows)[-15:][::-1]:
        bg = company.background([r["qname"]] + chain_list(r["cname_chain"]), app)
        recent.append({"ts": r["ts"], "qname": r["qname"], "blocked": r["block_reason"],
                       "tracker": (not bg) and is_tracking(classify(r["qname"], flags, r["cname_chain"]))})
    return {
        "shield": setting(conn, "shield", "off"),
        "selected_app": setting(conn, "selected_app", ""),
        "demo_mode": setting(conn, "demo_mode", "off"),
        "sessions": sessions,
        "session": sel,
        "duration_s": duration,
        "totals": {"lookups": len(rows), "domains": len({r["qname"] for r in rows}),
                   "companies": len(companies), "blocked_lookups": blocked_lookups,
                   "companies_blocked": len(companies_blocked),
                   "background_lookups": sum(b["hits"] for b in background.values())},
        "receipt": receipt,
        "background": [{"label": k, "hits": v["hits"], "domains": sorted(v["domains"])}
                       for k, v in sorted(background.items(), key=lambda kv: -kv[1]["hits"])],
        "unknown": sorted(unknown.items(), key=lambda kv: -kv[1])[:12],
        "recent": recent,
        "server_ts": time.time(),
    }


def tracker_web():
    conn = db()
    flags = flags_map(conn)
    apps = {r["session_id"]: r["app_name"] for r in conn.execute("SELECT session_id, app_name FROM sessions")}
    edges = defaultdict(int)
    for r in conn.execute("SELECT session_id, qname, cname_chain FROM raw_lookups WHERE session_id IS NOT NULL"):
        if r["session_id"] not in apps or company.background(
                [r["qname"]] + chain_list(r["cname_chain"]), apps[r["session_id"]]):
            continue
        hit = classify(r["qname"], flags, r["cname_chain"])
        if hit:
            edges[(apps[r["session_id"]], hit[0], hit[1])] += 1
    by_company = defaultdict(set)
    for (app, co, _), _n in edges.items():
        by_company[co].add(app)
    return {
        "edges": [{"app": a, "company": c, "category": cat, "hits": n} for (a, c, cat), n in edges.items()],
        "everywhere": sorted(([c, len(a)] for c, a in by_company.items()), key=lambda x: -x[1])[:10],
    }


def health():
    conn = db()
    last = conn.execute("SELECT MAX(ts) FROM raw_lookups").fetchone()[0]
    try:
        temp = int(open("/sys/class/thermal/thermal_zone0/temp").read().strip()) / 1000
    except (OSError, ValueError):
        temp = None
    dns_ok = False
    try:
        from dnslib import DNSRecord
        DNSRecord.parse(DNSRecord.question("example.com").send(os.environ.get("DIGIN_DNS_IP", "10.42.0.1"), 53, timeout=1))
        dns_ok = True
    except Exception:  # noqa: BLE001
        pass
    return {"dns_alive": dns_ok, "last_row_age_s": (time.time() - last) if last else None,
            "db_bytes": os.path.getsize(DB) if os.path.exists(DB) else 0, "cpu_temp_c": temp}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        body = json.dumps(obj, default=list).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    def do_GET(self):
        u = urlparse(self.path)
        try:
            holdout = setting(db(), "demo_holdout", "") or ""
            company.set_holdout(holdout.split(","))
            if u.path in ("/", "/index.html"):
                with open(os.path.join(HERE, "static", "index.html"), "rb") as f:
                    page = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)
            elif u.path == "/api/state":
                self.send_json(state(parse_qs(u.query).get("session", ["current"])[0]))
            elif u.path == "/api/web":
                self.send_json(tracker_web())
            elif u.path == "/api/flags":
                flags = list(flags_map(db()).values())
                flags.sort(key=lambda f: -f["score"])
                self.send_json(flags)
            elif u.path == "/health":
                self.send_json(health())
            else:
                self.send_json({"error": "not found"}, 404)
        except sqlite3.Error as e:
            self.send_json({"error": f"db: {e}"}, 503)

    def do_POST(self):
        b = self.body()
        try:
            if self.path == "/api/shield":
                set_setting("shield", "on" if b.get("state") == "on" else "off")
            elif self.path == "/api/app":
                set_setting("selected_app", (b.get("app") or "").strip()[:60])
            elif self.path == "/api/session/start":
                app = (b.get("app") or "").strip()[:60] or "unknown"
                conn = db()
                now = time.time()
                conn.execute("UPDATE sessions SET end_ts=? WHERE end_ts IS NULL", (now,))
                conn.execute("INSERT INTO sessions(device, app_name, start_ts) VALUES ('', ?, ?)", (app, now))
                conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('selected_app', ?)", (app,))
                conn.commit()
            elif self.path == "/api/session/end":
                conn = db()
                conn.execute("UPDATE sessions SET end_ts=? WHERE end_ts IS NULL", (time.time(),))
                conn.commit()
            elif self.path == "/api/flag":
                status = b.get("status")
                if status not in ("auto_blocked", "dismissed", "suspected"):
                    return self.send_json({"error": "bad status"}, 400)
                conn = db()
                conn.execute("UPDATE ml_flags SET status=?, updated_ts=? WHERE domain=?",
                             (status, time.time(), b.get("domain")))
                conn.commit()
            else:
                return self.send_json({"error": "not found"}, 404)
            self.send_json({"ok": True})
        except sqlite3.Error as e:
            self.send_json({"error": f"db: {e}"}, 503)


def main():
    global DB
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=DB)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=80)
    a = p.parse_args()
    DB = a.db
    init_db()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"Dashboard on http://{socket.gethostname()}:{a.port}  (db {DB})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
