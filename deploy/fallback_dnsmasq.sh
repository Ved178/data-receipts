#!/usr/bin/env bash
# EMERGENCY / undo: go back to Pranshu's mode in one command. dnsmasq answers DNS again (with his
# digin.conf if present); our logger stops. Phones keep internet; we just stop logging and Shield.
# Usage: sudo deploy/fallback_dnsmasq.sh        Back to our mode: sudo deploy/start_box.sh
set -uo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo"; exit 1; }
SSID=DigIn
[ -f /etc/digin/board.env ] && . /etc/digin/board.env

systemctl stop digin-dns 2>/dev/null
rm -f /etc/NetworkManager/dnsmasq-shared.d/zz-aarav-dns-handoff.conf
nmcli connection up "$SSID" >/dev/null || { echo "FAILED to restart $SSID"; exit 1; }
# Re-apply firewall rules (re-activating the hotspot can reset them)
[ -x /var/lib/digin/firewall.sh ] && /var/lib/digin/firewall.sh >/dev/null
echo "Fallback active on $SSID: dnsmasq answers DNS (Pranshu's mode). Our logger stopped."
