"""Compare feature groups on grouped held-out evaluation.

    python3 tools/ablate.py --db data/sessions/all.db

For each held-out company set, trains with company-grouped out-of-fold scoring (ml.train.fit_predict)
using the baseline features plus one extra group at a time, and reports ROC-AUC and precision/recall
at 0.7 and 0.8. Negatives are confirmed content domains that no list covers (what the box would
actually score). Only keep a group if it helps on most held-out sets, not just one.
"""
import argparse
import os
import sqlite3
import statistics
import sys
import warnings

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
warnings.filterwarnings("ignore")
from sklearn.metrics import roc_auc_score  # noqa: E402

from ml import features, lists, train  # noqa: E402

HELD = [["InMobi", "Vungle"], ["Unity Ads"], ["AppLovin"], ["Mintegral"]]


def run(conn, feats, held, thresholds=(0.7, 0.8)):
    features.FEATURES[:] = feats
    L = lists.load(exclude_companies=held)
    F = features.build(conn, L)
    dx = list(F)
    lb = train.label(L, dx, train.load_negatives())
    sc = train.fit_predict(conn, L, dx)
    s = lambda d: sc[d][0] if isinstance(sc[d], tuple) else sc[d]  # noqa: E731
    L0 = lists.load()
    neg = [d for d in dx if lb.get(d) == 0 and not L0.is_known(d) and not L0.is_allowlisted(d)]
    pos = [d for d in dx if L0.company(d) and L0.company(d)[0] in held]
    auc = roc_auc_score([1] * len(pos) + [0] * len(neg), [s(d) for d in pos + neg])
    out = {"auc": auc}
    for t in thresholds:
        tp = sum(s(d) >= t for d in pos)
        fp = sum(s(d) >= t for d in neg)
        out[t] = (tp, len(pos), fp, len(neg))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    a = ap.parse_args()
    conn = sqlite3.connect(a.db)
    base = list(features.BASE_FEATURES)
    variants = {"base": base}
    for g, cols in features.FEATURE_GROUPS.items():
        variants["+" + g] = base + cols
    variants["+all"] = base + sum(features.FEATURE_GROUPS.values(), [])
    summary = {}
    for name, feats in variants.items():
        rs = [run(conn, feats, h) for h in HELD]
        for h, r in zip(HELD, rs):
            print(f"{name:12s} {'+'.join(h):18s} auc={r['auc']:.2f}  " + "  ".join(
                f"@{t}: {r[t][0]}/{r[t][1]} caught, {r[t][2]}/{r[t][3]} false" for t in (0.7, 0.8)))
        prec = lambda r, t: r[t][0] / (r[t][0] + r[t][2]) if r[t][0] + r[t][2] else 0.0  # noqa: E731
        rec = lambda r, t: r[t][0] / r[t][1]  # noqa: E731
        summary[name] = (statistics.fmean(r["auc"] for r in rs),
                         statistics.fmean(prec(r, 0.8) for r in rs),
                         statistics.fmean(rec(r, 0.8) for r in rs))
    print("\nmean over held-out sets:   AUC   precision@0.8  recall@0.8")
    for k, (au, pr, re_) in summary.items():
        print(f"  {k:12s} {au:6.3f}     {pr:6.2f}        {re_:6.2f}")


if __name__ == "__main__":
    main()
