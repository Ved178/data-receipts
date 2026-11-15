"""Per-domain behavior features from digin.db (raw_lookups, sessions, raw_flows).

    from ml import features, lists
    L = lists.load(exclude_companies=[...])          # held-out companies already removed
    F = features.build(conn, L)                      # {domain: {feature: value}}
    X = features.matrix(F, domains)                  # numpy, columns = features.FEATURES

Leakage rules (docs/team-brief.md §6.1):
  * every list-derived signal (CNAME target, ad-network owner, "known tracker" lookups)
    goes through `L`, so held-out / demo-removed companies are NOT known here;
  * a domain's own list status is never a feature, and neither is `blocked`: Shield answers
    0.0.0.0 for list trackers, so "blocked" is the label in disguise. Blocked lookups have
    no CNAME chain or IPs, so for best results capture with Shield OFF.

Missing values are NaN (HistGradientBoosting handles them); flow features are NaN when
there is no raw_flows data for the domain. n_lookups is stored but not a model feature
(retries after a block inflate it).
"""
import bisect
import json
import math
import os
import re
import statistics
from collections import defaultdict

from ml import cname as cname_mod
from ml.lists import norm as lists_norm

NAN = float("nan")

BASE_FEATURES = [
    "n_apps",             # distinct apps (by session app_name) it appeared in
    "cname_depth",        # max CNAME hops seen
    "cname_to_tracker",   # 0/1 chain passes through a known tracker (via L)
    "first_party_cname",  # 0/1 app's own subdomain that CNAMEs to another org
    "asn_ad_network",     # 0/1 an answer IP is on an ad/analytics network (via L)
    "co_occur_lift",      # co_occur_rate minus its session's baseline (ad-heavy apps make everything co-occur)
    "launch_burst",       # 0/1 first seen within 10 s of a session start
    "periodicity",        # CV of gaps between repeat lookups (NaN if < 3 lookups)
    "flow_bytes_up_ratio",    # flows: up / (up + down)
    "flow_interval_cv",       # flows: regularity of connection starts
    "name_ad_tokens",     # 0-3 tracker-ish words in the hostname (ads, telemetry, sdk...); easy to fake
    "ip_generic_infra",   # 0/1 an answer IP is in a generic CDN/cloud network (NaN: no IPs/ASN db)
    "own_app_domain",     # 0/1 belongs to the company/name of an app it was seen in (content)
]
FLOW_SHAPE = [
    "flow_mean_up",       # log1p mean bytes up per connection (beacons are tiny)
    "flow_mean_dur",      # mean connection duration, seconds (beacons are short)
    "flows_per_lookup",   # connections opened per lookup
    "flow_pkt_size",      # mean bytes per packet
    "flow_port443",       # share of connections on port 443
]
NAME_SHAPE = [
    "name_n_labels", "name_len", "name_digit_ratio", "name_first_label_len", "name_hex_label",
]
LAUNCH_ALT = ["launch_not_own"]  # launch_burst and not the app's own domain (opt-in, for ablation)
TIMING = [
    "first_contact_delay",  # log1p seconds from session start to first contact
    "burst_size",           # other lookups within 2 s of its first lookup
    "lookups_per_min",      # lookups per minute of session time
    "idle_refire",          # 0/1 contacted again after >30 s of silence
]
FEATURE_GROUPS = {"flow_shape": FLOW_SHAPE, "name_shape": NAME_SHAPE, "timing": TIMING}
# Deployed list = base + timing. tools/ablate.py showed timing raised recall (0.53 -> 0.65 at 0.8)
# at equal precision; flow_shape and name_shape lowered precision on our small data, so they stay opt-in.
FEATURES = BASE_FEATURES + TIMING
ALL_COLUMNS = BASE_FEATURES + FLOW_SHAPE + NAME_SHAPE + TIMING
EXTRA_COLUMNS = ["n_lookups"]  # kept for reports, not fed to the model

NAME_TOKENS = {"ad", "ads", "adserver", "adx", "adsystem", "track", "tracking", "tracker", "telemetry",
               "analytics", "metrics", "events", "event", "beacon", "pixel", "sdk", "ssp", "rtb", "bid",
               "stats", "logs", "logging", "crash", "crashlytics", "collect", "tag", "tags", "attribution"}
INFRA_ORGS = ("akamai", "amazon", "cloudflare", "fastly", "microsoft", "apple", "google", "edgecast",
              "edgio", "limelight", "azure")
ASN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "GeoLite2-ASN.mmdb")
_asn = [None, False]  # [reader, tried]


def _asn_reader():
    if not _asn[1]:
        _asn[1] = True
        try:
            import maxminddb
            _asn[0] = maxminddb.open_database(ASN_PATH)
        except Exception:  # library or file missing: feature stays NaN
            _asn[0] = None
    return _asn[0]


