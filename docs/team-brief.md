# Data Receipt — Master Brief for Claude Code

This is the handoff for Claude Code. It matches the team's current plan ("Data Receipt — Team
Rundown", Sat 6 PM) — that rundown is the source of truth for **what** we build; this file adds
the **how**: schemas, rules, fixes and per-owner tasks. If the two disagree, ask the team in
chat before changing anything shared.

Read this whole file before writing code. Four people work in one GitHub repo in parallel.
Everything meets in one SQLite file, `digin.db`. Its schema (§5) is frozen: change it only after
posting in the team group.

---

## 1. What we're building (plain words)

**Data Receipt** is a Wi-Fi box that catches the trackers blocklists miss. Phones join our Wi-Fi
(**DigIn**) on an **Arduino Uno Q** and use the internet normally. The box logs every DNS lookup,
blocks known trackers with **Shield**, and runs an **on-device ML model** that flags new and
disguised trackers that aren't on any blocklist, judging each domain by how it behaves on the
network, not by its name. Every flag comes with a plain-English reason.

**Pitch:** "Every ad blocker is only as good as its list, and trackers dodge lists by renaming
themselves or hiding behind a website's own name. Data Receipt catches them anyway, on the box,
with no cloud."

**The one moment the demo exists for:** a real tracker we deliberately removed from every list
shows up in "Suspected trackers", flagged live by the model with its reason.

## 2. Context and constraints

| Item | Value |
|---|---|
| Event | MadHacks 2026, UW–Madison, theme "Let's Dig In" |
| Sponsor track | **Decide by 8 PM Sat**: Qualcomm/N+1 (AI app running entirely on the Uno Q) or TDS Privacy & Security. Build so both work: model inference must run on the box with no cloud. |
| Code freeze | **Sun 9:00 AM CT** |
| Submit | **Sun 11:00 AM CT** (hard deadline 12:00 PM) on Devpost |
| Judging | Sun 1–3 PM, science fair, ~2 min per judge |
| Rule | All code written at the event; packages OK |
| Safety | Only devices we own or judges who opt in. Never touch the venue network. Store only lookups/flow sizes per session; wipe on reset. |

## 3. Already proven (don't redo)

- Block Blast contacted **130 domains in ~2 min**, mostly ad companies.
- **Shield on the Uno Q** blocked most Block Blast ads and the game kept working.
- PCAPdroid capture (Subway Surfers): 12 ad/tracking companies in < 20 s. Disconnect's list
  matched only **36%** of that game's domains; with our mobile ad-network supplement
  (`EXTRA` in `tools/receipt_test.py`) **98%**. Lists miss mobile ad networks → the gap we fill.
- `digin_dns.py` (DNS logger + Shield in Python) written and tested on a laptop; not yet on the Uno Q.

**Known environment facts**
- Uno Q: Qualcomm QRB2210 (quad Cortex-A53, Debian) + STM32U585 MCU; Linux ↔ sketch via App Lab
  **Bridge**; 8×13 LED matrix; Wi-Fi 5; BT 5.1. Box internet = Android phone via **USB tethering**
  into the hub. Hotspot via NetworkManager (`10.42.0.0/24`, box = `10.42.0.1`).
- Test phones: **mobile data OFF** (else Android silently uses cellular). Private DNS on
  "Automatic" falls back to our DNS; a manually set Private DNS host bypasses us.
- iPhone Private Relay: answer NXDOMAIN for `mask.icloud.com` and `mask-h2.icloud.com`.
  Firefox canary: NXDOMAIN for `use-application-dns.net`.
- Encrypted DNS: `REJECT` (not DROP) TCP/UDP 853 and port 443 to 1.1.1.1, 1.0.0.1, 8.8.8.8,
  8.8.4.4, 9.9.9.9 so devices fall back fast.
- Phones cache DNS: most lookups happen at app launch. Before every demo/recording: force-close
  the app and toggle airplane mode.

## 4. Architecture

```
phone ──Wi-Fi "DigIn"──► Uno Q ──USB tether──► internet
                           │
  digin_dns.py (Python DNS server: log + Shield) ──► raw_lookups
  flows.py (conntrack/tcpdump byte counts)        ──► raw_flows        all in digin.db
  Start/Shield buttons, CLI                       ──► sessions, settings
  ml/score.py (every few s)  reads lookups+flows  ──► ml_flags
  dashboard (http://10.42.0.1) reads everything; STM32 reacts to hits
```

## 5. The `digin.db` contract (frozen)

