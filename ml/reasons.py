"""Plain-English reasons for a flag, built from feature values (docs/team-brief.md §7).

    reasons.explain(domain, feats, details) -> up to 3 strings (stored as a JSON array)

`details` = reasons.details(conn, L, domain): the evidence behind the 0/1 features
(CNAME target and its company, ad-network owner). Order is fixed by how strong and how
hard to fake each signal is: CNAME and network ownership first, name clues never.
"""
import json

from ml import cname as cname_mod

MAX_REASONS = 3


def details(conn, L, domain):
    """Evidence for one domain: first CNAME-to-tracker hit and the ad networks its IPs sit on."""
    out = {"cname": None, "asn": set()}
    for chain_s, ips_s in conn.execute(
            "SELECT cname_chain, answer_ips FROM raw_lookups WHERE qname=?", (domain,)):
        chain = [c.lower().strip(".") for c in cname_mod._loads(chain_s)]
        if out["cname"] is None and chain:
            hit = L.cname_tracker([domain] + chain)
            if hit:
                out["cname"] = hit  # (target, company, category)
        for _ip, _asn, co, _cat in cname_mod.ad_network_ips(cname_mod._loads(ips_s), L):
            out["asn"].add(co)
    return out


def explain(domain, f, det=None):
    det = det or {"cname": None, "asn": set()}
    out = []
    if f.get("cname_to_tracker") and det["cname"]:
        target, co, cat = det["cname"]
        who = f"{co} ({(cat or 'tracker').lower()})" if co else "a suspected tracker"
        out.append(f"CNAME points to {who} via {target}")
    elif f.get("cname_to_tracker"):
        out.append("CNAME points to a known tracker")
    if f.get("first_party_cname"):
        out.append("looks like the app's own subdomain but resolves to another company")
    if f.get("asn_ad_network"):
        orgs = ", ".join(sorted(det["asn"])) if det["asn"] else "an ad network"
        out.append(f"servers owned by {orgs}")
    n = int(f.get("n_apps") or 0)
    if n >= 2:
        out.append(f"seen in {n} unrelated apps")
    if (f.get("co_occur_rate") or 0) >= 0.5:
        out.append("contacted alongside known trackers")
    if f.get("launch_burst"):
        out.append("fires at app launch")
    return out[:MAX_REASONS] or ["behaves like known trackers (model score)"]


def dumps(reasons):
    return json.dumps(reasons)
