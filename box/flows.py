#!/usr/bin/env python3
"""
box/flows.py - connection logging for Data Receipt. Writes raw_flows rows into digin.db.

Why tcpdump and not conntrack: this Uno Q kernel has conntrack byte counting but no
netlink access (conntrack -E fails with "Protocol not supported"), so we count packets
ourselves from a tcpdump capture on the DigIn interface (ap0). On ap0 we see the phone's
real address and the real server address (before NAT), which is what we want.

One row = one connection (device, server IP, server port, protocol) that went quiet for
IDLE seconds, or has been open for MAX_AGE seconds (long connections are written in
chunks so the ML sees them while they're still running).
  bytes_up   = payload bytes phone -> server     pkts_up   = packets phone -> server
  bytes_down = payload bytes server -> phone     pkts_down = packets server -> phone
Payload bytes come from tcpdump's length field (TCP/UDP payload, headers not counted).

Run on the box (needs root for tcpdump):
  sudo python3 box/flows.py                      # writes /home/arduino/digin/digin.db
  sudo python3 box/flows.py --dry-run            # print rows instead of writing
  sudo python3 box/flows.py --db /tmp/test.db --idle 10
Privacy: stores only addresses, ports, sizes and timings. Never payloads.
"""
import argparse
import ipaddress
import os
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time

