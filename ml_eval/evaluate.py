"""Grouped held-out evaluation (docs/team-brief.md §7 Samarth #4, §6.1, §6.4).

Hold out whole companies, remove them from EVERY list (labels, blocklist, CNAME
targets, ad-network IP owners) via ml.lists.load(exclude_companies=...), let each
scorer see the data, and count how many of the held-out companies' domains it flags.

    python3 -m ml_eval.evaluate --db data/sessions/pcapdroid.db --holdout InMobi --holdout Vungle
    python3 -m ml_eval.evaluate --db ... --holdout InMobi --holdout Vungle --folds   # one fold each too
    python3 -m ml_eval.evaluate --db ... --suggest-negatives > /tmp/cands.txt       # to hand-confirm

Ground truth
  positives  domains seen in the data that the FULL lists attribute to a held-out company
  negatives  domains seen in the data AND listed in data/lists/content_negatives.txt
             (hand-confirmed content: app APIs, CDNs). Unlabeled is not negative.
  other      everything else unlabeled: flags there are reported, not scored

Scorers
  list   flagged iff on the (excluded) tracker list. Should catch 0 held-out domains;
         if it catches any, that's leakage and the run says so.
  rules  ml.cname rule flags (CNAME to tracker, ad-network IPs) under the same exclusions
  model  Ved's model, if ml/train.py exposes
             fit_predict(conn, L, domains) -> {domain: score in [0, 1]}
         where L = the excluded lists (labels must come from L only). Skipped if missing.

Writes: appends a run to RESULTS.md, rewrites ml_eval/results_table.md (slide table).
"""
import argparse
import datetime as dt
import importlib
import json
import os
import sqlite3
import sys

from ml import cname
from ml import lists as lists_mod

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
NEG_PATH = os.path.join(ROOT, "data", "lists", "content_negatives.txt")
RESULTS = os.path.join(ROOT, "RESULTS.md")
TABLE = os.path.join(HERE, "results_table.md")


def _belongs(name, doms):
    return any(name == d or name.endswith("." + d) for d in doms)


def load_negatives(path=NEG_PATH):
    if not os.path.exists(path):
        return set()
    out = set()
    for line in open(path):
        line = line.split("#", 1)[0].strip().lower().strip(".")
        if line:
            out.add(line)
    return out


def seen_domains(conn):
    return sorted({(q or "").lower().strip(".") for (q,) in
                   conn.execute("SELECT DISTINCT qname FROM raw_lookups") if q})


def ground_truth(conn, holdout, negatives):
    full = lists_mod.load()
    held_doms = {c: full.domains_of(c) for c in holdout}
    seen = seen_domains(conn)
    pos = {c: sorted(d for d in seen if _belongs(d, doms)) for c, doms in held_doms.items()}
    allpos = {d for v in pos.values() for d in v}
    neg = sorted(d for d in seen if d not in allpos and
                 any(d == n or d.endswith("." + n) for n in negatives))
    return seen, pos, neg


def leak_check(conn, L, positives):
    """Anything that would let a held-out domain be caught by a list in disguise."""
    leaks = []
    pos = set(positives)
    for d in positives:
        if L.is_known(d):
            leaks.append(f"{d} still on a list as {L.company(d)}")
    for q, ips in conn.execute("SELECT qname, answer_ips FROM raw_lookups"):
        q = (q or "").lower().strip(".")
        if q not in pos:
            continue
        for ip in cname._loads(ips):
            o = L.ip_owner(ip)
            if o and any(lists_mod.company_matches(o[1], [lists_mod.norm(c)]) for c in L.excluded):
                leaks.append(f"{q} -> {ip} still owned by {o[1]} in ASN list")
    for k, v in L.cnames.items():
        if _belongs(v, L.excluded_domains) or _belongs(k, L.excluded_domains):
            leaks.append(f"CNAME map still has {k} -> {v}")
    return sorted(set(leaks))


# ---- scorers ---------------------------------------------------------------------
def score_list(conn, L, domains):
    return {d: 1.0 for d in domains if L.is_tracker(d)}


def score_rules(conn, L, domains):
    flags = cname.rule_flags(conn, L)
    return {d: s for d, (s, _r, _t) in flags.items()}


def score_model(conn, L, domains):
    mod = importlib.import_module("ml.train")
    return mod.fit_predict(conn, L, domains)


def available_scorers():
    out = [("list-only", score_list), ("rules-only", score_rules)]
    try:
        mod = importlib.import_module("ml.train")
        if hasattr(mod, "fit_predict"):
            out.append(("model", score_model))
    except Exception as e:  # noqa: BLE001 - model may not exist yet
        print(f"(model skipped: {type(e).__name__}: {e})", file=sys.stderr)
    return out


