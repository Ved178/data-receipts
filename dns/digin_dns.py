#!/usr/bin/env python3
"""
dns/digin_dns.py - DigIn DNS logger + Shield (owner: Aarav). Runs on the Uno Q, Linux side.

Follows CLAUDE.md:
  section 5   writes raw_lookups / sessions / settings exactly as frozen in db/schema.sql
  section 6.3 never takes the network down:
                - per-query upstream timeout (2 s) with a second upstream
                - threaded UDP + TCP servers (dnslib uses ThreadingMixIn)
                - DB writes go through a queue + writer thread, never on the answer path
                - any internal error falls back to a plain forwarded answer
                - systemd unit restarts it (deploy/digin-dns.service)

Shield (settings.shield = 'on') answers 0.0.0.0 / :: when:
  list   the name or a parent domain is a known tracker
  cname  any name in its CNAME chain is a known tracker (CNAME cloaking)
  ml     ml_flags.status = 'auto_blocked'
...unless the name is on the allowlist or belongs to the current session's own company.

Known-tracker source: ml/lists.py (Samarth) when importable, else dns/blocklist.txt.
Private Relay / Firefox canary names always answer NXDOMAIN.

Usage
  sudo python3 dns/digin_dns.py serve                      # Plan A: listens on 10.42.0.1:53
  sudo python3 dns/digin_dns.py serve --listen BOARD_IP    # Plan B
  python3 dns/digin_dns.py session start "Block Blast"     # Start button calls this
  python3 dns/digin_dns.py session end
  python3 dns/digin_dns.py shield on|off
  python3 dns/digin_dns.py tail
"""
import argparse
import json
import os
import queue
import sqlite3
import sys
import threading
import time

from dnslib import AAAA, QTYPE, RCODE, RR, A, DNSRecord
from dnslib.server import BaseResolver, DNSServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

DEFAULT_DB = os.environ.get("DIGIN_DB", "/home/arduino/digin/digin.db")
SCHEMA_PATH = os.path.join(REPO, "db", "schema.sql")
BLOCKLIST_PATH = os.path.join(HERE, "blocklist.txt")
ALLOWLIST_PATH = os.path.join(HERE, "allowlist.txt")

ALWAYS_NX = {"mask.icloud.com", "mask-h2.icloud.com", "use-application-dns.net"}
LOGGED_QTYPES = {"A", "AAAA", "HTTPS", "CNAME"}

# App name (as typed at session start) -> owning company, so Shield never blocks an
# app's own company (CLAUDE.md section 7, Aarav task 2). Extend as needed.
APP_OWNER = {
    "instagram": "Meta", "facebook": "Meta", "whatsapp": "Meta", "messenger": "Meta",
    "threads": "Meta", "youtube": "Google", "gmail": "Google", "google": "Google",
    "tiktok": "ByteDance", "capcut": "ByteDance", "snapchat": "Snap",
}

# ---------------------------------------------------------------- tracker lookup adapter
try:  # Samarth's shared helper, once it exists
    from ml import lists as _lists
except Exception:  # noqa: BLE001 - any import problem means "not ready yet"
    _lists = None


def norm(name):
    return str(name).rstrip(".").lower()


def parents(name):
    parts = name.split(".")
    return [".".join(parts[i:]) for i in range(len(parts) - 1)]


def read_domain_file(path):
    out = set()
    try:
        with open(path) as f:
            for line in f:
                d = line.split("#")[0].strip()
                if d:
                    out.add(norm(d))
    except FileNotFoundError:
        pass
    return out