Path: `/home/arduino/digin/digin.db`. SQLite, `PRAGMA journal_mode=WAL;` set by whoever creates
it; every writer uses short transactions and `busy_timeout=3000`. Create with `db/schema.sql`.
Names below follow the team rundown; types and the few extra columns are added for precision.

```sql
CREATE TABLE IF NOT EXISTS raw_lookups (
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, device TEXT NOT NULL, session_id INTEGER,
  qname TEXT NOT NULL, qtype TEXT, cname_chain TEXT,   -- JSON array of names
  answer_ips TEXT,                                     -- JSON array
  blocked INTEGER NOT NULL DEFAULT 0, block_reason TEXT -- 'list'|'cname'|'ml'|NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  session_id INTEGER PRIMARY KEY, device TEXT, app_name TEXT NOT NULL,
  start_ts REAL NOT NULL, end_ts REAL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
  -- keys: 'shield' = 'on'|'off'; 'demo_mode' = 'on'|'off'; 'demo_holdout' = company name
CREATE TABLE IF NOT EXISTS ml_flags (
  domain TEXT PRIMARY KEY, score REAL NOT NULL, reasons TEXT NOT NULL, -- JSON array of strings
  status TEXT NOT NULL,          -- 'suspected'|'auto_blocked'|'dismissed'|'allowlisted'
  model_version TEXT NOT NULL, flagged_ts REAL NOT NULL, updated_ts REAL
);
CREATE TABLE IF NOT EXISTS raw_flows (     -- promoted from stretch to core (see §6.2)
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, device TEXT NOT NULL, session_id INTEGER,
  dst_ip TEXT NOT NULL, dst_port INTEGER, proto TEXT,
  bytes_up INTEGER, bytes_down INTEGER, pkts_up INTEGER, pkts_down INTEGER, duration_s REAL
);
CREATE INDEX IF NOT EXISTS ix_lookups_ts ON raw_lookups(ts);
CREATE INDEX IF NOT EXISTS ix_lookups_qname ON raw_lookups(qname);
CREATE INDEX IF NOT EXISTS ix_flows_ts ON raw_flows(ts);
```

Shared helper: **`lists.py`** (domain → company, category; known-tracker check). Used by
`digin_dns.py` (Shield), the ML features, and the dashboard receipt. One lookup, used three times.

## 6. Fixes from review (apply these; they change correctness, not scope)

### 6.1 No leakage in held-out evaluation or demo mode
"Ad-network servers" (IP → network owner → ad company) and "CNAME to an ad company" are lists in
disguise. When a company is held out (eval) or removed (demo mode), remove it from **all** of:
training labels, the tracker blocklist, the ad-company network-owner list, and the CNAME target
list. Implement once as `lists.py: load(exclude_companies=[...])` and use it everywhere.
`settings.demo_holdout` drives this live.

### 6.2 Behavior needs flows, not just DNS
Repeat DNS lookups follow cache expiry (TTL), so "regular pinging" from DNS alone measures the
server's TTL, not app behavior. `raw_flows` is core: per-connection bytes up/down, packets,
duration, via `conntrack` (`sysctl -w net.netfilter.nf_conntrack_acct=1`;
`conntrack -E -e DESTROY -o timestamp,extended`) or a tcpdump summarizer. Map `dst_ip` → domain
from recent `raw_lookups.answer_ips` for the same device. If flows aren't ready by midnight, drop
the DNS-only "regular pinging" clue rather than ship a misleading one.

### 6.3 `digin_dns.py` must never take the network down
It's a single point of failure. Required: per-query upstream timeout (≤ 2 s) with a second
upstream; concurrent handling (threads or asyncio); DB writes off the answer path (queue + writer
thread) so a locked DB never delays an answer; systemd `Restart=always`, `RestartSec=1`;
`deploy/fallback_dnsmasq.sh` switches to plain forwarding in one command.

