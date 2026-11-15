# Data Receipt

**A Wi-Fi box that catches the trackers ad blockers miss.** Built at MadHacks 2026 on an Arduino Uno Q.

Every ad blocker is only as good as its list, and trackers dodge lists by renaming themselves or
hiding behind a website's own name. Data Receipt sits between your phone and the internet, shows
a live "receipt" of every company each app talks to, blocks known trackers, and runs an
**on-device ML model** that flags new or disguised trackers by how they behave on the network,
each with a plain-English reason. No cloud: everything runs on the box.

## What it does

- **Receipt.** Join the box's Wi-Fi (**DigIn**), use any app, and the dashboard lists every
  company it contacted, grouped by category (ads, analytics, fingerprinting, content).
- **Shield.** One tap blocks known trackers at the DNS level for every device on the network.
  The app keeps working; the ads don't load.
- **Suspected trackers.** A model trained on our own captures scores every domain that no
  blocklist knows and explains why: *"CNAME points to a known tracker; seen in 7 unrelated apps;
  fires at app launch"*. One tap blocks it.
- **Tracker Web.** Which ad companies show up across which apps.

**Result:** trained with InMobi and Vungle removed from every list, the model still caught
**18 of their 29 domains, with 1 false flag among 61 content domains** (threshold 0.8). Full,
honest numbers and their caveats: [`docs/model.md`](docs/model.md).

## How it works

```
phone ──Wi-Fi "DigIn" (10.42.0.1)──► Arduino Uno Q ──uplink Wi-Fi / USB tether──► internet
                                          │
   dns/digin_dns.py    DNS server: logs every lookup, Shield answers 0.0.0.0 for trackers
   box/flows.py        per-connection byte counts (tcpdump)         ┐
   ml/score.py         ML model ml-v1, scores unlisted domains      ├─ all share one SQLite
   dns/detector.py     rules detector (Shield-off vs Shield-on)     │  file: digin.db
   dashboard/app.py    live dashboard at http://10.42.0.1           ┘
```

The model is gradient boosting (scikit-learn) on behavior features, not names: CNAME chains to
ad companies, how many unrelated apps contact a domain, whether it fires at app launch, whether
it travels with known trackers, server network owner (ASN), bytes up vs down, and timing.
It never scores domains already on a list, login hosts or call servers, and it never
auto-blocks: flags start as *suspected* and a person decides.

## Run it

**On the Uno Q** (full guide: [`deploy/SETUP_BOARD.md`](deploy/SETUP_BOARD.md)):

```bash
sudo apt-get install -y --no-install-recommends iw conntrack tcpdump python3-dnslib python3-pip
sudo pip install --break-system-packages -r requirements.txt
sudo deploy/start_box.sh          # hotspot, DNS, dashboard, both detectors; also at every boot
```

Then on a phone: mobile data off, join **DigIn** (password `digin1234`), open
**http://10.42.0.1**, type the app name, press **Start**, use the app, press **End**.
Health: `http://10.42.0.1/health`. Undo: `sudo deploy/fallback_dnsmasq.sh`.

**On a laptop** (no hotspot; uses a recorded database):

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp data/sessions/all.db /tmp/demo.db
.venv/bin/python -m ml.score --db /tmp/demo.db --once          # model writes its flags
.venv/bin/python dashboard/app.py --host 127.0.0.1 --port 8080 --db /tmp/demo.db
# open http://127.0.0.1:8080
.venv/bin/python -m unittest discover tests                     # 47 tests
```

**Demo mode** removes one real tracker company from every list, so the model has to catch it
live: `python3 -m ml.demo set InMobi`, then `status`, then `clear`.

## Repo layout

| Folder | What's in it |
|---|---|
| `dns/` | DNS logger + Shield, rules detector |
| `dashboard/` | web dashboard (no external assets, works offline) |
| `ml/` | tracker lists, CNAME uncloaking, ASN lookup, features, model, live scorer, demo mode |
| `ml_eval/` | held-out evaluation by company, importers for PCAPdroid and box captures |
| `box/` | hotspot, firewall, flow capture |
| `deploy/` | systemd services, one-command start, board setup guide |
| `db/` | the shared `digin.db` schema |
| `data/` | tracker lists, recorded sessions and captures used for training |
| `docs/` | model details and numbers, original team brief |

## Limits

We see who an app talks to, never what it sends (traffic stays encrypted). Server-side tracking
is invisible to any network tool. The model is trained on a few thousand domains from about 30
apps, so it is demo-grade, and name clues are easy to fake, which is why it leans on behavior
and CNAME chains.

## Team

**Aarav** (DNS, Shield, dashboard, integration) · **Pranshu** (box, hotspot, flow capture) ·
**Samarth** (lists, CNAME uncloaking, evaluation, demo mode) · **Ved** (features, model, live scorer)
