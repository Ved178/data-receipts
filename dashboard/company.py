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
import re
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

# Other apps / the phone itself talking in the background. They show up in every session no matter
# which app is tested, so the receipt puts them on one "Background" line instead of the app's total.
# (label, app-name words, domain suffixes): if the tested app's name contains one of the words, that
# group is NOT background (e.g. Snapchat domains in a Snapchat session count normally).
BACKGROUND = [
    ("Apple / iCloud", ("apple", "icloud", "iphone"),
     ("apple.com", "icloud.com", "aaplimg.com", "apple-dns.net", "mzstatic.com", "apple-cloudkit.com",
      "icloud-content.com", "cdn-apple.com", "apple-mapkit.com")),
    ("WhatsApp", ("whatsapp",), ("whatsapp.net", "whatsapp.com")),
    ("Snapchat", ("snap",), ("snapchat.com", "sc-cdn.net", "sc-static.net", "snapkit.com", "snap-dev.net")),
]

# Owner names from the lists that mean nothing to a person reading the receipt.
# Volentio JSD Ltd runs jsDelivr (cdn.jsdelivr.net), a free public code CDN.
DISPLAY = {"Volentio JSD": "jsDelivr (code CDN)"}

# Classify by what a domain DOES, not only who owns it: an ad server run by a company the lists file
# under "Content" (amazon-adsystem.com, *.advertising.a2z.com, pagead2...) is still advertising.
AD_PARTS = {"ad", "ads", "adsystem", "advertising", "adserver", "adservice", "adserv", "pagead",
            "adx", "doubleclick", "ssp", "rtb", "vast", "adsapi"}
ANALYTICS_PARTS = {"analytics", "metrics", "telemetry", "measurement", "crashlytics"}


def parts(domain):
    """Whole name parts: 'ssp-win.startappservice.com' -> {ssp, win, startappservice, com}."""
    return set(re.split(r"[.\-]", domain.lower().strip(".")))


def by_function(names, category):
    """Upgrade a 'Content' (or unknown) category when a domain name says it serves ads/analytics."""
    if category not in (None, "Content"):
        return category
    p = set()
    for n in names:
        p |= parts(n)
    if p & AD_PARTS:
        return "Advertising"
    if p & ANALYTICS_PARTS:
        return "Analytics"
    return category


def base(name):
    """Registrable name: ntusus.bidease.com -> bidease.com, a.b.co.uk -> b.co.uk."""
    p = name.lower().strip(".").split(".")
    if len(p) >= 3 and p[-2] in ("co", "com", "net", "org", "gov", "ac", "edu") and len(p[-1]) == 2:
        return ".".join(p[-3:])
    return ".".join(p[-2:])


def display(name):
    return DISPLAY.get(name, name)


def background(names, app=None):
    """Label of the background group these names belong to, or None. Never the tested app itself."""
    a = (app or "").lower()
    for label, app_words, suffixes in BACKGROUND:
        if any(w in a for w in app_words):
            continue
        for n in names:
            d = n.lower().strip(".")
            if any(d == s or d.endswith("." + s) for s in suffixes):
                return label
    return None


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
