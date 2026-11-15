"""Live scorer: rebuild features, score domains no list knows, upsert ml_flags.

    python3 -m ml.score --db /home/arduino/digin/digin.db --once
    python3 -m ml.score --db ... --loop 3

Rules (CLAUDE.md §7 / handoff §5):
  * only domains NOT on any list and not allowlisted are scored (Shield owns the rest);
  * new rows are 'suspected'; 'auto_blocked' is OFF unless --auto-block, and then only if score >= AUTO_BLOCK_THRESHOLD, the
    domain is not the current session app's own company, and it is not allowlisted;
  * never touch a row a human set to dismissed/allowlisted; model rows replace rules-v1 rows;
  * survives a locked DB (retry with backoff) and a missing model (log and keep looping).
"""
import argparse
import glob
import json
import re
import os
import sqlite3
import sys
import time

from ml import features as feat
from ml import lists as lists_mod
from ml import reasons as reasons_mod

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HERE, "models")
FLAG_THRESHOLD = 0.8  # lowest score written; the dashboard shows >=0.95 as High confidence, 0.8-0.95 as Possible
AUTO_BLOCK_THRESHOLD = 0.97
LOCK_RETRIES = 5


def load_latest_model(models_dir=MODELS):
    """(model, meta) for the newest model_*.joblib, or (None, None)."""
    files = sorted(glob.glob(os.path.join(models_dir, "model_*.joblib")), key=os.path.getmtime)
    if not files:
        return None, None
    import joblib
    path = files[-1]
    meta_path = path.replace(".joblib", ".json")
    meta = json.load(open(meta_path)) if os.path.exists(meta_path) else {}
    meta.setdefault("version", os.path.basename(path)[6:-7])
    if meta.get("features") not in (None, feat.FEATURES):
        raise RuntimeError("model was trained with a different feature list; retrain")
    return joblib.load(path), meta


def current_app(conn):
    row = conn.execute("SELECT app_name FROM sessions WHERE end_ts IS NULL "
                       "ORDER BY start_ts DESC LIMIT 1").fetchone()
    return row[0] if row else None


_CALL_INFRA = re.compile(r"^(stun|turn|turns)\d*$")


def is_call_infra(name):
    """STUN/TURN servers set up voice and video calls. They look chatty and periodic, so a
    model can mistake them for trackers, but blocking them breaks calling: never score them."""
    return bool(_CALL_INFRA.match((name or "").split(".")[0]))


_AUTH_TOKENS = {"login", "logon", "signin", "signon", "auth", "oauth", "sso", "accounts", "account",
                "idp", "identity"}


def is_auth_endpoint(name):
    """Login / sign-in hosts (login.aa.com, eu-auth2.samsungosp.com). Blocking one breaks a login
    mid-demo, and the model tends to score them high, so they are never scored. Matches whole
    tokens of the first label (digits stripped), so 'authority-ads' or 'id5-sync' don't match."""
    first = (name or "").lower().split(".")[0]
    return any(re.sub(r"\d+", "", t) in _AUTH_TOKENS for t in re.split(r"[^a-z0-9]+", first) if t)


def score_domains(conn, L, model):
    """{domain: (score, features)} for unlisted, non-allowlisted domains."""
    F = feat.build(conn, L)
    cand = [d for d in F if not L.is_known(d) and not L.is_allowlisted(d) and not is_call_infra(d)
            and not is_auth_endpoint(d)]
    if not cand:
        return {}
    probs = model.predict_proba(feat.matrix(F, cand))[:, 1]
    return {d: (float(p), F[d]) for d, p in zip(cand, probs)}


def write_flags(conn, L, scored, version, threshold=FLAG_THRESHOLD,
                auto_threshold=None, now=None):
    """auto_threshold=None disables auto_blocked (the default until held-out precision is >= 0.9)."""
    now = now or time.time()
    app = current_app(conn)
    n = 0
    with conn:
        for dom, (score, f) in scored.items():
            if score < threshold:
                continue
            row = conn.execute("SELECT status, model_version FROM ml_flags WHERE domain=?",
                               (dom,)).fetchone()
            if row and row[0] in ("dismissed", "allowlisted"):
                continue
            det = reasons_mod.details(conn, L, dom)
            why = reasons_mod.dumps(reasons_mod.explain(dom, f, det))
            auto = auto_threshold is not None and score >= auto_threshold and not L.same_company(dom, app) \
                and not L.is_allowlisted(dom)
            # A row's status is never demoted here: the dashboard's Block button sets
            # 'auto_blocked' and Shield reads it, so a person's choice must survive every pass.
            status = "auto_blocked" if auto else (row[0] if row else "suspected")
            conn.execute(
                "INSERT INTO ml_flags(domain, score, reasons, status, model_version, "
                "flagged_ts, updated_ts) VALUES (?,?,?,?,?,?,?) ON CONFLICT(domain) DO UPDATE "
                "SET score=excluded.score, reasons=excluded.reasons, status=excluded.status, "
                "model_version=excluded.model_version, updated_ts=excluded.updated_ts",
                (dom, score, why, status, version, now, now))
            n += 1
    return n


def run_once(conn, model, meta, threshold=FLAG_THRESHOLD, auto_threshold=None):
    L = lists_mod.load_live(conn)
    return write_flags(conn, L, score_domains(conn, L, model), meta["version"], threshold,
                       auto_threshold)


def _retry(fn, *a, **kw):
    delay = 0.2
    for i in range(LOCK_RETRIES):
        try:
            return fn(*a, **kw)
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) or i == LOCK_RETRIES - 1:
                raise
            time.sleep(delay)
            delay *= 2


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="/home/arduino/digin/digin.db")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", type=float, default=5.0)
    ap.add_argument("--threshold", type=float, default=FLAG_THRESHOLD)
    ap.add_argument("--models", default=MODELS)
    ap.add_argument("--auto-block", action="store_true",
                    help=f"allow status auto_blocked at score >= {AUTO_BLOCK_THRESHOLD} (off by default)")
    a = ap.parse_args(argv)
    conn = sqlite3.connect(a.db, timeout=3)
    conn.execute("PRAGMA busy_timeout=3000")
    model = meta = None
    while True:
        try:
            if model is None:
                model, meta = load_latest_model(a.models)
            if model is None:
                print("score: no model file yet; sleeping", flush=True)
                if a.once:
                    return 1
            else:
                t = time.time()
                n = _retry(run_once, conn, model, meta, a.threshold,
                           AUTO_BLOCK_THRESHOLD if a.auto_block else None)
                print(f"score: wrote {n} flags in {time.time() - t:.2f}s "
                      f"(model {meta['version']})", flush=True)
        except sqlite3.Error as e:
            print(f"score: db error {e}; will retry", flush=True)
            if a.once:
                return 1
        if a.once:
            return 0
        time.sleep(a.loop)


if __name__ == "__main__":
    sys.exit(main())
