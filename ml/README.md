# ml/ part 1 — lists, uncloaking, ASN, demo mode (Samarth)

Everything is offline (data in `data/lists/`), stdlib only.

## For Aarav (Shield + dashboard)
```python
from ml import lists
L = lists.load_live(conn)          # call every ≤2 s; cached, applies settings.demo_holdout
L.is_tracker(qname)                # block with 0.0.0.0 (unless L.same_company(qname, app))
L.cname_tracker([qname, *cname_chain])   # CNAME-to-tracker -> block_reason='cname'
L.is_allowlisted(qname)            # never block
L.company(qname)                   # (company, category) for the receipt, or None
```

## For Ved (features / train / score)
- Labels: `L.is_tracker(d)` with `L = lists.load(exclude_companies=...)`. Never read lists directly.
- Negatives: `data/lists/content_negatives.txt` (hand-confirmed). Unlabeled ≠ negative.
- Features: `cname.uncloak(q, chain, L, flagged)`, `cname.ad_network_ips(ips, L)`, `L.ip_owner(ip)`.
- Score only domains where `not L.is_known(d)`.
- For the held-out eval, expose in `ml/train.py`:
  `fit_predict(conn, L, domains) -> {domain: score}` (train using labels from `L` only).

## Commands
```bash
python3 -m ml.cname --db /home/arduino/digin/digin.db --loop 3     # cut-line rule flags -> ml_flags
python3 -m ml.demo --db ... set InMobi | status | watch | clear    # demo mode
python3 -m ml_eval.import_pcapdroid data/pcapdroid/*.csv --out data/sessions/pcapdroid.db
python3 -m ml_eval.evaluate --db data/sessions/pcapdroid.db --holdout InMobi --holdout Vungle --folds
python3 -m ml.asn ip 103.251.108.10                                  # which source + owner
python3 -m ml.asn build ip2asn-v4.tsv.gz                             # rebuild TSV fallback
python3 -m unittest discover tests -v
```

Rule flags use `model_version='rules-v1'`, score ≤ 0.95 (never auto-block), and never
overwrite a model row or a dismissed/allowlisted one.

## Data sources
| File | From |
|---|---|
| `services.json` | Disconnect tracking-protection list |
| `android-tds.json` | DuckDuckGo Tracker Radar (Android build): trackers, owners, 788 known CNAME cloaks |
| `ml/data/GeoLite2-ASN.mmdb` | Ved's GeoLite2 download (gitignored: copy onto the box). Preferred when present; IPv4 + IPv6, read by stdlib `ml/mmdb.py` |
| `ad_asn_ranges.tsv` | iptoasn.com ip2asn-v4, filtered to 20 ad/analytics ASNs (`ml/asn.py: AD_ASNS`) |
| `apps.json` | app → own company/domains (**best guesses, confirm from captures**) |

Rule-flag service: `deploy/digin-rules.service` (install steps at the top of the file).

Known gaps: without the GeoLite2 file, IPv6 answers get no ASN owner; Vungle/Mintegral/Unity have no own ASN in
the data (they sit on clouds), so for them only CNAME + behavior can help.
