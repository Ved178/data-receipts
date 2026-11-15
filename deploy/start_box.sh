#!/usr/bin/env bash
# Bring up the whole box: Pranshu's hotspot + firewall, then our DNS logger + dashboard. Safe to run twice.
# Usage (repo root on the Uno Q):  sudo deploy/start_box.sh
# Undo / switch back to Pranshu's plain dnsmasq mode:  sudo deploy/fallback_dnsmasq.sh
#
# Per-board settings (optional) in /etc/digin/board.env, e.g. for board #2:
#   SSID=DigIn2
#   AP_MAC=02:12:34:56:78:9b
# Without that file, box/hotspot.sh runs exactly as Pranshu wrote it (DigIn, ...:9a).
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo"; exit 1; }
REPO="$(cd "$(dirname "$0")/.." && pwd)"
CONF_DIR=/etc/NetworkManager/dnsmasq-shared.d
HANDOFF="$CONF_DIR/zz-aarav-dns-handoff.conf"   # ours; Pranshu's digin.conf is never touched
RUN=/var/lib/digin   # not /run: it is mounted noexec on the Uno Q

mkdir -p /home/arduino/digin
chmod 777 /home/arduino/digin  # service runs as root, CLI as arduino: both must write the DB

# 1. Hand DNS to our logger: NetworkManager's dnsmasq keeps giving out addresses (DHCP) but stops
#    answering DNS (port=0) and tells phones to ask 10.42.0.1, where digin_dns.py listens.
mkdir -p "$CONF_DIR"
cat > "$HANDOFF" <<'CONF'
# Added by deploy/start_box.sh (Aarav). Delete this file to give DNS back to dnsmasq.
port=0
dhcp-option=6,10.42.0.1
CONF

# 2. Pranshu's hotspot (creates ap0, brings the hotspot up -> dnsmasq re-reads the files above,
#    sets NAT, runs his firewall.sh). Run from a copy so per-board SSID/MAC never edit his file.
HOT="$REPO/box/hotspot.sh"
FW="$REPO/box/firewall.sh"; [ -f "$FW" ] || FW="$REPO/box/box/firewall.sh"
[ -f "$HOT" ] || { echo "Missing $HOT (Pranshu's box/ folder)"; exit 1; }
SSID=DigIn; AP_MAC=
[ -f /etc/digin/board.env ] && . /etc/digin/board.env
mkdir -p "$RUN"
sed -e "s/^SSID=.*/SSID=${SSID}/" ${AP_MAC:+-e "s/^AP_MAC=.*/AP_MAC=${AP_MAC}/"} "$HOT" > "$RUN/hotspot.sh"
[ -f "$FW" ] && cp "$FW" "$RUN/firewall.sh"
chmod +x "$RUN"/*.sh
"$RUN/hotspot.sh"

# 3. DNS logger + dashboard as services (auto-restart).
for unit in digin-dns digin-dash; do
  sed "s#/home/arduino/madhacks26#$REPO#" "$REPO/deploy/$unit.service" > /etc/systemd/system/$unit.service
done
systemctl daemon-reload
systemctl enable digin-dns digin-dash
systemctl restart digin-dns digin-dash
sleep 2
systemctl --no-pager --lines=3 status digin-dns digin-dash || true
echo "Box up on $SSID. Dashboard: http://10.42.0.1   Watch lookups: python3 $REPO/dns/digin_dns.py tail"
