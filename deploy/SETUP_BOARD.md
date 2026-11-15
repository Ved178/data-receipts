# Set up a Data Receipt box from scratch (board #1 / any Uno Q)

Written by Aarav for Ved. Follow it top to bottom on **board #1** (Pranshu's board). It ends with the
exact same product that runs on board #2 (DigIn2), which is now **code-frozen as the safe demo box**.
Do the ML work on board #1 only. Don't change board #2.

Total time: about 30–40 min (most of it waiting for downloads).

---

## 0. What the box does (1 minute read)

```
phone ──Wi-Fi "DigIn" (ap0, 10.42.0.1)──► Uno Q ──Wi-Fi client "UWNet" (wlan0)──► internet
                                              │
  NetworkManager's dnsmasq  : hands phones an address (DHCP only, does NOT answer DNS)
  digin-dns  (dns/digin_dns.py)   : answers every DNS lookup, logs it to digin.db, Shield blocks trackers
  digin-dash (dashboard/app.py)   : dashboard at http://10.42.0.1  (+ /story page)
  digin-detector (dns/detector.py): rules-v1 detector, writes ml_flags every 5 s
  digin-box  (deploy/start_box.sh): runs all of the above at every boot
```

- Everything shares one SQLite file: `/home/arduino/digin/digin.db` (schema: `db/schema.sql`, frozen).
- Pranshu's `box/hotspot.sh` + `box/firewall.sh` create the hotspot on a second interface
  (`ap0`), so the board stays on UWNet at the same time.
- `start_box.sh` never edits Pranshu's `/etc/NetworkManager/dnsmasq-shared.d/digin.conf`. It adds its
  own `zz-aarav-dns-handoff.conf` (`port=0`, `dhcp-option=6,10.42.0.1`). Delete that one file (or run
  `deploy/fallback_dnsmasq.sh`) to go back to Pranshu's plain dnsmasq mode.

## 1. What to download

