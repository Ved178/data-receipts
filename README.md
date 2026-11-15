# madhacks26

**Data Receipt** (MadHacks 2026): a Wi-Fi box on an Arduino Uno Q that logs DNS lookups, blocks
known trackers with Shield, and runs an on-device ML model that flags trackers no list knows,
each with a plain-English reason. The team brief is in [`CLAUDE.md`](CLAUDE.md).

Everything meets in one SQLite file, `digin.db` (schema in `db/schema.sql`). The ML part reads
`raw_lookups`, `raw_flows` and `sessions`, and writes `ml_flags`.

## Run the whole box with Docker (Uno Q)

The five services (DNS logger + Shield, dashboard, rules-v1 detector, ml-v1 scorer, flows) run
as containers from one arm64 image. The hotspot, firewall and DNS hand-off stay on the host, as
before. The dashboard is reachable **only on DigIn** (`http://10.42.0.1`), never on the venue
network. Details and troubleshooting: [`docker/README.md`](docker/README.md).

**1. Build the image on your laptop.** An Apple-Silicon Mac builds arm64 natively (~30 s). The
saved file is ~135 MB.
```bash
docker buildx build --platform linux/arm64 -f docker/Dockerfile -t digin:latest --load .
docker save digin:latest | gzip -1 > digin-arm64.tar.gz
```

**2. Copy the image and the repo to the board** while it's online (the demo runs offline).
`adb push` works instead of `scp`.
```bash
scp digin-arm64.tar.gz arduino@<board address>:/home/arduino/
scp -r ../madhacks26 arduino@<board address>:/home/arduino/
```

**3. On the board, check Docker and load the image.** The engine must be version 25 or newer. If
`docker compose version` fails, run `sudo apt-get install docker-compose-plugin`.
```bash
docker version
docker compose version
sudo apt-get install -y --no-install-recommends iw
docker load < ~/digin-arm64.tar.gz
```
`iw` is needed on the board itself (not in Docker): the hotspot uses it to create `ap0`.
(If `docker` says "permission denied", put `sudo` in front, or run `sudo usermod -aG docker arduino` once and log in again.)

**4. Optional per-board settings** in `/etc/digin/board.env` (same as `start_box.sh`). Other keys:
`SSID=`, `AP_MAC=`, `FLOWS=off`.
```bash
sudo mkdir -p /etc/digin
echo 'UPLINK=UWNet' | sudo tee /etc/digin/board.env
```

**5. Start everything (one command):**
```bash
cd /home/arduino/madhacks26 && sudo deploy/start_box_docker.sh
```
This brings up the DigIn hotspot and firewall and starts the containers. It also installs
`digin-box-docker.service`, so the box comes back by itself after every reboot. It disables the
systemd path (`digin-box`, `digin-dns`, ...) so only one copy owns ports 53 and 80.

**6. Check it:** join **DigIn** on a phone (mobile data off) and open `http://10.42.0.1`. On the
board, `docker compose ps` should show dns as "healthy". `logs -f` shows live lookups (also works
for dash, ml, detector, flows). The last line is the session CLI (`start`, `end`, `shield on|off`).
```bash
docker compose ps
docker compose logs -f dns
docker exec digin-dns python dns/digin_dns.py session start "Block Blast"
```

| Need | Command |
|---|---|
| Emergency: plain dnsmasq, no logging | `sudo deploy/start_box_docker.sh fallback` |
| Back to normal | `sudo deploy/start_box_docker.sh` |
| Ship a code change | rebuild (step 1, only the code layer changes), copy, `docker load`, `docker compose up -d` |
| Back to the systemd path | `sudo systemctl disable digin-box-docker; docker compose down; sudo deploy/start_box.sh` |

## ML scorer (`ml/`)

`ml/score.py` scores every domain that is on no list and writes the suspicious ones to
`ml_flags`: `domain`, `score` (0 to 1), `reasons` (JSON array of sentences), `status`
(`suspected`), `model_version` (`ml-v1`). The trained model is committed at
`ml/models/model_ml-v1.joblib`.

Rules it follows:

- Domains already on a blocklist or allowlist are never scored.
- Flags start as `suspected`. `auto_blocked` happens only with `--auto-block` and a score of
  0.97 or more, and never for the current app's own company.
- Rows the dashboard set to `dismissed` or `allowlisted` are never overwritten.
- A locked database is retried with backoff. A missing model is logged, then it sleeps.

### Run it on a laptop

From the repo root:

```
python3 -m venv .venv
.venv/bin/pip install numpy scikit-learn joblib maxminddb pytest
```

Test on a **copy** of a database, never on the real one:

```
cp data/sessions/all.db /tmp/test.db
.venv/bin/python -m ml.score --db /tmp/test.db --once
sqlite3 /tmp/test.db "select domain, round(score,2), reasons from ml_flags order by score desc limit 10;"
```

`--once` does one pass. For a live loop that rescores every 5 seconds, replace it with
`--loop 5` (stop with Ctrl-C). Insert rows into `raw_lookups` from another terminal and new flags
appear on the next pass.

Other flags: `--threshold 0.8` (lowest score written; 0.8 is the default, the dashboard splits flags at 0.95), `--models <dir>`, `--auto-block`.

Check the tests and the held-out numbers:

```
.venv/bin/python -m pytest -q tests
.venv/bin/python -m ml_eval.evaluate --db data/sessions/all.db --holdout InMobi --holdout Vungle --threshold 0.7 --no-write
```

`--no-write` stops it from overwriting `RESULTS.md`. The leak check must print PASS.

