# Docker setup for the Uno Q

One arm64 image (`digin:latest`) runs the five Python services as containers. The networking stays
on the host: NetworkManager hotspot, `iw`, iptables, and the dnsmasq DNS hand-off. Containers
can't drive these safely.

| Container | Runs | Waits for |
|---|---|---|
| digin-dns | `dns/digin_dns.py serve` (healthchecked) | 10.42.0.1 on the host |
| digin-dash | `dashboard/app.py` on 10.42.0.1:80 | 10.42.0.1 |
| digin-detector | rules-v1 `dns/detector.py --loop 5` | digin.db |
| digin-ml | ml-v1 `python -m ml.score --loop 5` | digin.db |
| digin-flows | `box/flows.py` (tcpdump on ap0), profile `flows` | ap0 up |

All containers use host networking and bind-mount `/home/arduino/digin`, so the DB path is the same
as before. The CLI from the host, which doesn't need dnslib installed on the host:
`docker exec digin-dns python dns/digin_dns.py session start "Block Blast"` (or `end`, `shield on`).

## Why it starts fast
- **Waits instead of crash-looping.** Each container waits inside for what it needs (checking
  every 0.5 s), so Docker's growing restart backoff never kicks in at boot. DNS starts about 0.5 s
  after the hotspot comes up.
- **Overlapping startup.** The containers start *before* `hotspot.sh` runs, so the slow
  Python/scikit-learn imports happen while the hotspot comes up.
- **Precompiled bytecode** for the code and site-packages, so a cold start skips compiling.
- **Model checked at build time.** The build fails if `model_ml-v1.joblib` won't load. scikit-learn
  is pinned to 1.9.1, the version the model was trained with.
- **Boots without the script.** `restart: always` means Docker itself restarts the containers at
  boot. `init: true` and a 3 s stop grace give quick, clean restarts. Logs are capped at 2×5 MB.

## Build (on a Mac with Apple Silicon: native arm64, about 30 s)
```bash
docker buildx build --platform linux/arm64 -f docker/Dockerfile -t digin:latest --load .
docker save digin:latest | gzip -1 > digin-arm64.tar.gz      # ~135 MB (646 MB unpacked)
scp digin-arm64.tar.gz arduino@<board>:/home/arduino/
```
Or build on the board itself (needs internet, slower). Either way, the board never pulls anything at
demo time.

## On the board
```bash
docker version; docker compose version           # App Lab ships Docker; check both (engine >= 25)
docker load < ~/digin-arm64.tar.gz
cd /home/arduino/madhacks26
sudo deploy/start_box_docker.sh                  # hotspot + firewall + containers; also installs
                                                 # digin-box-docker (boot) and disables digin-box
```
`/etc/digin/board.env` works as before (`SSID`, `AP_MAC`, `UPLINK`, `FLOWS=off`).

The script runs `systemctl disable --now` on the systemd `digin-dns/dash/detector/ml/flows` units,
so ports 53 and 80 have one owner. To go back to the systemd path:
`sudo systemctl disable digin-box-docker; docker compose down; sudo deploy/start_box.sh`.
If an older engine rejects `start_interval` in `docker-compose.yml`, delete that line.

| Task | Command |
|---|---|
| Status | `docker compose ps` |
| Logs | `docker compose logs -f dns` (or dash, ml, ...) |
| Emergency: plain dnsmasq | `sudo deploy/start_box_docker.sh fallback` |
| Back to logging | `sudo deploy/start_box_docker.sh` |
| New code, same deps | rebuild (only the code layer changes, a few seconds), `docker save`/`load`, `docker compose up -d` |