class TrackerLookup:
    """Known-tracker checks. Uses ml/lists.py (Samarth) when it imports, with the live demo
    holdout applied (CLAUDE.md 6.1); dns/blocklist.txt is the fallback and an extra list.
    lists.py API: load_live(conn) -> Lists with .owner() .is_tracker() .is_allowlisted()
    .same_company() .cname_tracker() .excluded_domains."""

    def __init__(self, blocklist_path):
        self.blocklist_path = blocklist_path
        self.blockset = read_domain_file(blocklist_path)
        self.loaded_ts = time.time()
        self.L = None  # current ml/lists.py Lists object (None = not available)

    def reload_if_changed(self):
        try:
            if os.path.getmtime(self.blocklist_path) > self.loaded_ts:
                self.blockset = read_domain_file(self.blocklist_path)
                self.loaded_ts = time.time()
        except FileNotFoundError:
            pass

    def update(self, conn):
        """Pick up settings.demo_holdout. lists.py caches per holdout, so this is cheap."""
        if _lists is None:
            return
        try:
            self.L = _lists.load_live(conn)
        except Exception as e:  # noqa: BLE001 - keep the previous lists
            print(f"[lists] keeping old lists: {e}", flush=True)

    def _held_out(self, name):
        return self.L is not None and any(p in self.L.excluded_domains for p in parents(name))

    def company(self, name):
        if self.L is not None:
            try:
                return self.L.owner(name)
            except Exception:  # noqa: BLE001
                pass
        return None

    def is_allowlisted(self, name):
        try:
            return self.L is not None and self.L.is_allowlisted(name)
        except Exception:  # noqa: BLE001
            return False

    def same_company(self, name, app):
        try:
            return self.L is not None and bool(app) and self.L.same_company(name, app)
        except Exception:  # noqa: BLE001
            return False

    def is_tracker(self, name):
        if self.L is not None:
            try:
                if self.L.is_tracker(name):
                    return True
            except Exception:  # noqa: BLE001
                pass
        return self.on_blocklist(name)

    def on_blocklist(self, name):
        """dns/blocklist.txt, minus anything lists.py says belongs to a held-out company."""
        return any(p in self.blockset for p in parents(name)) and not self._held_out(name)

    def cname_hit(self, qname, chain, flagged=()):
        if self.L is not None:
            try:
                if self.L.cname_tracker([qname] + list(chain), extra_flagged=flagged):
                    return True
            except Exception:  # noqa: BLE001
                pass
        return any(self.is_tracker(c) for c in chain)


# ---------------------------------------------------------------- database
def connect(db_path, init=False):
    """Open the shared DB. init=True also applies db/schema.sql (retrying if another writer holds a lock)."""
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=3, check_same_thread=False)
    conn.execute("PRAGMA busy_timeout=3000")
    if init:
        with open(SCHEMA_PATH) as f:
            schema = f.read()
        for attempt in range(10):
            try:
                conn.executescript(schema)
                break
            except sqlite3.OperationalError as e:
                if attempt == 9:
                    raise
                print(f"[schema] retry {attempt + 1}: {e}", flush=True)
                time.sleep(0.5)
    return conn


class Writer(threading.Thread):
    """Single DB writer fed by a queue, so a locked DB never delays a DNS answer."""

    def __init__(self, db_path):
        super().__init__(daemon=True)
        self.q = queue.Queue(maxsize=10000)
        self.db_path = db_path

    def put(self, row):
        try:
            self.q.put_nowait(row)
        except queue.Full:
            pass  # drop the log row, never the answer

    def run(self):
        conn = connect(self.db_path)
        while True:
            batch = [self.q.get()]
            time.sleep(0.2)
            while not self.q.empty() and len(batch) < 500:
                batch.append(self.q.get_nowait())
            for attempt in range(5):
                try:
                    conn.executemany(
                        "INSERT INTO raw_lookups(ts, device, session_id, qname, qtype, cname_chain,"
                        " answer_ips, blocked, block_reason) VALUES (?,?,?,?,?,?,?,?,?)",
                        batch,
                    )
                    conn.commit()
                    break
                except sqlite3.OperationalError as e:
                    print(f"[writer] retry {attempt + 1}: {e}", flush=True)
                    time.sleep(0.5 * (attempt + 1))