def _infra_ip(ips):
    r = _asn_reader()
    if r is None or not ips:
        return NAN
    for ip in ips:
        try:
            org = ((r.get(ip) or {}).get("autonomous_system_organization") or "").lower()
        except Exception:
            continue
        if any(o in org for o in INFRA_ORGS):
            return 1
    return 0


def _name_shape(dom):
    labels = (dom or "").split(".")
    chars = re.sub(r"[^a-z0-9]", "", dom or "")
    digits = sum(c.isdigit() for c in chars)
    hexlab = any(len(l) >= 8 and re.fullmatch(r"[0-9a-f-]+", l) and any(c.isdigit() for c in l)
                 for l in labels)
    return {"name_n_labels": len(labels), "name_len": len(dom or ""),
            "name_digit_ratio": digits / len(chars) if chars else 0.0,
            "name_first_label_len": len(labels[0]), "name_hex_label": int(hexlab)}


def _name_tokens(name):
    labels = re.split(r"[^a-z]+", (name or "").lower())
    return min(3, len({t for t in labels if t in NAME_TOKENS}))


GENERIC_APP_WORDS = {"game", "games", "app", "simulator", "racing", "shopping", "airlines", "music",
                     "unknown", "video", "free", "play"}
CO_WINDOW = 2.0
LAUNCH_WINDOW = 10.0
_SLD = {"co", "com", "org", "net", "gov", "ac", "edu"}


def _loads(s):
    try:
        v = json.loads(s) if s else []
    except (ValueError, TypeError):
        return []
    return v if isinstance(v, list) else []


def registrable(name):
    p = (name or "").lower().strip(".").split(".")
    if len(p) >= 3 and p[-2] in _SLD and len(p[-1]) == 2:
        return ".".join(p[-3:])
    return ".".join(p[-2:])


def _cv(vals):
    if len(vals) < 2:
        return NAN
    m = statistics.fmean(vals)
    return statistics.pstdev(vals) / m if m > 0 else NAN


