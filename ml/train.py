"""Train the tracker model and expose fit_predict for ml_eval/evaluate.py.

    fit_predict(conn, L, domains) -> {domain: probability}

Labels come ONLY from `L` (held-out / demo companies already removed, CLAUDE.md §6.1):
  positive = L.is_tracker(domain); negative = hand-confirmed content domains
  (data/lists/content_negatives.txt). Everything else is unlabeled: scored, never trained on.

Honest scoring: labeled domains that appear in `domains` are scored out-of-fold, with folds
grouped by owning company, so a domain is never scored by a model that saw it or its company
(CLAUDE.md rule: grouped split). Unlabeled domains (e.g. held-out trackers) get the model fit
on all labeled data.

    python -m ml.train --db data/sessions/digin_run1.db --out ml/models
"""
import argparse
import datetime as dt
import json
import os
import sqlite3
import sys

from ml import features as feat
from ml import lists as lists_mod

HERE = os.path.dirname(os.path.abspath(__file__))
NEG_PATH = os.path.join(HERE, "..", "data", "lists", "content_negatives.txt")
MODELS = os.path.join(HERE, "models")
N_FOLDS = 4
MIN_PER_CLASS = 3


def load_negatives(path=NEG_PATH):
    out = set()
    if os.path.exists(path):
        for line in open(path):
            line = line.split("#", 1)[0].strip().lower().strip(".")
            if line:
                out.add(line)
    return out


def _is_neg(d, negs):
    return any(d == n or d.endswith("." + n) for n in negs)


def label(L, domains, negs):
    """{domain: 1|0} for labeled domains only."""
    out = {}
    for d in domains:
        if L.is_tracker(d):
            out[d] = 1
        elif not L.is_known(d) and _is_neg(d, negs) or (L.is_known(d) and not L.is_tracker(d)
                                                          and _is_neg(d, negs)):
            out[d] = 0
    return out


def _group(L, d):
    return L.owner(d) or feat.registrable(d)


def _model(seed=0):
    from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
    if os.environ.get("ML_MODEL", "hgb") == "rf":
        return RandomForestClassifier(n_estimators=200, min_samples_leaf=2, max_features="sqrt",
                                      class_weight="balanced_subsample", random_state=seed,
                                      n_jobs=1)
    return HistGradientBoostingClassifier(max_depth=3, max_iter=100, learning_rate=0.1,
                                          class_weight="balanced", random_state=seed)


class Model:
    """Two HistGradientBoosting models, routed per row by whether flow data exists.

    Why: rows without raw_flows (DNS-only sessions) and rows with it come from different sources
    with different label mixes, so a single model learned "no flow data" as a clue for "content"
    and missed DNS-only trackers. `noflow` never sees the flow columns; `flow` is fit only on
    rows that have them. Columns that are all-NaN in training are dropped (sklearn's binning
    crashes on those)."""
    MIN_FLOW_ROWS = 20

    def __init__(self, X, y):
        import numpy as np
        y = np.asarray(y)
        flow = np.array([f.startswith("flow_") for f in feat.FEATURES])
        self.key = feat.FEATURES.index("flow_bytes_up_ratio") if "flow_bytes_up_ratio" in feat.FEATURES else None
        live = ~np.isnan(X).all(axis=0)
        self.cols0 = live & ~flow                      # no-flow model: never sees flow columns
        self.clf0 = _model().fit(X[:, self.cols0], y)
        self.clf1 = None
        if self.key is not None:
            has = ~np.isnan(X[:, self.key])
            if has.sum() >= self.MIN_FLOW_ROWS and min(y[has].sum(), (1 - y[has]).sum()) >= MIN_PER_CLASS:
                self.cols1 = ~np.isnan(X[has]).all(axis=0)
                self.clf1 = _model().fit(X[has][:, self.cols1], y[has])

    def predict_proba(self, X):
        import numpy as np
        p = self.clf0.predict_proba(X[:, self.cols0])
        if self.clf1 is not None:
            has = ~np.isnan(X[:, self.key])
            if has.any():
                p[has] = self.clf1.predict_proba(X[has][:, self.cols1])
        return p


def _fit(X, y):
    if len(set(y)) < 2 or min(sum(y), len(y) - sum(y)) < MIN_PER_CLASS:
        return None
    # import by module path so the pickle says ml.train.Model even under `python -m ml.train`
    from ml.train import Model as _Model
    return _Model(X, y)


def _proba(m, X, prior):
    return [prior] * len(X) if m is None else m.predict_proba(X)[:, 1].tolist()


def fit_predict(conn, L, domains, negatives=None, return_model=False):
    negs = negatives if negatives is not None else load_negatives()
    F = feat.build(conn, L)
    domains = [d for d in domains if d in F]
    lab = label(L, F.keys(), negs)
    ld = sorted(lab)
    X_all = feat.matrix(F, ld) if ld else None
    y_all = [lab[d] for d in ld]
    prior = sum(y_all) / len(y_all) if y_all else 0.0
    scores = {}

    full = _fit(X_all, y_all) if ld else None
    unl = [d for d in domains if d not in lab]
    if unl:
        for d, p in zip(unl, _proba(full, feat.matrix(F, unl), prior)):
            scores[d] = p

    # out-of-fold scores for labeled domains, folds grouped by company
    groups = {}
    for d in ld:
        groups.setdefault(_group(L, d), []).append(d)
    names = sorted(groups, key=lambda g: (-len(groups[g]), g))
    k = min(N_FOLDS, len(names))
    folds = [[] for _ in range(k)]
    for i, g in enumerate(names):
        folds[i % k].extend(groups[g])
    for fold in folds:
        held = set(fold)
        tr = [d for d in ld if d not in held]
        m = _fit(feat.matrix(F, tr), [lab[d] for d in tr]) if tr else None
        te = [d for d in fold if d in set(domains)]
        if te:
            for d, p in zip(te, _proba(m, feat.matrix(F, te), prior)):
                scores[d] = p
    return (scores, full, F, lab) if return_model else scores


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True)
    ap.add_argument("--holdout", action="append", default=[])
    ap.add_argument("--out", default=MODELS)
    ap.add_argument("--version", default=dt.datetime.now().strftime("%Y%m%d-%H%M"))
    a = ap.parse_args(argv)
    import joblib
    conn = sqlite3.connect(a.db)
    L = lists_mod.load(exclude_companies=a.holdout)
    _, model, F, lab = fit_predict(conn, L, [], return_model=True)
    if model is None:
        print(f"not enough labeled data: {sum(lab.values())} positives, "
              f"{len(lab) - sum(lab.values())} negatives (need {MIN_PER_CLASS}+ each)")
        return 1
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, f"model_{a.version}.joblib")
    joblib.dump(model, path)
    meta = {"version": a.version, "features": feat.FEATURES, "heldout": a.holdout,
            "n_pos": sum(lab.values()), "n_neg": len(lab) - sum(lab.values()),
            "trained": dt.datetime.now().isoformat(timespec="seconds")}
    json.dump(meta, open(path.replace(".joblib", ".json"), "w"), indent=1)
    print(json.dumps(meta))
    return 0


if __name__ == "__main__":
    sys.exit(main())
