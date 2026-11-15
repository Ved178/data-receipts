# dns/ — DigIn DNS logger + Shield (owner: Aarav)

Writes `raw_lookups`, `sessions`, `settings` in `digin.db` (frozen schema: `db/schema.sql`).

## On the Uno Q (once)
    pip install dnslib --break-system-packages
    git clone <repo> /home/arduino/madhacks26 && cd /home/arduino/madhacks26

## Plan A (box hosts DigIn at 10.42.0.1)
    sudo deploy/start_box.sh          # Pranshu's box/hotspot.sh + firewall, DNS hand-off, logger + dashboard
    python3 dns/digin_dns.py tail     # watch lookups live
    sudo deploy/fallback_dnsmasq.sh   # undo: back to Pranshu's plain dnsmasq mode (start_box.sh to return)
Run start_box.sh once; it installs digin-box.service, so everything comes back by itself after a reboot
(tested on board #2: up 15 s after boot). Disable with `sudo systemctl disable digin-box`.
Second board: put `SSID=DigIn2`, `AP_MAC=02:12:34:56:78:9b`, `UPLINK=UWNet` in /etc/digin/board.env.
No internet on the board yet (UWNet login)? Packages can be copied over USB: `apt install` needs
iw, conntrack, python3-dnslib. Dashboard from a laptop over USB: `adb forward tcp:8090 tcp:80`.

## Plan B (box and phone on a teammate's hotspot)
    hostname -I                       # BOARD_IP
    sudo python3 dns/digin_dns.py serve --listen BOARD_IP
    # phone: Wi-Fi -> (i) -> Configure DNS -> Manual -> BOARD_IP; mobile data OFF

## Commands (Start / Shield buttons call these)
    python3 dns/digin_dns.py session start "Block Blast"
    python3 dns/digin_dns.py session end
    python3 dns/digin_dns.py shield on | off

## If DNS ever misbehaves during the demo
    sudo deploy/fallback_dnsmasq.sh   # plain forwarding, phones keep working

## Files
- `blocklist.txt` known trackers, used until `ml/lists.py` (Samarth) exists; then lists.py is used automatically.
- `allowlist.txt` never blocked (CDNs, phone system checks).
- `APP_OWNER` in digin_dns.py: app name -> company, so Shield never blocks an app's own company.

Test DB with example rows for other teams:
    sqlite3 /tmp/seed.db < db/schema.sql && sqlite3 /tmp/seed.db < db/seed.sql

## Dashboard (dashboard/)
    sudo python3 dashboard/app.py                       # http://10.42.0.1 (port 80), started by start_box.sh
    python3 dashboard/app.py --port 8080 --db /tmp/seed.db   # try it on a laptop with the seed data
Plan B: open http://BOARD_IP from any laptop on the same hotspot.