def build(conn, L, since_ts=None):
    """{domain: {feature: value, 'n_lookups': int}} for every queried name."""
    sess = {sid: (app, start) for sid, app, start in conn.execute(
        "SELECT session_id, app_name, start_ts FROM sessions")}
    q = "SELECT ts, device, session_id, qname, cname_chain, answer_ips FROM raw_lookups"
    args = ()
    if since_ts is not None:
        q += " WHERE ts >= ?"
        args = (since_ts,)
    rows = conn.execute(q + " ORDER BY ts", args).fetchall()

    tracker_ts = sorted(ts for ts, _d, _s, name, _c, _i in rows
                        if name and L.is_tracker(name.lower().strip(".")))
    by = defaultdict(list)
    for r in rows:
        name = (r[3] or "").lower().strip(".")
        if name:
            by[name].append(r)

    all_ts = sorted(r[0] for r in rows)
    span, ends = {}, {sid: end for sid, end in conn.execute("SELECT session_id, end_ts FROM sessions")}
    for r in rows:
        if r[2] is not None:
            a = span.setdefault(r[2], [r[0], r[0]])
            a[1] = max(a[1], r[0])
    # co-occurrence flag per lookup and per-session baseline over non-tracker lookups
    cof = {}
    base_n, base_c = defaultdict(int), defaultdict(int)
    for r in rows:
        nm = (r[3] or "").lower().strip(".")
        i = bisect.bisect_left(tracker_ts, r[0] - CO_WINDOW)
        j = bisect.bisect_right(tracker_ts, r[0] + CO_WINDOW)
        trk = bool(nm) and L.is_tracker(nm)
        cof[id(r)] = (j - i - (1 if trk and j > i else 0)) > 0
        if not trk:
            base_n[r[2]] += 1
            base_c[r[2]] += cof[id(r)]
    ip_dom = defaultdict(set)  # (device, ip) -> domains, for flow mapping
    out = {}
    for dom, rs in by.items():
        # distinct APPS, not sessions: two Block Blast sessions are one app
        sids = {sess[r[2]][0].lower() if r[2] in sess else r[2] for r in rs if r[2] is not None}
        depth = 0
        chain_known = False  # NULL cname_chain = source has no CNAME data (e.g. PCAPdroid)
        c2t = fp = asn = 0
        launch = 0
        co = 0
        own_app = 0
        infra = NAN
        bl = []
        for r in rs:
            ts, dev, sid, _n, chain_s, ips_s = r
            chain_known = chain_known or chain_s is not None
            chain = [c.lower().strip(".") for c in _loads(chain_s)]
            ips = _loads(ips_s)
            depth = max(depth, len(chain))
            for ip in ips:
                ip_dom[(dev, ip)].add(dom)
            if chain and L.cname_tracker([dom] + chain):
                c2t = 1
            if cname_mod.ad_network_ips(ips, L):
                asn = 1
            ii = _infra_ip(ips)
            if ii == ii:
                infra = max(ii, 0 if infra != infra else infra)
            app, start = sess.get(sid, (None, None))
            if app and not own_app:
                reg = lists_norm(registrable(dom))
                words = [w for w in re.split(r"[^a-z0-9]+", app.lower())
                         if len(w) >= 4 and w not in GENERIC_APP_WORDS]
                if L.same_company(dom, app) or any(w in reg for w in words):
                    own_app = 1
            if app and chain and L.same_company(dom, app) and \
                    any(not L.same_company(h, app) and registrable(h) != registrable(dom)
                        for h in chain):
                fp = 1
            if start is not None and 0 <= ts - start <= LAUNCH_WINDOW:
                launch = 1
            co += cof[id(r)]
            bl.append(base_c[sid] / base_n[sid] if base_n.get(sid) else 0.0)
        ts_list = [r[0] for r in rs]
        gaps = [b - a for a, b in zip(ts_list, ts_list[1:]) if b - a > 0]
        delays = [r[0] - sess[r[2]][1] for r in rs if r[2] in sess and sess[r[2]][1] is not None]
        i0 = bisect.bisect_left(all_ts, ts_list[0] - CO_WINDOW)
        j0 = bisect.bisect_right(all_ts, ts_list[0] + CO_WINDOW)
        mins = sum(max(0.5, ((ends.get(x) or span[x][1]) - sess[x][1]) / 60)
                   for x in {r[2] for r in rs if r[2] in sess and r[2] in span})
        out[dom] = {
            "n_apps": len(sids),
            "cname_depth": depth if chain_known else NAN,
            "cname_to_tracker": c2t if chain_known else NAN,
            "first_party_cname": fp if chain_known else NAN, "asn_ad_network": asn,
            "co_occur_rate": co / len(rs),
            "co_occur_lift": co / len(rs) - (sum(bl) / len(bl) if bl else 0.0), "launch_burst": launch,
            "periodicity": _cv(gaps) if len(gaps) >= 2 else NAN,
            "flow_bytes_up_ratio": NAN, "flow_interval_cv": NAN,
            "launch_not_own": int(bool(launch) and not own_app),
            "name_ad_tokens": _name_tokens(dom), "ip_generic_infra": infra, "own_app_domain": own_app,
            "first_contact_delay": math.log1p(max(0.0, min(delays))) if delays else NAN,
            "burst_size": j0 - i0 - 1,
            "lookups_per_min": len(rs) / mins if mins > 0 else NAN,
            "idle_refire": int(any(g > 30 for g in gaps)),
            "flow_mean_up": NAN, "flow_mean_dur": NAN, "flows_per_lookup": NAN,
            "flow_pkt_size": NAN, "flow_port443": NAN,
            **_name_shape(dom),
            "n_lookups": len(rs),
        }
    _add_flows(conn, out, ip_dom, since_ts)
    return out


def _add_flows(conn, out, ip_dom, since_ts):
    try:
        q = ("SELECT ts, device, dst_ip, bytes_up, bytes_down, dst_port, pkts_up, pkts_down, "
             "duration_s FROM raw_flows")
        rows = conn.execute(q + (" WHERE ts >= ?" if since_ts is not None else ""),
                            (since_ts,) if since_ts is not None else ()).fetchall()
    except Exception:  # noqa: BLE001 - table may not exist yet
        return
    agg = defaultdict(list)
    for row in rows:
        for dom in ip_dom.get((row[1], row[2]), ()):
            agg[dom].append(row)
    for dom, fl in agg.items():
        if dom not in out:
            continue
        up = sum(f[3] or 0 for f in fl)
        down = sum(f[4] or 0 for f in fl)
        if up + down > 0:
            out[dom]["flow_bytes_up_ratio"] = up / (up + down)
        tss = sorted(f[0] for f in fl)
        gaps = [b - a for a, b in zip(tss, tss[1:]) if b - a > 0]
        if len(gaps) >= 2:
            out[dom]["flow_interval_cv"] = _cv(gaps)
        out[dom]["flow_mean_up"] = math.log1p(up / len(fl))
        durs = [f[8] for f in fl if f[8] is not None]
        if durs:
            out[dom]["flow_mean_dur"] = statistics.fmean(durs)
        out[dom]["flows_per_lookup"] = len(fl) / max(1, out[dom]["n_lookups"])
        pk = sum((f[6] or 0) + (f[7] or 0) for f in fl)
        if pk > 0:
            out[dom]["flow_pkt_size"] = (up + down) / pk
        out[dom]["flow_port443"] = sum(f[5] == 443 for f in fl) / len(fl)


def matrix(F, domains):
    import numpy as np
    return np.array([[F[d][f] for f in FEATURES] for d in domains], dtype=float)
