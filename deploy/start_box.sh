#!/usr/bin/env bash
# Bring up the whole box: hotspot DNS hand-off, firewall, DNS logger. Safe to run twice.
# Usage: sudo deploy/start_box.sh   (run from the repo root on the Uno Q)
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
CONF=/etc/NetworkManager/dnsmasq-shared.d/digin.conf

mkdir -p /home/arduino/digin
chmod 777 /home/arduino/digin  # service runs as root, CLI as arduino: both must write the DB

# 1. Hotspot hands out addresses only; our Python server answers DNS on 10.42.0.1:53.
mkdir -p /etc/NetworkManager/dnsmasq-shared.d
cat > "$CONF" <<CONF
port=0
dhcp-option=option:dns-server,10.42.0.1
CONF

# 2. Hotspot up (Pranshu's script if present, else bring the DigIn connection up).
if [ -x "$REPO/box/hotspot.sh" ]; then "$REPO/box/hotspot.sh"; fi
nmcli connection down DigIn >/dev/null 2>&1 || true
nmcli connection up DigIn

# 3. Firewall: force devices off encrypted DNS (Pranshu's script).
if [ -x "$REPO/box/firewall.sh" ]; then "$REPO/box/firewall.sh"; fi

# 4. DNS logger + dashboard as services (auto-restart).
for unit in digin-dns digin-dash; do
  sed "s#/home/arduino/madhacks26#$REPO#" "$REPO/deploy/$unit.service" > /etc/systemd/system/$unit.service
done
systemctl daemon-reload
systemctl enable --now digin-dns digin-dash
systemctl restart digin-dns digin-dash
sleep 2
systemctl --no-pager --lines=3 status digin-dns digin-dash || true
echo "Box up. Dashboard: http://10.42.0.1   Watch lookups: python3 $REPO/dns/digin_dns.py tail"
