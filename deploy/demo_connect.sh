#!/usr/bin/env bash
# deploy/demo_connect.sh - open the Data Receipt dashboard on this Mac and keep the link alive.
# Runs on the presenting Mac, not the board. Leave the window open during the demo; Ctrl-C stops it.
#
#   ./deploy/demo_connect.sh                 # use the last board address that worked
#   ./deploy/demo_connect.sh 10.141.47.104   # board's UWNet address changed? (board: hostname -I)
#
# Tries, in order:
#   1. Mac is on DigIn itself      -> http://10.42.0.1 (no tunnel needed)
#   2. SSH tunnel over the venue Wi-Fi (key ~/.ssh/digin_board) -> http://localhost:8090
# and reconnects automatically if the link drops. (No USB route: the dashboard listens on DigIn
# only, and adb can only reach the board's localhost.)
set -u
PORT=8090
KEY="${DIGIN_KEY:-$HOME/.ssh/digin_board}"
SAVED="$HOME/.digin_board_ip"
CANDIDATES=()
[ -n "${1:-}" ] && CANDIDATES+=("$1")
[ -f "$SAVED" ] && CANDIDATES+=("$(cat "$SAVED")")
CANDIDATES+=("10.141.47.104" "projectWithAI.local")
opened=""

say() { echo "[$(date +%H:%M:%S)] $*"; }
open_once() { [ -n "$opened" ] && return; opened=1; say "Dashboard: $1   Story: $1/story"; open "$1" 2>/dev/null; }
up() { curl -s -m 3 -o /dev/null -w "%{http_code}" "$1" 2>/dev/null | grep -q 200; }

while true; do
  # 1. On DigIn: no tunnel needed
  if up "http://10.42.0.1/health"; then
    open_once "http://10.42.0.1"
    sleep 5; continue
  fi
  # Something already serving the dashboard on our port (an earlier copy of this script)?
  if up "http://localhost:$PORT/health"; then
    open_once "http://localhost:$PORT"
    sleep 5; continue
  fi
  # 2. SSH tunnel over the venue Wi-Fi
  connected=""
  for host in "${CANDIDATES[@]}"; do
    [ -f "$KEY" ] || break
    if ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=4 -o StrictHostKeyChecking=accept-new \
         arduino@"$host" true 2>/dev/null; then
      say "Board reachable at $host, opening tunnel on localhost:$PORT"
      echo "$host" > "$SAVED"
      ssh -i "$KEY" -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=5 \
          -o ServerAliveCountMax=3 -N -L "$PORT:10.42.0.1:80" arduino@"$host" &
      pid=$!
      for _ in 1 2 3 4 5 6 7 8 9 10; do up "http://localhost:$PORT/health" && break; sleep 1; done
      if up "http://localhost:$PORT/health"; then
        connected=1; open_once "http://localhost:$PORT"
        wait "$pid"   # returns when the link drops
        say "Link to $host dropped, reconnecting..."
      else
        kill "$pid" 2>/dev/null; say "Tunnel to $host opened but no dashboard answered"
      fi
      break
    fi
  done
  [ -z "$connected" ] && say "Board not reachable yet (is it on and on the same Wi-Fi?). Retrying in 3 s. Address changed? Run: $0 <address>"
  sleep 3
done