DEFAULT_DB = "/home/arduino/digin/digin.db"
LAN = ipaddress.ip_network("10.42.0.0/24")
BOX = "10.42.0.1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_flows (
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, device TEXT NOT NULL, session_id INTEGER,
  dst_ip TEXT NOT NULL, dst_port INTEGER, proto TEXT,
  bytes_up INTEGER, bytes_down INTEGER, pkts_up INTEGER, pkts_down INTEGER, duration_s REAL
);
CREATE INDEX IF NOT EXISTS ix_flows_ts ON raw_flows(ts);
"""

# tcpdump -n -tt -q -l output, e.g.
#   1731628800.123456 IP 10.42.0.33.51234 > 142.250.191.46.443: tcp 517
#   1731628800.123456 IP 142.250.191.46.443 > 10.42.0.33.51234: UDP, length 1250
LINE = re.compile(
    r"^(?P<ts>\d+\.\d+) IP (?P<src>\d+\.\d+\.\d+\.\d+)\.(?P<sport>\d+) > "
    r"(?P<dst>\d+\.\d+\.\d+\.\d+)\.(?P<dport>\d+): (?P<rest>.*)$"
)
TCP_LEN = re.compile(r"^tcp (\d+)")
UDP_LEN = re.compile(r"^UDP, length (\d+)")


def is_lan(ip):
    try:
        return ipaddress.ip_address(ip) in LAN
    except ValueError:
        return False


def parse(line):
    """Return (ts, device, server_ip, server_port, proto, nbytes, upload) or None."""
    m = LINE.match(line.strip())
    if not m:
        return None
    rest = m["rest"]
    t = TCP_LEN.match(rest)
    u = UDP_LEN.match(rest)
    if t:
        proto, n = "tcp", int(t.group(1))
    elif u:
        proto, n = "udp", int(u.group(1))
    else:
        return None
    src, dst = m["src"], m["dst"]
    if is_lan(src) and not is_lan(dst):
        return float(m["ts"]), src, dst, int(m["dport"]), proto, n, True
    if is_lan(dst) and not is_lan(src):
        return float(m["ts"]), dst, src, int(m["sport"]), proto, n, False
    return None  # LAN-to-LAN or box traffic: not a flow to the internet


class FlowTable:
    """Aggregates packets into flows; hands finished flows to emit()."""

    def __init__(self, idle, max_age, emit):
        self.idle, self.max_age, self.emit = idle, max_age, emit
        self.flows = {}
        self.lock = threading.Lock()

    def add(self, p):
        ts, dev, ip, port, proto, n, up = p
        key = (dev, ip, port, proto)
        with self.lock:
            f = self.flows.get(key)
            if f is None:
                f = self.flows[key] = {"first": ts, "last": ts, "bu": 0, "bd": 0, "pu": 0, "pd": 0}
            f["last"] = ts
            if up:
                f["bu"] += n
                f["pu"] += 1
            else:
                f["bd"] += n
                f["pd"] += 1

    def sweep(self, now, force=False):
        done = []
        with self.lock:
            for key, f in list(self.flows.items()):
                if force or now - f["last"] >= self.idle or now - f["first"] >= self.max_age:
                    done.append((key, f))
                    del self.flows[key]
        for key, f in done:
            self.emit(key, f)
        return len(done)


class Writer:
    """Batches rows into SQLite; survives a locked DB by retrying next round."""

    def __init__(self, db_path, dry_run):
        self.dry_run = dry_run
        self.pending = []
        self.lock = threading.Lock()
        self.conn = None
        if not dry_run:
            os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
            self.conn = sqlite3.connect(db_path, timeout=3, check_same_thread=False)
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA busy_timeout=3000")
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def session_for(self, device, ts):
        if self.conn is None:
            return None
        try:
            row = self.conn.execute(
                "SELECT session_id FROM sessions WHERE (device = ? OR device IS NULL OR device = '') "
                "AND start_ts <= ? AND (end_ts IS NULL OR end_ts >= ?) "
                "ORDER BY start_ts DESC LIMIT 1", (device, ts, ts)).fetchone()
            return row[0] if row else None
        except sqlite3.Error:
            return None  # sessions table not created yet

    def add(self, key, f):
        dev, ip, port, proto = key
        row = (f["first"], dev, self.session_for(dev, f["first"]), ip, port, proto,
               f["bu"], f["bd"], f["pu"], f["pd"], round(f["last"] - f["first"], 3))
        if self.dry_run:
            print(f"{time.strftime('%H:%M:%S', time.localtime(row[0]))} {dev} -> {ip}:{port}/{proto} "
                  f"up {f['bu']}B/{f['pu']}p  down {f['bd']}B/{f['pd']}p  {row[-1]}s", flush=True)
            return
        with self.lock:
            self.pending.append(row)

    def flush(self):
        if self.dry_run or not self.pending:
            return 0
        with self.lock:
            rows, self.pending = self.pending, []
        try:
            self.conn.executemany(
                "INSERT INTO raw_flows (ts, device, session_id, dst_ip, dst_port, proto, "
                "bytes_up, bytes_down, pkts_up, pkts_down, duration_s) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
            self.conn.commit()
            return len(rows)
        except sqlite3.OperationalError as e:
            print(f"DB busy ({e}); retrying {len(rows)} rows next round", file=sys.stderr)
            with self.lock:
                self.pending = rows + self.pending
            return 0


def main():
    ap = argparse.ArgumentParser(description="Log per-connection byte counts into raw_flows.")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--iface", default="ap0")
    ap.add_argument("--idle", type=float, default=15.0, help="seconds of silence that end a flow")
    ap.add_argument("--max-age", type=float, default=60.0, help="write long flows in chunks this long")
    ap.add_argument("--dry-run", action="store_true", help="print flows instead of writing the DB")
    ap.add_argument("--stdin", action="store_true", help="read tcpdump lines from stdin (testing)")
    a = ap.parse_args()

    writer = Writer(a.db, a.dry_run)
    table = FlowTable(a.idle, a.max_age, writer.add)
    stop = threading.Event()

    def ticker():
        total = 0
        while not stop.wait(2.0):
            table.sweep(time.time())
            n = writer.flush()
            if n:
                total += n
                print(f"wrote {n} flows (total {total})", flush=True)

    if a.stdin:
        for line in sys.stdin:
            p = parse(line)
            if p:
                table.add(p)
        table.sweep(0, force=True)
        writer.flush()
        return 0

    cmd = ["tcpdump", "-i", a.iface, "-n", "-tt", "-q", "-l", "-s", "96",
           f"ip and not host {BOX}"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, bufsize=1)

    def shutdown(*_):
        stop.set()
        proc.terminate()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    threading.Thread(target=ticker, daemon=True).start()
    print(f"flows.py: capturing on {a.iface} -> {'stdout' if a.dry_run else a.db}", flush=True)

    for line in proc.stdout:
        p = parse(line)
        if p:
            table.add(p)
        if stop.is_set():
            break
    stop.set()
    table.sweep(0, force=True)
    writer.flush()
    if proc.poll() not in (None, 0, -15):
        print(f"tcpdump exited with code {proc.returncode}: is {a.iface} up? running as root?",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
