"""
dashboard/company.py - one place to answer "who owns this domain and what is it for?"

Order of sources:
  1. ml/lists.py (Samarth), once it exists: company_for(domain) -> (company, category)
  2. tools/receipt_test.py EXTRA (mobile ad networks) + Disconnect services.json if present
     at data/lists/services.json
Also importable by bridge/feedback.py (Pranshu) to decide when the box should click.
"""
import importlib.util
import os
import sys
from functools import lru_cache

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

try:
    from ml import lists as _lists
except Exception:  # noqa: BLE001
    _lists = None

_rt = None
_tmap = {}
try:
    spec = importlib.util.spec_from_file_location("receipt_test", os.path.join(REPO, "tools", "receipt_test.py"))
    _rt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(_rt)
    svc = os.path.join(REPO, "data", "lists", "services.json")
    if os.path.exists(svc):
        _tmap = _rt.load_tracker_map(svc)
except Exception as e:  # noqa: BLE001
    print(f"[company] fallback lists unavailable: {e}")

SYSTEM = ("connectivitycheck.gstatic.com", "connectivitycheck.android.com", "clients3.google.com",
          "captive.apple.com", "time.android.com", "time.apple.com", "mtalk.google.com")


_holdout = ()  # companies removed for demo mode (settings.demo_holdout), set by app.py


def set_holdout(companies):
    """Demo mode (CLAUDE.md 6.1): held-out companies must vanish from the receipt lists too,
    so the model's catch shows up as Suspected instead of as a known company."""
    global _holdout
    _holdout = tuple(sorted({c.strip() for c in companies if c and c.strip()}))


def _L(holdout):
    return _lists.load(exclude_companies=holdout) if _lists is not None else None


def company_for(domain):
    """(company, category) for known trackers/third parties, else None."""
    return _company_for(domain.lower().strip("."), _holdout)


@lru_cache(maxsize=50000)
def _company_for(d, holdout):
    if _lists is not None:
        try:
            hit = _L(holdout).company(d)
            return tuple(hit) if hit else None  # lists.py already includes EXTRA + Disconnect
        except Exception as e:  # noqa: BLE001
            print(f"[company] lists.py failed, using fallback: {e}")
    if _rt is not None:
        hit = _rt.lookup(d, _tmap)
        if hit:
            return tuple(hit)
    return None


def is_system(domain):
    d = domain.lower().strip(".")
    return any(d == s or d.endswith("." + s) for s in SYSTEM)


def is_tracker(domain):
    if _lists is not None:
        try:
            return _L(_holdout).is_tracker(domain.lower().strip("."))
        except Exception:  # noqa: BLE001
            pass
    hit = company_for(domain)
    return bool(hit) and hit[1] not in ("Content",)