### 6.4 Honest numbers
With 2–3 held-out companies, report **counts** next to percentages ("caught 11 of 14 held-out
domains; 1 false flag among 50 content domains"). Log every eval run in `RESULTS.md`.

## 7. Owners and tasks

Parts only talk through `digin.db`, so nobody waits on anybody's code. Edit outside your folders
only by PR the owner reviews.

### Pranshu — box internet, hotspot, STM32, enclosure  (`box/`, `sketches/`, `bridge/`)
1. USB tethering from the Android phone into the hub; DigIn hotspot (Plan A). Plan B if the Uno Q
   can't host and stay online: shared phone hotspot + test phone's DNS set statically to the box.
2. Firewall rules from §3 (encrypted-DNS REJECTs) in `box/firewall.sh`, idempotent.
3. STM32 sketch + `bridge/feedback.py`: on each new tracker hit (poll `raw_lookups` where the domain
   is a known tracker or in `ml_flags`): click, red LED flash, LED-matrix counter; < 0.5 s.
   **Start** button → new `sessions` row (app name from the dashboard's last selection);
   **Shield** button → toggle `settings.shield`. Fail silently when Bridge is absent (laptop).
4. Enclosure; label the buttons.
**Done:** phone on DigIn has internet; a tracker hit clicks and counts; buttons work.

### Aarav — `digin_dns.py`, sessions, blocklist, integration, dashboard  (`dns/`, `dashboard/`, `deploy/`)
1. Run `digin_dns.py` on the Uno Q writing real `raw_lookups` rows (with CNAME chain and answer
   IPs); apply §6.3 hardening; Private Relay/Firefox canaries answered NXDOMAIN.
2. Shield: answer `0.0.0.0` for list trackers, CNAME-to-tracker, and `ml_flags.status='auto_blocked'`;
   never block the current session's own company (from `lists.py`) or the allowlist (CDNs,
   phone system services). Re-read `settings` and `ml_flags` every ≤ 2 s.
3. `digin_dns.py session start "<app>"` / `end`.
4. Dashboard at `http://10.42.0.1` (no CDN assets, works offline): live receipt grouped by company
   and category, **Tracker Web** (which companies appear across which apps), **Suspected trackers**
   panel (from `ml_flags`, with reasons and score), Shield toggle, session picker.
5. Integration: `deploy/start_box.sh` brings up everything; systemd units; `/health` (DNS alive,
   last row age, DB size, CPU temp).
**Done:** dashboard shows a correct receipt from real rows; Shield works from the button and the page.

### Samarth — ML (part 1): lists, uncloaking, evaluation, demo mode  (`ml/lists.py`, `ml/cname.py`, `ml/asn.py`, `ml_eval/`, `ml/demo.py`)
Kept separate from the model author so the model isn't graded by the person who built it.
1. `lists.py`: Disconnect `services.json` + DuckDuckGo Tracker Radar (if downloadable) + our
   `EXTRA` mobile ad list → domain → (company, category); `exclude_companies` (§6.1); allowlist.
2. `cname.py`: CNAME uncloaking — flag names whose chain ends at a known (or model-flagged) tracker.
3. `asn.py`: IP → network owner → ad/analytics company. GeoLite2-ASN needs a free MaxMind account
   and license key; alternative: an offline IP-to-ASN file (e.g. iptoasn.com). Download while online.
4. `ml_eval/`: **grouped held-out evaluation** — hold out whole companies (e.g. InMobi, Vungle),
   with §6.1 exclusions, report precision (target ≥ 0.9), recall, and counts; compare to a
   list-only and rules-only baseline. Writes `RESULTS.md` + a slide-ready table.
5. `demo.py`: demo mode — set `settings.demo_holdout`, exclude that company everywhere, so the
   model must catch it live.
6. **Cut line owner:** if the model isn't ready by midnight, ship CNAME uncloaking + rule flags
   (CNAME to tracker, ad-network servers) into `ml_flags` with reasons; dashboard unchanged.

### Ved — ML (part 2): features, model, live scorer  (`ml/features.py`, `ml/train.py`, `ml/score.py`)
1. `features.py`, per domain (and per domain × session where useful):
   - CNAME to known/flagged tracker (from `cname.py`)
   - number of **unrelated app sessions** it appears in
   - **fires at launch**: first contact within 10 s of session start
   - answer IPs owned by an ad/analytics network (from `asn.py`)
   - **travels with known trackers**: looked up within 2 s of a known tracker
   - from `raw_flows`: bytes up/down ratio, mean flow size and its consistency, connection
     interval regularity (real beaconing, §6.2)
   - weak name clues (entropy, digits, "track/pixel/ads"): low weight, easy to fake
2. `train.py`: labels from `lists.py` (excluding held-out companies); unlabeled ≠ negative —
   negatives are hand-confirmed content domains (app APIs, CDNs). scikit-learn
   `HistGradientBoostingClassifier` or `RandomForestClassifier`; save with `joblib` + a
   `model_version`. Data now: PCAPdroid CSVs, Shield-test blocklist, Block Blast dnsmasq log;
   then real `digin.db` sessions.
3. `score.py`: every ~3 s, score domains not on any list; write `ml_flags` with plain-English
   reasons from top feature contributions (e.g. "CNAME to an ad company + seen in 7 unrelated apps
   + fires at launch"). Status `suspected`; `auto_blocked` only if score ≥ 0.97 and not the
   session's own company and not allowlisted. Must run on the Uno Q in < 1 s per cycle.
**Done:** `ml_flags` fills with reasoned flags on the box; Samarth's eval numbers ready for slides.

## 8. Milestones (Sat → Sun, CT)

| Time | Milestone | Owner(s) |
|---|---|---|
| 6:30 PM | `digin_dns.py` on the Uno Q (Plan B OK), real rows in `raw_lookups` | Aarav |
| 7:30 PM | Box internet + DigIn hotspot (Plan A) | Pranshu |
| 8:00 PM | **Sponsor track decided** | Team |
| 9:00 PM | Full loop: phone → `digin.db` → simple receipt page; CNAME uncloaking works; `raw_flows` writing | All |
| Midnight | Model trained + held-out numbers. **Else ship the cut line.** | Ved, Samarth |
| 3:00 AM | Demo mode catches the removed tracker live; LED counter + buttons; Suspected panel | All |
| 3–7 AM | Sleep in shifts; box keeps collecting data | — |
| 9:00 AM | Feature freeze; rehearse; backup video | All |
| 11:00 AM | Devpost submitted | Aarav |

Check-ins every 2 hours (Aarav runs them): each owner shows their piece working **through
`digin.db` on the box**, not just on a laptop.

## 9. Repo layout

```
CLAUDE.md  README.md  RESULTS.md
db/schema.sql                       (Aarav creates; frozen per §5)
dns/digin_dns.py                    Aarav
dashboard/                          Aarav
deploy/start_box.sh, *.service, fallback_dnsmasq.sh   Aarav
box/hotspot.sh, firewall.sh, flows.py                  Pranshu (flows.py writes raw_flows)
sketches/feedback/, bridge/feedback.py                 Pranshu
ml/lists.py, cname.py, asn.py, demo.py                 Samarth
ml_eval/                                               Samarth
ml/features.py, train.py, score.py, models/            Ved
tools/receipt_test.py               (existing scorer + EXTRA mobile ad list)
data/pcapdroid/, data/sessions/, data/lists/           shared
tests/                              each owner adds tests for their part
```

## 10. Demo (~2 min) and judge answers

1. "Every ad blocker is only as good as its list." Judge's phone joins DigIn.
2. Press Start, play Block Blast: the box clicks like a Geiger counter; the receipt fills.
3. Press Shield: known trackers blocked, game keeps working.
4. **The moment:** the tracker removed from every list (demo mode) appears in Suspected trackers
   with its reason.
5. Held-out numbers: "Trained without InMobi and Vungle, it still caught X of Y of their domains
   at Z precision."
6. (Qualcomm track) Unplug the upstream for a few seconds: scoring continues on the box.

Before the demo: force-close the game and toggle airplane mode so lookups are fresh.

| Judge question | Answer |
|---|---|
| Different from Pi-hole / NextDNS / VPN? | They block from lists; a new or disguised tracker gets through until someone adds it. We catch it from behavior, on the box. |
| Isn't this App Privacy Report? | That lists domains on one iPhone and can't block. We cover every device and find trackers no list names. |
| What's hard? | On-device classifier using CNAME chains, network ownership and timing/flow behavior, with grouped held-out evaluation; live DNS pipeline + Shield. |
| Where's the AI? | The tracker classifier, trained by us, running on the Uno Q. |
| What data was sent? | Encrypted; we never read contents. We show who was contacted and judge behavior. |
| Won't it block by mistake? | Flags start as suspected; auto-block only at ≥ 0.97, never the app's own company; CDNs allowlisted. |
| (Expect) Did held-out companies leak through your lists? | No: held-out companies are removed from labels, blocklist, network-owner list and CNAME targets (§6.1). |

**Limits we say out loud:** we see who, never what; trained on a few thousand domains from 15–30
apps (demo-grade); server-side tracking is invisible to any network tool; name clues are easy to
fake, so we lean on behavior and CNAME chains; YouTube ads can't be blocked by DNS.

## 11. How to work

- Schema first: `db/schema.sql` and a tiny seed DB with example rows go in before other code.
- Small PRs into `main`; `main` always runs `deploy/start_box.sh`. Tests before merge.
- Offline at demo time: no CDNs; download lists, ASN data and Python wheels on the box while online.
- Prefer scikit-learn/NumPy/stdlib; ask before heavy dependencies. The Uno Q is a modest ARM CPU.
- Report failures plainly with numbers. Never fake a demo result.
