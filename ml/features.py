"""Per-domain behavior features from digin.db (raw_lookups, sessions, raw_flows).

    from ml import features, lists
    L = lists.load(exclude_companies=[...])          # held-out companies already removed
    F = features.build(conn, L)                      # {domain: {feature: value}}
    X = features.matrix(F, domains)                  # numpy, columns = features.FEATURES

Leakage rules (CLAUDE.md §6.1):
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
import statistics
from collections import defaultdict

from ml import cname as cname_mod

NAN = float("nan")

FEATURES = [
    "n_apps",             # distinct sessions it appeared in
    "cname_depth",        # max CNAME hops seen
    "cname_to_tracker",   # 0/1 chain passes through a known tracker (via L)
    "first_party_cname",  # 0/1 app's own subdomain that CNAMEs to another org
    "asn_ad_network",     # 0/1 an answer IP is on an ad/analytics network (via L)
    "co_occur_rate",      # share of lookups within 2 s of a known-tracker lookup
    "launch_burst",       # 0/1 first seen within 10 s of a session start
    "periodicity",        # CV of gaps between repeat lookups (NaN if < 3 lookups)
    "flow_bytes_up_ratio",    # flows: up / (up + down)
    "flow_interval_cv",       # flows: regularity of connection starts
]
EXTRA_COLUMNS = ["n_lookups"]  # kept for reports, not fed to the model

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

    ip_dom = defaultdict(set)  # (device, ip) -> domains, for flow mapping
    out = {}
    for dom, rs in by.items():
        sids = {r[2] for r in rs if r[2] is not None}
        depth = 0
        c2t = fp = asn = 0
        launch = 0
        co = 0
        for ts, dev, sid, _n, chain_s, ips_s in rs:
            chain = [c.lower().strip(".") for c in _loads(chain_s)]
            ips = _loads(ips_s)
            depth = max(depth, len(chain))
            for ip in ips:
                ip_dom[(dev, ip)].add(dom)
            if chain and L.cname_tracker([dom] + chain):
                c2t = 1
            if cname_mod.ad_network_ips(ips, L):
                asn = 1
            app, start = sess.get(sid, (None, None))
            if app and chain and L.same_company(dom, app) and \
                    any(not L.same_company(h, app) and registrable(h) != registrable(dom)
                        for h in chain):
                fp = 1
            if start is not None and 0 <= ts - start <= LAUNCH_WINDOW:
                launch = 1
            i = bisect.bisect_left(tracker_ts, ts - CO_WINDOW)
            j = bisect.bisect_right(tracker_ts, ts + CO_WINDOW)
            # the domain's own lookup does not count as "another tracker"
            own = 1 if L.is_tracker(dom) and j > i else 0
            co += (j - i - own) > 0
        ts_list = [r[0] for r in rs]
        gaps = [b - a for a, b in zip(ts_list, ts_list[1:]) if b - a > 0]
        out[dom] = {
            "n_apps": len(sids), "cname_depth": depth, "cname_to_tracker": c2t,
            "first_party_cname": fp, "asn_ad_network": asn,
            "co_occur_rate": co / len(rs), "launch_burst": launch,
            "periodicity": _cv(gaps) if len(gaps) >= 2 else NAN,
            "flow_bytes_up_ratio": NAN, "flow_interval_cv": NAN, "n_lookups": len(rs),
        }
    _add_flows(conn, out, ip_dom, since_ts)
    return out


def _add_flows(conn, out, ip_dom, since_ts):
    try:
        q = "SELECT ts, device, dst_ip, bytes_up, bytes_down FROM raw_flows"
        rows = conn.execute(q + (" WHERE ts >= ?" if since_ts is not None else ""),
                            (since_ts,) if since_ts is not None else ()).fetchall()
    except Exception:  # noqa: BLE001 - table may not exist yet
        return
    agg = defaultdict(lambda: [0, 0, []])
    for ts, dev, ip, up, down in rows:
        for dom in ip_dom.get((dev, ip), ()):
            a = agg[dom]
            a[0] += up or 0
            a[1] += down or 0
            a[2].append(ts)
    for dom, (up, down, tss) in agg.items():
        if dom not in out:
            continue
        if up + down > 0:
            out[dom]["flow_bytes_up_ratio"] = up / (up + down)
        tss.sort()
        gaps = [b - a for a, b in zip(tss, tss[1:]) if b - a > 0]
        if len(gaps) >= 2:
            out[dom]["flow_interval_cv"] = _cv(gaps)


def matrix(F, domains):
    import numpy as np
    return np.array([[F[d][f] for f in FEATURES] for d in domains], dtype=float)