# ---------------------------------------------------------------- resolver
class DigInResolver(BaseResolver):
    def __init__(self, db_path, upstreams, blocklist_path, allowlist_path, timeout):
        self.upstreams = upstreams
        self.timeout = timeout
        self.trackers = TrackerLookup(blocklist_path)
        self.allowlist_path = allowlist_path
        self.writer = Writer(db_path)
        self.writer.start()
        self.read_conn = connect(db_path)
        self.read_lock = threading.Lock()
        self.state = {"shield": False, "ml_blocked": set(), "sessions": [], "allow": set()}
        self.state_ts = 0.0
        self.refresh()  # load lists now, not on the first phone query

    def refresh(self):
        """Re-read settings, ml_flags and sessions at most every 2 s (CLAUDE.md task 2)."""
        if time.time() - self.state_ts < 2.0:
            return
        self.state_ts = time.time()
        try:
            with self.read_lock:
                row = self.read_conn.execute("SELECT value FROM settings WHERE key='shield'").fetchone()
                ml = {norm(r[0]) for r in self.read_conn.execute(
                    "SELECT domain FROM ml_flags WHERE status='auto_blocked'")}
                sessions = self.read_conn.execute(
                    "SELECT session_id, device, app_name FROM sessions WHERE end_ts IS NULL"
                    " ORDER BY start_ts DESC").fetchall()
            self.state.update(shield=bool(row and row[0] == "on"), ml_blocked=ml, sessions=sessions,
                              allow=read_domain_file(self.allowlist_path))
            self.trackers.reload_if_changed()
            with self.read_lock:
                self.trackers.update(self.read_conn)
        except sqlite3.Error as e:
            print(f"[refresh] keeping old state: {e}", flush=True)

    def session_for(self, device):
        for sid, dev, app in self.state["sessions"]:
            if not dev or dev == device:
                return sid, app
        return None, None

    def forward(self, request):
        last = None
        for up in self.upstreams:
            try:
                return DNSRecord.parse(request.send(up, 53, timeout=self.timeout))
            except Exception as e:  # noqa: BLE001
                last = e
        print(f"[upstream] all failed for {request.q.qname}: {last}", flush=True)
        reply = request.reply()
        reply.header.rcode = RCODE.SERVFAIL
        return reply

    def block_reason(self, qname, chain, app):
        if not self.state["shield"]:
            return None
        t = self.trackers
        # dns/blocklist.txt entries are deliberate, so they beat the CDN allowlist
        # (e.g. global.moloco.map.fastly.net); the app's-own-company rule still applies.
        listed = t.on_blocklist(qname)
        if not listed and (any(p in self.state["allow"] for p in parents(qname)) or t.is_allowlisted(qname)):
            return None
        own = APP_OWNER.get((app or "").strip().lower())
        if (own and t.company(qname) == own) or t.same_company(qname, app):
            return None
        if any(p in self.state["ml_blocked"] for p in parents(qname)):
            return "ml"
        if listed or t.is_tracker(qname):
            return "list"
        if t.cname_hit(qname, chain, self.state["ml_blocked"]):
            return "cname"
        return None

    def resolve(self, request, handler):
        qname = norm(request.q.qname)
        if qname in ALWAYS_NX:
            reply = request.reply()
            reply.header.rcode = RCODE.NXDOMAIN
            return reply

        reply = self.forward(request)
        try:
            self.refresh()
            device = handler.client_address[0]
            qtype = QTYPE[request.q.qtype]
            sid, app = self.session_for(device)
            chain = [norm(rr.rdata) for rr in reply.rr if rr.rtype == QTYPE.CNAME]
            ips = [str(rr.rdata) for rr in reply.rr if rr.rtype in (QTYPE.A, QTYPE.AAAA)]
            reason = self.block_reason(qname, chain, app)
            if reason:
                reply = request.reply()
                if qtype == "A":
                    reply.add_answer(RR(request.q.qname, QTYPE.A, rdata=A("0.0.0.0"), ttl=30))
                elif qtype == "AAAA":
                    reply.add_answer(RR(request.q.qname, QTYPE.AAAA, rdata=AAAA("::"), ttl=30))
            if qtype in LOGGED_QTYPES:
                self.writer.put((time.time(), device, sid, qname, qtype, json.dumps(chain),
                                 json.dumps(ips), int(bool(reason)), reason))
        except Exception as e:  # noqa: BLE001 - never let logging/Shield break DNS
            print(f"[resolve] {qname}: {e}", flush=True)
        return reply


