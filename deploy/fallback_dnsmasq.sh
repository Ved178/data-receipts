#!/usr/bin/env bash
# EMERGENCY: if digin_dns.py misbehaves, hand DNS back to plain dnsmasq forwarding in one command.
# Phones keep working; we just stop logging and blocking. Undo with: sudo deploy/start_box.sh
set -euo pipefail
systemctl stop digin-dns || true
cat > /etc/NetworkManager/dnsmasq-shared.d/digin.conf <<CONF
server=1.1.1.1
server=8.8.8.8
CONF
nmcli connection down DigIn >/dev/null 2>&1 || true
nmcli connection up DigIn
echo "Fallback active: plain DNS forwarding, no logging, no Shield."