From GitHub: **`Ved178/madhacks26`, branch `main`** (after PR #4 is merged; it has the last fixes).

```bash
git clone https://github.com/Ved178/madhacks26.git
cd madhacks26 && git log --oneline -3      # should include "SETUP_BOARD.md"
```
(The repo is private, so clone it on your laptop with your GitHub login, then copy it to the board in
step 4. The board itself doesn't need GitHub access.)

## 2. Connect to the board

1. Plug the board's USB-C **straight into the laptop** (no hub). Wait ~1 min.
2. Open **Arduino App Lab**, click the board, open the **>_** terminal.
3. Check the admin password works:
   ```bash
   sudo -v
   ```
   No message = OK. If it says `Sorry, try again` 3 times, stop and redo the board's first-time setup
   in App Lab.

App Lab's copy of `adb` lets you copy files over USB. On a Mac it's at
`~/Library/Arduino15/packages/arduino/tools/adb/32.0.0/adb` (Windows: under
`%LOCALAPPDATA%\Arduino15\packages\arduino\tools\adb\...`). Check with `adb devices`.
USB `adb` sometimes goes "offline"; step 9 sets up SSH over Wi-Fi, which is more reliable.

## 3. Board internet + packages

1. Join UWNet (open network, no password), on the board:
   ```bash
   sudo nmcli device wifi connect UWNet
   ```
   UWNet holds the board at a login page until a person logs in. You'll do that from your phone in
   step 6 (through the box), so for now the board may have no internet.
   If it already has internet (check: `curl -sI https://www.google.com | head -1`), go on.
   If it's stuck at the login page, either use a phone hotspot for the board for these installs
   (`sudo nmcli device wifi connect "<hotspot name>" password "<password>"`), or do step 6 first.
2. Install what the code needs:
   ```bash
   sudo apt-get update
   sudo apt-get install -y --no-install-recommends iw conntrack python3-dnslib python3-pip
   sudo pip install --break-system-packages scikit-learn joblib      # ~5 min, ~280 MB
   python3 -c "import dnslib, sklearn, joblib; print('ok', sklearn.__version__)"
   ```
   On board #2 this gave scikit-learn **1.9.1**, joblib 1.6.0; 2.6 GB disk left.
   **Train your model with the same scikit-learn version as the board**, or train on the board.

## 4. Copy the code to the board

From the folder that contains `madhacks26` on your laptop:
```bash
adb push madhacks26 /home/arduino/
```
(or with SSH after step 9: `scp -r madhacks26 arduino@<board address>:/home/arduino/`)

Then on the board:
```bash
cd /home/arduino/madhacks26
chmod +x deploy/*.sh box/*.sh
chmod -R go-w /home/arduino/madhacks26     # the boot service runs this folder as root
python3 -m unittest discover tests         # expect: Ran 33 tests ... OK
```

## 5. Board settings (optional file)

Board #1 uses Pranshu's defaults: Wi-Fi **DigIn**, ID `02:12:34:56:78:9a`. Only add this so the board
prefers UWNet at boot:
```bash
sudo mkdir -p /etc/digin && echo 'UPLINK=UWNet' | sudo tee /etc/digin/board.env
```
(Board #2 has `SSID=DigIn2`, `AP_MAC=02:12:34:56:78:9b` here. Never give both boards the same SSID or MAC.)

## 6. Start everything (one command)

```bash
sudo /home/arduino/madhacks26/deploy/start_box.sh
```
Expect, in order: `Internet comes from: wlan0`, `Firewall rules in place`, `inet 10.42.0.1/24 ...`,
`DigIn is up`, then three green `active (running)` blocks (digin-dns, digin-dash, digin-detector),
and finally `Box up on DigIn. Dashboard: http://10.42.0.1`.

It also installs **digin-box.service**, so after this, every boot brings everything back by itself
(tested: back 15 s after boot, and after a sudden unplug).

Now the phone:
1. iPhone: **Mobile Data OFF**. Wi-Fi → **DigIn** (password = `PSK=` line in `box/hotspot.sh`).
2. Tap (i) next to DigIn → **Limit IP Address Tracking OFF**.
3. If the **UW login page** pops up, log in: that unlocks internet for the whole box.
4. Open any website. It loads = network done.
5. Check lookups are logged, on the board: `python3 /home/arduino/madhacks26/dns/digin_dns.py tail`
   (Ctrl-C to stop).

## 7. Dashboard

- From a phone/laptop **on DigIn**: `http://10.42.0.1` and `http://10.42.0.1/story`.
- The dashboard is **not reachable from UWNet** on purpose (it only listens on 10.42.0.1), so nobody at
  the venue can flip Shield.
- From your laptop on UWNet: use SSH (step 9) and run
  `deploy/demo_connect.sh <board's UWNet address>` → `http://localhost:8090`. It reconnects by itself.
  Run it for **one board at a time** (both use port 8090).

Using it: type the app name, **Start**, (force-close the game + airplane mode on/off on the phone so
lookups are fresh), play ~2 min, **End**. Shield button toggles blocking. The detector needs one
**Shield-OFF** round of a game first, then a **Shield-ON** round of the same game (same name).

## 8. Check it's healthy

```bash
systemctl is-active digin-dns digin-dash digin-detector digin-box   # 4x active
journalctl -u digin-dns -n 20 --no-pager                              # logger messages
journalctl -u digin-detector -n 5 --no-pager                          # "[detector] N flagged"
```

## 9. SSH from your laptop (recommended)

Board's UWNet address: `hostname -I` on the board (the `10.14x...` one).
```bash
ssh-keygen -t ed25519 -f ~/.ssh/digin_board1 -N ""
ssh-copy-id -i ~/.ssh/digin_board1.pub arduino@<board address>     # type the board password once
ssh -i ~/.ssh/digin_board1 arduino@<board address>
DIGIN_KEY=~/.ssh/digin_board1 ./deploy/demo_connect.sh <board address>
```
(Campus Wi-Fi blocks `projectWithAI.local` names, so use the address.)

## 10. Adding the ML model (your part)

The contract is docs/team-brief.md §5. What already exists and how to plug in:

- **Write to `ml_flags`** with your own `model_version` (e.g. `hgb-v1`), `status='suspected'`,
  `score` 0–1, `reasons` = JSON list of plain-English strings. `auto_blocked` only if score ≥ 0.97,
  never the app's own company, never allowlisted.
- **The rules-v1 detector also writes `ml_flags`.** `domain` is the primary key, so one row per
  domain. rules-v1 **never overwrites rows of another model_version** and keeps a person's
  Block/Dismiss choice. Please do the same (don't overwrite `rules-v1` rows), or stop the detector
  if you want your model alone: `sudo systemctl disable --now digin-detector`.
- Flags are matched to lookups by parent domain (flag `bidease.com` covers `ntusus.bidease.com`).
- **Dashboard shows your flags automatically**: Suspected panel, "caught by our detector" counter
  (per selected session), receipt "Caught by our detector", /story timeline (orange dots).
- **Shield** answers `0.0.0.0` for `ml_flags.status='auto_blocked'` within ~2 s (no restart).
- Run your scorer as a **loop that stays running** (importing scikit-learn takes ~5 s on the board).
  Copy `deploy/digin-detector.service` to `deploy/digin-score.service`, point `ExecStart` at
  `ml/score.py --loop 3`, and add `digin-score` to the unit list in `deploy/start_box.sh`.
- Speed on board #2: HistGradientBoosting trains 3000×12 in 1.5 s, scores 500 rows in 9 ms.
- Useful code: `ml/lists.py` (Samarth), `dashboard/company.py` (background apps, hosting names,
  name parts), `dns/detector.py` (session baselines, Shield-ON/OFF, plain-English reasons).
- **Data**: `data/sessions/` has real recorded sessions (laptop + board #2). Aarav has a newer full
  backup of board #2's `digin.db` (4,175 lookups, 12 sessions) if you need more.
- Known gap: this kernel has **no conntrack netlink**, so `raw_flows` needs a tcpdump-based
  `flows.py` (Pranshu). Until then `raw_flows` is empty.

Honest detector results so far (for comparing your model):

| Game (Shield OFF → ON) | rules-v1 flags | Verdict |
|---|---|---|
| Block Blast round 3 | bidease.com, startappservice.com, startappelb.com, ibl-video.site | 3 confirmed + 1 suspected; **missed Aarki** |
| Block Blast later round | abr.ge | confirmed (Airbridge) ; Aarki missed again |
| Knife Hit | 0 (first run: 2 false flags, Azure; fixed) | correct |
| Subway Surfers | 0 (first run: 1 false flag, moloco.local; fixed) | correct |

## 11. If something goes wrong

| Problem | Fix |
|---|---|
| Phone on DigIn but no internet | Log in to the UW page on the phone. Still broken: `sudo deploy/fallback_dnsmasq.sh` (Pranshu's plain mode, no logging). Back: `sudo deploy/start_box.sh` |
| `start_box.sh` says `Missing .../box/hotspot.sh` | The repo copy is incomplete; copy it again (step 4) |
| `Permission denied` running a script in `/run` | Old version; use the current `main` (it runs from `/var/lib/digin`) |
| DigIn doesn't appear after boot | Wait 2 min; else `sudo deploy/start_box.sh`; check `journalctl -u digin-box -b` |
| A service keeps restarting | `journalctl -u <name> -n 50 --no-pager` |
| Power off | `sudo poweroff`, then unplug within ~10 s (on USB power it starts itself again). A sudden unplug is also survivable (tested) |

Undo everything: `sudo systemctl disable --now digin-box digin-dns digin-dash digin-detector`,
`sudo rm /etc/NetworkManager/dnsmasq-shared.d/zz-aarav-dns-handoff.conf`, reboot.
