#!/bin/sh
# Wait for what a service needs, then exec it. Docker's restart backoff doubles on every crash,
# so crash-looping at boot until the hotspot is up would leave DNS dead for tens of seconds
# (CLAUDE.md §6.3). Waiting here instead means the service starts within 0.5 s of ap0 coming up.
#   WAIT_FOR=addr   wait until 10.42.0.1 is on a host interface (dns, dash)
#   WAIT_FOR=iface  wait until ap0 exists and is up              (flows)
#   WAIT_FOR=db     wait until digin.db exists                    (detector, ml)
umask 0000   # host CLI runs as arduino, containers as root: both must write the DB
BOX_IP="${BOX_IP:-10.42.0.1}"; IFACE="${IFACE:-ap0}"
n=0
case "${WAIT_FOR:-}" in
  addr)  until ip -4 -o addr show | grep -q "inet $BOX_IP/"; do
           [ $((n += 1)) -eq 20 ] && echo "[entrypoint] waiting for $BOX_IP (hotspot up?)"; sleep 0.5; done ;;
  iface) until ip link show "$IFACE" 2>/dev/null | grep -q "state UP\|LOWER_UP"; do
           [ $((n += 1)) -eq 20 ] && echo "[entrypoint] waiting for $IFACE"; sleep 0.5; done ;;
  db)    until [ -f "$DIGIN_DB" ]; do
           [ $((n += 1)) -eq 20 ] && echo "[entrypoint] waiting for $DIGIN_DB"; sleep 0.5; done ;;
esac
exec "$@"