def evaluate(conn, holdout, negatives, threshold=0.5, scorers=None, skip_covered=False):
    L = lists_mod.load(exclude_companies=holdout)
    seen, pos_by_co, neg = ground_truth(conn, holdout, negatives)
    n_skipped = 0
    if skip_covered:
        # ml/score.py never scores a domain that is on any list or allowlist, so counting such
        # domains as possible false flags measures something the box never does.
        kept = [d for d in neg if not L.is_known(d)]
        n_skipped, neg = len(neg) - len(kept), kept
    pos = sorted({d for v in pos_by_co.values() for d in v})
    other = [d for d in seen if d not in set(pos) | set(neg) and not L.is_known(d)]
    res = {"holdout": holdout, "threshold": threshold, "n_seen": len(seen),
           "positives": {c: len(v) for c, v in pos_by_co.items()}, "n_neg": len(neg),
           "n_skipped_covered": n_skipped, "n_other": len(other), "leaks": leak_check(conn, L, pos), "scorers": []}
    for name, fn in scorers or available_scorers():
        scores = fn(conn, L, seen)
        flagged = {d for d, s in scores.items() if s >= threshold}
        if skip_covered:
            # same guards as ml/score.py: login hosts and STUN/TURN servers are never scored on the box
            from ml.score import is_auth_endpoint, is_call_infra
            flagged = {d for d in flagged if not (is_auth_endpoint(d) or is_call_infra(d))}
        tp = sorted(d for d in pos if d in flagged)
        fp = sorted(d for d in neg if d in flagged)
        fo = sorted(d for d in other if d in flagged)
        prec = len(tp) / (len(tp) + len(fp)) if tp or fp else None
        rec = len(tp) / len(pos) if pos else None
        res["scorers"].append({
            "name": name, "tp": len(tp), "p": len(pos), "fp": len(fp), "n": len(neg),
            "precision": prec, "recall": rec, "flags_on_other_unlabeled": len(fo),
            "caught": tp, "missed": sorted(set(pos) - set(tp)), "false_flags": fp,
            "per_company": {c: sum(d in flagged for d in v) for c, v in pos_by_co.items()},
        })
    return res


def _pct(x):
    return "n/a" if x is None else f"{x * 100:.0f}%"


def table(res):
    held = " + ".join(res["holdout"])
    lines = [f"Held out: **{held}** (removed from labels, blocklist, CNAME targets, ad-network owners)",
             "", "| Method | Caught held-out domains | False flags on content domains | Precision | Recall |",
             "|---|---|---|---|---|"]
    for s in res["scorers"]:
        lines.append(f"| {s['name']} | {s['tp']} of {s['p']} | {s['fp']} of {s['n']} | "
                     f"{_pct(s['precision'])} | {_pct(s['recall'])} |")
    lines.append("")
    lines.append(f"Leak check: {'PASS (no held-out domain reachable through any list)' if not res['leaks'] else 'FAIL: ' + '; '.join(res['leaks'][:5])}")
    return "\n".join(lines)


def write_results(results, db_path, note=""):
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    body = [f"\n## Eval run {stamp}", f"- data: `{os.path.relpath(db_path, ROOT)}`"]
    if note:
        body.append(f"- note: {note}")
    for r in results:
        body.append(f"- seen {r['n_seen']} domains; positives {r['positives']}; "
                    f"hand-confirmed negatives {r['n_neg']}; other unlabeled {r['n_other']}; "
                    f"threshold {r['threshold']}")
        body += ["", table(r), ""]
        for s in r["scorers"]:
            if s["missed"]:
                body.append(f"- {s['name']} missed: {', '.join(s['missed'][:15])}")
            if s["false_flags"]:
                body.append(f"- {s['name']} false flags: {', '.join(s['false_flags'][:15])}")
            body.append(f"- {s['name']} flagged {s['flags_on_other_unlabeled']} other unlabeled domains")
    new = not os.path.exists(RESULTS)
    with open(RESULTS, "a") as f:
        if new:
            f.write("# RESULTS\n\nEvery eval run, appended by `python3 -m ml_eval.evaluate`. "
                    "Counts next to percentages (docs/team-brief.md §6.4).\n")
        f.write("\n".join(body) + "\n")
    with open(TABLE, "w") as f:
        f.write(f"<!-- generated {stamp} by ml_eval/evaluate.py -->\n")
        f.write("\n\n".join(table(r) for r in results) + "\n")


def suggest_negatives(conn):
    """Unlabeled domains seen in the data, most-seen first, for a human to confirm."""
    L = lists_mod.load()
    rows = conn.execute("SELECT qname, COUNT(*), COUNT(DISTINCT session_id) FROM raw_lookups "
                        "GROUP BY qname ORDER BY 3 DESC, 2 DESC")
    out = []
    for q, n, s in rows:
        q = (q or "").lower().strip(".")
        if q and not L.is_known(q):
            out.append(f"# {q}    seen x{n} in {s} sessions")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True)
    ap.add_argument("--holdout", action="append", default=[])
    ap.add_argument("--folds", action="store_true", help="also one fold per held-out company")
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--negatives", default=NEG_PATH)
    ap.add_argument("--note", default="")
    ap.add_argument("--skip-covered", action="store_true",
                    help="production view: drop content negatives already on a list/allowlist "
                         "(the box never scores them) and apply ml/score.py's login/call-server "
                         "guards. Default off.")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--suggest-negatives", action="store_true")
    a = ap.parse_args(argv)
    conn = sqlite3.connect(a.db)
    if a.suggest_negatives:
        print("\n".join(suggest_negatives(conn)))
        return 0
    if not a.holdout:
        ap.error("give at least one --holdout company")
    neg = load_negatives(a.negatives)
    runs = [a.holdout] + ([[c] for c in a.holdout] if a.folds and len(a.holdout) > 1 else [])
    results = [evaluate(conn, h, neg, a.threshold, skip_covered=a.skip_covered) for h in runs]
    for r in results:
        print(table(r) + "\n")
        if a.skip_covered:
            print(f"(production view: skipped {r['n_skipped_covered']} content domains already on a "
                  f"list or allowlist; they are never scored by ml/score.py)\n")
    print(json.dumps([{k: v for k, v in r.items() if k != "scorers"} for r in results], indent=1))
    if not a.no_write:
        write_results(results, a.db, a.note)
        print(f"appended to {os.path.relpath(RESULTS)}; table in {os.path.relpath(TABLE)}")
    return 1 if any(r["leaks"] for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
