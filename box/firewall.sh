#!/bin/bash
# box/firewall.sh - make DigIn devices use the box for DNS. Safe to run again.
# Called by hotspot.sh. Rules (docs/team-brief.md section 3):
#   - REJECT (not DROP) DNS-over-TLS (port 853) so phones fall back to us fast
#   - REJECT DNS-over-HTTPS to well-known public resolvers (port 443)
#   - Send plain DNS aimed at outside servers (e.g. hard-coded 8.8.8.8:53) to the box
#     (DNAT, because this kernel has no REDIRECT module)
set -u
AP=ap0
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo"; exit 1; }

ins() { iptables -C "$@" 2>/dev/null || iptables -I "$@"; }

ins FORWARD -i "$AP" -p tcp --dport 853 -j REJECT --reject-with tcp-reset
ins FORWARD -i "$AP" -p udp --dport 853 -j REJECT

for ip in 1.1.1.1 1.0.0.1 8.8.8.8 8.8.4.4 9.9.9.9 149.112.112.112; do
  ins FORWARD -i "$AP" -d "$ip" -p tcp --dport 443 -j REJECT --reject-with tcp-reset
  ins FORWARD -i "$AP" -d "$ip" -p udp --dport 443 -j REJECT
done

BOX=10.42.0.1
for proto in udp tcp; do
  iptables -t nat -C PREROUTING -i "$AP" -p "$proto" --dport 53 ! -d "$BOX" -j DNAT --to-destination "$BOX:53" 2>/dev/null || \
    iptables -t nat -I PREROUTING -i "$AP" -p "$proto" --dport 53 ! -d "$BOX" -j DNAT --to-destination "$BOX:53"
done
echo "Firewall rules in place"