# ---------------------------------------------------------------- CLI
def cmd_serve(a):
    connect(a.db, init=True).close()
    resolver = DigInResolver(a.db, a.upstream, a.blocklist, a.allowlist, a.timeout)
    servers = [DNSServer(resolver, address=a.listen, port=a.port, logger=QuietLogger()),
               DNSServer(resolver, address=a.listen, port=a.port, tcp=True, logger=QuietLogger())]
    for s in servers:
        s.start_thread()
    src = "ml/lists.py" if _lists is not None else f"{a.blocklist}"
    print(f"DigIn DNS on {a.listen}:{a.port} upstreams={a.upstream} trackers={src} db={a.db}", flush=True)
    while all(s.isAlive() for s in servers):
        time.sleep(1)
    sys.exit(1)  # a server thread died: exit so systemd restarts us


class QuietLogger:
    """dnslib logs every request by default; that's too slow/noisy on the Uno Q."""

    def log_recv(self, *a): pass
    def log_send(self, *a): pass
    def log_request(self, *a): pass
    def log_reply(self, *a): pass
    def log_truncated(self, *a): pass
    def log_data(self, *a): pass
    def log_prefix(self, *a): return ""

    def log_error(self, handler, e):
        print(f"[dnslib] {e}", flush=True)


def cmd_session(a):
    conn = connect(a.db, init=True)
    now = time.time()
    conn.execute("UPDATE sessions SET end_ts=? WHERE end_ts IS NULL", (now,))
    if a.action == "start":
        cur = conn.execute(
            "INSERT INTO sessions(device, app_name, start_ts, end_ts) VALUES (?,?,?,NULL)",
            (a.device or "", a.app or "unknown", now))
        print(f"session {cur.lastrowid} started: {a.app}")
    else:
        print("session ended")
    conn.commit()


def cmd_shield(a):
    conn = connect(a.db, init=True)
    conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('shield', ?)", (a.state,))
    conn.commit()
    print(f"shield {a.state}")


def cmd_tail(a):
    conn = connect(a.db, init=True)
    last_id = (conn.execute("SELECT MAX(id) FROM raw_lookups").fetchone()[0] or 0) - 20
    while True:
        rows = conn.execute(
            "SELECT id, ts, device, session_id, qname, cname_chain, block_reason FROM raw_lookups"
            " WHERE id > ? ORDER BY id", (last_id,)).fetchall()
        for rid, ts, dev, sid, q, chain, reason in rows:
            tag = f"BLOCKED({reason}) " if reason else ""
            names = json.loads(chain or "[]")
            via = f"  via {' -> '.join(names)}" if names else ""
            print(f"{time.strftime('%H:%M:%S', time.localtime(ts))} {dev} s{sid} {tag}{q}{via}")
            last_id = rid
        time.sleep(1)


def main():
    p = argparse.ArgumentParser(description="DigIn DNS logger + Shield")
    p.add_argument("--db", default=DEFAULT_DB)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve")
    s.add_argument("--listen", default="10.42.0.1")
    s.add_argument("--port", type=int, default=53)
    s.add_argument("--upstream", nargs="+", default=["1.1.1.1", "8.8.8.8"])
    s.add_argument("--timeout", type=float, default=2.0)
    s.add_argument("--blocklist", default=BLOCKLIST_PATH)
    s.add_argument("--allowlist", default=ALLOWLIST_PATH)
    s.set_defaults(func=cmd_serve)

    ss = sub.add_parser("session")
    ss.add_argument("action", choices=["start", "end", "stop"])
    ss.add_argument("app", nargs="?")
    ss.add_argument("--device", help="limit to one device IP (default: all devices)")
    ss.set_defaults(func=cmd_session)

    sh = sub.add_parser("shield")
    sh.add_argument("state", choices=["on", "off"])
    sh.set_defaults(func=cmd_shield)

    t = sub.add_parser("tail")
    t.set_defaults(func=cmd_tail)

    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
