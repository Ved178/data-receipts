#!/bin/bash
# box/hotspot.sh - bring up the DigIn Wi-Fi on the Uno Q. Safe to run again (idempotent).
# Run after every boot:   sudo ./box/hotspot.sh
#
# How it works (proven Sat 5:40 PM):
#   wlan0 stays a normal Wi-Fi client (the box's internet), and a second virtual
#   interface ap0 runs DigIn at 10.42.0.1. The WCN3980 chip allows client + AP at once.
#   If the internet comes from USB tethering instead, that works too: UP is taken
#   from the default route.
# DNS: NetworkManager's dnsmasq answers DNS for DigIn and reads
#   /etc/NetworkManager/dnsmasq-shared.d/digin.conf (logging + blocklist).
#   When Aarav's digin_dns.py takes over port 53, put "port=0" and
#   "dhcp-option=6,10.42.0.1" in that file so dnsmasq only hands out addresses.
set -u
WIFI=wlan0
AP=ap0
AP_MAC=02:12:34:56:78:9a
SSID=DigIn
PSK=digin1234
CHANNEL=6
DIR="$(cd "$(dirname "$0")" && pwd)"

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo"; exit 1; }

UP=$(ip route show default | awk '{print $5; exit}')
if [ -z "$UP" ]; then
  echo "WARN: no internet route. DigIn will start but phones get no internet."
  UP=$WIFI
fi
echo "Internet comes from: $UP"

# 1. Virtual AP interface (disappears on reboot, so recreate it)
iw dev "$AP" info >/dev/null 2>&1 || iw dev "$WIFI" interface add "$AP" type __ap
ip link set "$AP" address "$AP_MAC"
nmcli device set "$AP" managed yes
sleep 2

# 2. DigIn profile (create once; always pin interface, MAC and IPv4-only)
if ! nmcli -t -f NAME connection show | grep -qx "$SSID"; then
  nmcli connection add type wifi ifname "$AP" con-name "$SSID" autoconnect no ssid "$SSID" \
    802-11-wireless.mode ap 802-11-wireless.band bg 802-11-wireless.channel "$CHANNEL" \
    ipv4.method shared wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PSK"
fi
nmcli connection modify "$SSID" connection.interface-name "$AP" \
  802-11-wireless.cloned-mac-address "$AP_MAC" ipv6.method disabled
nmcli connection up "$SSID" || { echo "FAILED to start DigIn"; exit 1; }

# 3. Forwarding + NAT (Docker sets FORWARD to drop, so allow DigIn explicitly)
sysctl -qw net.ipv4.ip_forward=1
iptables -C FORWARD -i "$AP" -o "$UP" -j ACCEPT 2>/dev/null || \
  iptables -I FORWARD 1 -i "$AP" -o "$UP" -j ACCEPT
iptables -C FORWARD -i "$UP" -o "$AP" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || \
  iptables -I FORWARD 1 -i "$UP" -o "$AP" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
iptables -t nat -C POSTROUTING -s 10.42.0.0/24 -o "$UP" -j MASQUERADE 2>/dev/null || \
  iptables -t nat -A POSTROUTING -s 10.42.0.0/24 -o "$UP" -j MASQUERADE

# 4. Firewall rules (must run after step 3 so its REJECTs sit above the ACCEPTs)
[ -x "$DIR/firewall.sh" ] && "$DIR/firewall.sh"

ip -4 addr show "$AP" | grep "inet "
echo "DigIn is up (internet via $UP)"