### Run it on the Arduino Uno Q

Do the install while the box is online, because the demo runs offline.

1. Get the repo onto the box (git pull). The model file, `ml/data/GeoLite2-ASN.mmdb` and
   `data/lists/` come with it.
2. Install the Python packages on the box: `numpy`, `scikit-learn`, `joblib`, `maxminddb`.
   Use a venv, and download the wheels in advance if the box will be offline.
3. Confirm it runs, using a copy first:
   ```
   cp /home/arduino/digin/digin.db /tmp/digin_copy.db
   python3 -m ml.score --db /tmp/digin_copy.db --once
   ```
   It should print `score: wrote N flags in X s (model ml-v1)`. The target is under 1 second.
4. Run it for real, next to the DNS server:
   ```
   python3 -m ml.score --db /home/arduino/digin/digin.db --loop 5
   ```
   To keep it running, use a systemd unit (the team's units live in `deploy/`) with
   `Restart=always` and the command above as `ExecStart`.

Status: scikit-learn on the Uno Q's ARM CPU has **not been tested yet**. If it fails to install or
import, the fallback from the brief is the rule flags in `ml/cname.py`, which write to the
same table.

If `maxminddb` or the ASN file is missing, the `ip_generic_infra` feature becomes empty and the
scorer still runs, with slightly different scores.

### Retrain or change the data

```
# DNS logs from the box (one session spec per app, times in UTC)
.venv/bin/python -m ml_eval.import_dnsmasq data/collected-data/run4_bike_racing.log \
    --out data/sessions/run4.db --year 2026 --session "Bike Race=01:28:05-01:32:40"

# PCAPdroid exports (one app per file; background apps are dropped by default)
.venv/bin/python -m ml_eval.import_pcapdroid data/pcapdroid/pcap-*.csv --out data/sessions/pcap.db

# merge sources into one training database, then train
.venv/bin/python -m ml_eval.merge_dbs --out data/sessions/all.db data/sessions/run4.db data/sessions/pcap.db
.venv/bin/python -m ml.train --db data/sessions/all.db --version ml-v1
```

Labels come from the lists in `data/lists/`:

| File | Role |
|---|---|
| `services.json` (Disconnect) and `ml/lists.py` `EXTRA` | tracker labels (positives) |
| `content_negatives.txt` | hand-confirmed content domains (negatives) |
| `infrastructure_allowlist.txt` | hand-reviewed CDN and OS domains: never flagged or blocked |
| `allowlist_trackerradar.txt` | generated by `python tools/build_allowlist.py` from Tracker Radar |

Unlisted is not the same as negative. Only add a domain to `content_negatives.txt` once it is
confirmed as content. Whole first-party families can hide tracking hosts, so prefer exact
hostnames.

### Current numbers (honest)

There are three test sets. Quote which one a number comes from.

| Set | What it is | Size |
|---|---|---|
| **A. Official held-out test** | `ml_eval/evaluate.py --holdout InMobi --holdout Vungle --skip-covered`: InMobi and Vungle removed from every list, then scored | 29 tracker domains, 61 content domains |
| **B. Ten-company held-out runs** | the same method repeated ten times, holding out one company each time (A is one of them) | 144 tracker domains in total, the same 61 content domains each run |
| **C. Fresh box sessions** | Maps, Spotify and Samsung Weather recorded on the box after the model was final; flags checked by name | 13 flags at 0.8 |

A and B use data the features, allowlist and threshold were tuned on, so they are optimistic.
C was never tuned on, but it is tiny. `--skip-covered` drops content domains on a list or
allowlist and applies the scorer's login and call-server guards, which is what the box does.

| Threshold | A: official held-out (29) | B: ten companies (144) | C: fresh box sessions |
|---|---|---|---|
| 0.8 | 18 caught (62%), 1 false, 95% precision | 107 caught (74%), mean precision 93% | 8 of 11 judged flags real, about 73% |
| 0.9 | 15 caught (52%), 0 false | 86 caught (60%), mean precision 94% | about 73% |
| 0.95 | 13 caught (45%), 0 false | 74 caught (51%), mean precision 94% (lowest company 67%) | 8 of 9 real, about 89% |

List-only and rules-only baselines catch 0 of 29 in set A, because the held-out companies are
removed from every list. Without `--skip-covered` (counting allowlisted infrastructure such as
Akamai and fbcdn as possible false flags), precision in set A at 0.8 is 35%.

The scorer writes every flag at 0.8 or more. The dashboard shows two tiers: **High confidence**
(0.95 and up) and **Possible** (0.8 to 0.95). In set C the High confidence tier was 8 real
trackers on no list (`atmtd.com` x3, `bids.ws`, `ureca-lab.com`, `rapidedge.io`, `weatherfx.com`,
`em.weather.com`) and 1 false (`api.locketcamera.com`). The Possible tier had 4 hosts, at least 2
of them content (`www5.whentowork.com`, `api.weather.com`). Verdicts should be confirmed by a
second person.

The scorer never scores login hosts (first-label tokens like `login`, `auth`, `sso`, `accounts`) or
STUN/TURN call servers, because blocking either breaks the app and the model tends to score them high.

The model is two gradient-boosting models routed by whether a domain has connection data
(`raw_flows`), each an average of 15 models fit on resampled data to steady the scores. A single model had learned "no flow data" as a clue for "content" and missed
trackers seen only in DNS logs; routing fixed that (AUC 0.93 to 0.97 across the ten companies).
It uses the 13 base features plus 4 timing features. Compare feature groups with
`python3 tools/ablate.py --db data/sessions/all.db`.
