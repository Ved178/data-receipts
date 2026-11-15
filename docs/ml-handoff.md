# ML Handoff: Data Receipt tracker detection (MadHacks 2026)

You are building the ML layer of **Data Receipt**, a Wi-Fi box (Arduino Uno Q, Linux side) that shows which ad and tracking companies each device contacts and blocks them. The ML layer flags **trackers that are not on any blocklist**, judging a domain by how it behaves on the network, not by its name. Everything runs offline on the box.

Read this whole file before writing code. Build in the order of the milestones below. Keep every piece runnable on its own.

---

## 1. Context you need

- **Data path:** phone joins the box's Wi-Fi → every DNS lookup goes through our Python DNS server (`dnslib`), which logs the lookup and its full CNAME chain to SQLite → `tcpdump` catches direct-to-IP connections → the dashboard (small Python web server at `http://10.42.0.1`) shows the receipt.
- **Other owners (don't rewrite their parts, read from them):**
  - Networking and DNS server: Aarav + CE teammate
  - Names → companies, main database: Teammate 2
  - Dashboard and Tracker Web: Teammate 3
  - ML: Ved + ML teammate (this file)
- **Sessions:** a Start button on the box begins a session; in the demo, one session = one app on one device. Sessions are how we approximate "which app".
- **Hardware limits:** Uno Q Linux side is ARM, low RAM. Use scikit-learn only (no PyTorch). Models must load in < 2 s and score a batch of a few hundred domains in < 1 s.
- **Fully offline at runtime.** Tracker lists and the ASN database are downloaded ahead of time into `ml/data/`.

## 2. Goal and success criteria

1. **CNAME uncloaking** works on live traffic: `metrics.someapp.com → ...tracker.net` is reported as the tracker's company.
2. A trained model flags unknown domains as **suspected trackers**, each with a human-readable reason, e.g. `CNAME to an ad company + seen in 7 unrelated apps + fires at launch`.
3. **Held-out evaluation:** trained without 2–3 whole tracker companies, the model still catches their domains. Report precision and recall. Target precision ≥ 0.9 at the chosen threshold; recall is secondary.
4. **Live demo:** one real tracker is removed from our blocklist; the model flags it live from behavior alone.
5. Flags are written to `ml_flags`, which the dashboard's "Suspected trackers" panel reads.

## 3. Repo layout to create

```
ml/
  README.md              # how to run each step
  schema.sql             # the two tables below; the contract with other teams
  config.py              # paths, thresholds, held-out companies, demo tracker
  data/                  # downloaded lists + GeoLite2 (gitignored if large)
  lists.py               # load Disconnect + Tracker Radar → domain→company, category
  asn.py                 # GeoLite2 ASN lookup, ad/analytics ASN set
  uncloak.py             # CNAME chain → final target → company
  pcap_ingest.py         # bootstrap: PCAPdroid .pcap → raw_lookups rows (scapy)
  features.py            # raw logs → domain_features
  labels.py              # fill label + company
  train.py               # fit, held-out eval, save model
  score.py               # live loop: score new domains → ml_flags
  reasons.py             # turn feature values into reason strings
  tests/                 # small pytest tests for each module
```

## 4. Data contracts (freeze these first)

### Input: raw logs (owned by the DNS team; confirm exact names with Aarav)

Expect roughly this shape. If the real table differs, write an adapter in `features.py`; don't ask them to change it.

```sql
-- one row per DNS answer seen
raw_lookups(ts REAL, device TEXT, session_id TEXT, qname TEXT,
            cname_chain TEXT,   -- JSON list, in order, may be empty
            answer_ips TEXT)    -- JSON list
-- one row per tcpdump flow (stretch)
raw_flows(ts REAL, device TEXT, session_id TEXT, dst_ip TEXT,
          bytes_up INT, bytes_down INT)
sessions(session_id TEXT, device TEXT, app_name TEXT, start_ts REAL, end_ts REAL)
```

### Output 1: `domain_features` (one row per domain)

```sql
CREATE TABLE IF NOT EXISTS domain_features (
  domain            TEXT PRIMARY KEY,
  n_apps            INT,   -- distinct app sessions it appeared in
  n_lookups         INT,
  cname_depth       INT,   -- hops in its CNAME chain
  cname_to_tracker  INT,   -- 0/1: chain ends at a known tracker (LEAKAGE RULE applies)
  first_party_cname INT,   -- 0/1: subdomain of the app's own site that points to another org
  asn_ad_network    INT,   -- 0/1: an answer IP belongs to an ad/analytics ASN
  co_occur_rate     REAL,  -- share of its lookups within 2 s of a known-tracker lookup
  launch_burst      INT,   -- 0/1: first seen within 10 s of session start
  periodicity       REAL,  -- coefficient of variation of gaps between repeat lookups
  bytes_up          INT,   -- stretch, from raw_flows
  bytes_down        INT,   -- stretch
  label             INT,   -- 1 tracker, 0 not, NULL unknown
  company           TEXT,  -- owning company for labeled rows; used for grouped splits
  updated_ts        REAL
);
```

### Output 2: `ml_flags` (read by the dashboard)

```sql
CREATE TABLE IF NOT EXISTS ml_flags (
  domain        TEXT PRIMARY KEY,
  score         REAL,     -- model probability 0–1
  reasons       TEXT,     -- one line, plain English
  status        TEXT,     -- 'suspected' | 'auto_blocked' | 'approved' | 'dismissed'
  model_version TEXT,
  flagged_ts    REAL
);
```

The dashboard sets `approved` / `dismissed`. Never overwrite a row whose status is `approved` or `dismissed`.

## 5. Rules that must not be broken

1. **Leakage rule.** No feature may read the label list directly for the companies being tested. When computing `cname_to_tracker`, `co_occur_rate` ("known-tracker lookup"), and any list-derived signal for evaluation, use the tracker list **with the held-out companies removed**. Otherwise the model memorizes the list and the held-out test proves nothing. Add a test that fails if a held-out company's domain is used as "known" during eval feature building.
2. **Grouped split.** Split train/test by `company`, never by row. Domains from one company must never appear on both sides.
3. **Safety.** Default status is `suspected`. Set `auto_blocked` only if `score ≥ AUTO_BLOCK_THRESHOLD` (config, default 0.97) **and** the domain's company is not the first party of the current app (e.g. never block Meta domains during an Instagram session). Keep a hard allowlist in `config.py` (CDNs, OS services, captive-portal checks).
4. **Never** score or flag domains already on the blocklist; those are handled by the core feature. The ML layer is for unknowns.
5. **Privacy.** Store only domains, counts and timings. No payloads, no full URLs.

## 6. Labels

- **Positive (1):** domain or its registrable parent appears in Disconnect `services.json` (Advertising, Analytics, FingerprintingInvasive, FingerprintingGeneral, Social categories) or in Tracker Radar with an ad/analytics/tracking category.
- **Negative (0):** the app's own domains (from `sessions.app_name` mapping in `config.py`), CDNs, and known functional services (list them explicitly in `config.py`: e.g. Google Play services, Apple push, Cloudflare, Akamai, Fastly, Firebase Crashlytics is a judgment call, so pick one and document it).
- **Unknown (NULL):** everything else. Train only on labeled rows; score everything.
- Inspect the actual list files before writing parsers; don't assume field names. Tracker Radar has one JSON per domain under `domains/<region>/`; Disconnect nests `categories → [ {Company: {homepage: [domains]}} ]`.

## 7. Model

- Start with `HistGradientBoostingClassifier`; compare to `RandomForestClassifier`. Pick by precision at the target threshold on the held-out companies.
- Use `class_weight` or sample weights; positives will be the majority in some apps.
- Save with `joblib` to `ml/models/model_<version>.joblib` plus a JSON with feature list, threshold, train date, held-out companies, metrics.
- **Reasons** (`reasons.py`): rule-based from feature values, ordered by global feature importance. Examples:
  - `cname_to_tracker=1` → "CNAME points to <company>"
  - `n_apps ≥ 3` → "seen in N unrelated apps"
  - `launch_burst=1` → "fires at app launch"
  - `asn_ad_network=1` → "servers owned by <ASN org>"
  - `co_occur_rate ≥ 0.5` → "contacted alongside known trackers"
  - Join the top 3 that apply with " + ".

## 8. Evaluation (`train.py --eval`)

- Held-out companies come from `config.HELDOUT_COMPANIES` (pick 2–3 with many domains, e.g. from the Block Blast run: InMobi, Vungle).
- Print and save: precision, recall, F1, confusion matrix at the chosen threshold; PR curve points to CSV; per-held-out-company recall.
- Optional second test: train on an older list snapshot, test on domains added later.
- Output a short `ml/eval_report.md` with the numbers for the slides.

## 9. Live scoring (`score.py`)

- Loop every 5 s: rebuild features for domains seen in the last N minutes (incremental is fine), drop blocklisted and allowlisted ones, score, upsert into `ml_flags` per the safety rules.
- Must survive the DB being locked (retry with backoff) and the model file missing (log and sleep).
- CLI: `python -m ml.score --db <path> --once` for testing.

## 10. Milestones (build in this order)

1. **Setup:** `schema.sql`, `config.py`, `lists.py` loading both lists, `asn.py` loading GeoLite2. Test: `lists.company_for("doubleclick.net") == "Google"`.
2. **CNAME uncloaking** (`uncloak.py`): given a `cname_chain`, return final target, its company and whether it's first-party cloaking. This ships even if the model doesn't.
3. **Bootstrap ingest** (`pcap_ingest.py`): parse PCAPdroid `.pcap` files with scapy into `raw_lookups`, so work doesn't wait on the box. Map each pcap to a session by filename (`<app_name>.pcap`).
4. **Features** (`features.py`) from `raw_lookups` (+ `raw_flows` if present).
5. **Labels + training + held-out eval.** Pipeline must run end to end even on a tiny dataset.
6. **Live scoring** into `ml_flags`.
7. **Demo mode:** `config.DEMO_REMOVED_TRACKER` is excluded from the blocklist and from "known tracker" lists everywhere; a script `python -m ml.demo_check` confirms the model flags it from a recorded session.

**Cut line:** if milestone 5 isn't working, ship milestone 2 plus rule-based flags (`cname_to_tracker` or `asn_ad_network` → `suspected` with a reason) through the same `ml_flags` table, so the dashboard needs no changes.

## 11. Known limits (say these honestly in the demo)

- A few thousand labeled domains from 15–30 apps: enough for a demo, not production.
- Server-side tracking (app → its own server → ad company) is invisible to any network tool.
- Traffic is encrypted: we know *who* was contacted, never *what* was sent.
- Name features (random-looking strings, "track"/"pixel") are weak and easy to fake; don't rely on them.

## 12. Open questions to confirm with the team

- Exact `raw_lookups` / `sessions` table names and columns (Aarav).
- Where the main blocklist lives and its format (Teammate 2).
- How the dashboard reads `ml_flags` and writes approve/dismiss (Teammate 3).
- Python version on the Uno Q and whether `scikit-learn` installs there; if not, train on a laptop and export a model that loads on the box.
