#!/usr/bin/env bash
# Docker variant of deploy/start_box.sh: same host networking, services in containers.
# Host keeps: DNS hand-off conf, Pranshu's hotspot.sh + firewall.sh (nmcli / iw / iptables).
# Containers: dns, dash, detector, ml (+ flows), from the image built by docker/Dockerfile.
# Usage (repo root on the Uno Q):  sudo deploy/start_box_docker.sh            # bring up
#                                  sudo deploy/start_box_docker.sh fallback   # = fallback_dnsmasq.sh
# Board settings come from /etc/digin/board.env exactly as for start_box.sh (SSID, AP_MAC, UPLINK, FLOWS).
# Like start_box.sh, every run also installs its boot unit (digin-box-docker) and disables the
# systemd path (digin-box + service units), so only one copy ever owns :53 and :80.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo"; exit 1; }
REPO="$(cd "$(dirname "$0")/.." && pwd)"
CONF_DIR=/etc/NetworkManager/dnsmasq-shared.d
HANDOFF="$CONF_DIR/zz-aarav-dns-handoff.conf"
RUN=/var/lib/digin
UNITS="digin-dns digin-dash digin-detector digin-ml digin-flows"
SSID=DigIn; AP_MAC=; UPLINK=; FLOWS=on
[ -f /etc/digin/board.env ] && . /etc/digin/board.env
compose() { docker compose -f "$REPO/docker-compose.yml" "$@"; }

case "${1:-up}" in
  fallback)   # plain dnsmasq DNS, no logging (same effect as deploy/fallback_dnsmasq.sh)
    compose stop dns
    rm -f "$HANDOFF"
    nmcli connection up "$SSID" >/dev/null || { echo "FAILED to restart $SSID"; exit 1; }
    [ -x "$RUN/firewall.sh" ] && "$RUN/firewall.sh" >/dev/null
    echo "Fallback active on $SSID: dnsmasq answers DNS. Back: sudo $0"; exit 0 ;;
  up) ;;
  *) echo "usage: $0 [up|fallback]"; exit 2 ;;
esac

docker compose version >/dev/null 2>&1 || {
  echo "docker compose plugin missing: sudo apt-get install docker-compose-plugin"; exit 1; }
docker image inspect digin:latest >/dev/null 2>&1 || {
  echo "No digin:latest image. Load it: docker load < digin-arm64.tar.gz (see docker/README.md)"; exit 1; }

# 0. One owner per port and one boot path: stop the systemd copies (they'd fight over :53 / :80),
#    disable their boot unit, install ours. Rewritten only when changed, so boot runs skip reload.
systemctl disable --now $UNITS 2>/dev/null || true
systemctl disable digin-box 2>/dev/null || true
mkdir -p "$RUN"
sed "s#/home/arduino/madhacks26#$REPO#" "$REPO/deploy/digin-box-docker.service" > "$RUN/digin-box-docker.service"
cmp -s "$RUN/digin-box-docker.service" /etc/systemd/system/digin-box-docker.service || {
  cp "$RUN/digin-box-docker.service" /etc/systemd/system/; systemctl daemon-reload; }
systemctl enable --no-reload digin-box-docker 2>/dev/null || true

mkdir -p /home/arduino/digin && chmod 777 /home/arduino/digin

# 1. DNS hand-off: dnsmasq keeps DHCP, stops answering DNS, points phones at 10.42.0.1.
mkdir -p "$CONF_DIR"
cat > "$HANDOFF" <<'CONF'
# Added by deploy/start_box.sh (Aarav). Delete this file to give DNS back to dnsmasq.
port=0
dhcp-option=6,10.42.0.1
CONF

# 2. Start containers now: they wait inside for 10.42.0.1 / ap0, so Python + scikit-learn
#    imports overlap with the hotspot coming up instead of following it.
PROFILE=(); [ "$FLOWS" != off ] && PROFILE=(--profile flows)
compose "${PROFILE[@]}" up -d --remove-orphans
[ "$FLOWS" = off ] && compose rm -sf flows >/dev/null 2>&1 || true

# 3. Pranshu's hotspot + firewall, run from a copy (per-board SSID/MAC never edit his file).
HOT="$REPO/box/hotspot.sh"
FW="$REPO/box/firewall.sh"; [ -f "$FW" ] || FW="$REPO/box/box/firewall.sh"
[ -f "$HOT" ] || { echo "Missing $HOT"; exit 1; }
[ -n "$UPLINK" ] && { nmcli connection modify "$UPLINK" connection.autoconnect-priority 10 || true; }
for _ in $(seq 1 30); do ip route show default | grep -q . && break; sleep 2; done
mkdir -p "$RUN"
sed -e "s/^SSID=.*/SSID=${SSID}/" ${AP_MAC:+-e "s/^AP_MAC=.*/AP_MAC=${AP_MAC}/"} "$HOT" > "$RUN/hotspot.sh"
[ -f "$FW" ] && cp "$FW" "$RUN/firewall.sh"
chmod +x "$RUN"/*.sh
"$RUN/hotspot.sh"
# Re-activating DigIn restarts NM's dnsmasq; on a re-run (e.g. after fallback) it may have held
# :53 when dns started. Restart the two light services now that 10.42.0.1 is ours (ml/detector
# keep running, so scikit-learn isn't re-imported).
compose restart -t 2 dns dash >/dev/null

# 4. Wait (max 20 s) for DNS to answer, then report.
for _ in $(seq 1 40); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' digin-dns 2>/dev/null)" = healthy ] && break; sleep 0.5
done
compose "${PROFILE[@]}" ps
echo "Box up on $SSID (Docker). Dashboard: http://10.42.0.1   Logs: docker compose logs -f dns"
echo "Back to the systemd path: sudo systemctl disable digin-box-docker; docker compose down; sudo deploy/start_box.sh"
