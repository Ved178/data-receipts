# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

The `ml/` directory is currently empty (only the initial commit exists). The design below comes from `../ML_HANDOFF.md`, which is the full spec. Read it before writing code. Update this file as modules land, and replace the "planned" notes with real commands.

## What this is

ML layer of **Data Receipt** (MadHacks 2026): a Wi-Fi box (Arduino Uno Q, Linux/ARM, low RAM) that shows which ad and tracking companies each device contacts. This layer flags **unknown trackers** by network behavior rather than by name, and it also does CNAME uncloaking. Everything runs offline on the box.

Data flow: phone → box's Python `dnslib` DNS server → SQLite (`raw_lookups` with CNAME chains; `raw_flows` from tcpdump is a stretch goal) → `features.py` → `domain_features` → model → `ml_flags` → dashboard "Suspected trackers" panel.

Other teams own the DNS server, the names→companies DB, and the dashboard. Read from their tables. If the real `raw_lookups` or `sessions` shape differs from the spec, write an adapter in `features.py` and don't ask them to change it.

## Planned layout and commands

Modules planned under `ml/`: `schema.sql`, `config.py`, `lists.py`, `asn.py`, `uncloak.py`, `pcap_ingest.py`, `features.py`, `labels.py`, `train.py`, `score.py`, `reasons.py`, `demo_check.py`, `tests/`. Build in this order:

1. schema, config, lists, asn
2. uncloak (ships even if the model doesn't)
3. pcap_ingest (PCAPdroid `<app_name>.pcap` → `raw_lookups`, via scapy)
4. features
5. labels, train and held-out eval
6. live scoring
7. demo mode

Commands named in the spec (run as modules from the repo root):
- `python -m ml.score --db <path> --once` runs one scoring pass. The live loop runs every 5 s.
- `python -m ml.train --eval` trains, evaluates on held-out companies, and writes `ml/eval_report.md` plus PR-curve CSV.
- `python -m ml.demo_check` confirms that the model flags `config.DEMO_REMOVED_TRACKER` from a recorded session.
- Tests use pytest. Run one with `pytest ml/tests/test_<module>.py::test_name`.

Cut line: if training doesn't work, ship uncloaking plus rule-based flags (`cname_to_tracker` or `asn_ad_network` → `suspected` with a reason) through the same `ml_flags` table.

## Constraints

- scikit-learn and joblib only (no PyTorch). The model must load in under 2 s and score a few hundred domains in under 1 s. Compare `HistGradientBoostingClassifier` with `RandomForestClassifier` and pick by precision at the target threshold on held-out companies (target ≥ 0.9). Save to `ml/models/model_<version>.joblib` plus a JSON with the feature list, threshold, held-out companies and metrics.
- No network at runtime. Disconnect `services.json`, Tracker Radar and the GeoLite2 ASN DB live in `ml/data/`. Inspect the real files before writing parsers. Tracker Radar is one JSON per domain under `domains/<region>/`. Disconnect nests `categories → [{Company: {homepage: [domains]}}]`.
- Privacy: store only domains, counts and timings, never payloads or full URLs.

## Rules that must not be broken

1. **Leakage:** when building features for evaluation (`cname_to_tracker`, `co_occur_rate`, any list-derived signal), use the tracker list with `config.HELDOUT_COMPANIES` removed. `config.DEMO_REMOVED_TRACKER` is excluded from the blocklist and from "known tracker" lists everywhere. A test must fail if a held-out company's domain is used as "known" during eval feature building.
2. **Grouped split:** split train/test by `company`, never by row.
3. **Safety:** default status is `suspected`. Set `auto_blocked` only if `score ≥ AUTO_BLOCK_THRESHOLD` (default 0.97) **and** the domain's company is not the first party of the current app. Keep a hard allowlist in `config.py` (CDNs, OS services, captive-portal checks).
4. Never score or flag domains already on the blocklist. This layer handles unknowns only.
5. **`ml_flags` upserts** must never overwrite rows whose `status` is `approved` or `dismissed`, since the dashboard sets those.
6. `score.py` must survive a locked DB (retry with backoff) and a missing model file (log and sleep).

## Labels

Positive is a domain or its registrable parent in Disconnect (Advertising, Analytics, FingerprintingInvasive, FingerprintingGeneral, Social) or Tracker Radar ad/analytics/tracking categories. Negative is the app's own domains (mapped from `sessions.app_name` in `config.py`) plus explicit CDN and functional-service lists. NULL is everything else. Train only on labeled rows and score everything.

## Data contracts

`schema.sql` defines the contract with other teams and holds `domain_features` and `ml_flags`. The exact columns are in `../ML_HANDOFF.md` §4. Freeze them first. Input tables are `raw_lookups`, `raw_flows` and `sessions`, and their names are still to be confirmed with the DNS team.
