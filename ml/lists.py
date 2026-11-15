"""One shared lookup: domain -> (company, category), known-tracker check, allowlist,
ad-network IP owners and CNAME targets — all with held-out companies removed.

    from ml import lists
    L = lists.load()                                  # everything
    L = lists.load(exclude_companies=["InMobi"])      # eval / demo mode (docs/team-brief.md §6.1)
    L = lists.load_live(conn)                         # reads settings.demo_holdout, cached

    L.company("a.b.doubleclick.net")   -> ("Google", "Advertising") or None
    L.is_tracker(name)                 -> bool  (list tracker, not allowlisted)
    L.is_allowlisted(name)             -> bool  (CDNs, phone system services: never block)
    L.ip_owner("1.2.3.4")              -> (asn, company, category) or None
    L.cname_tracker(chain)             -> (name, company, category) for first tracker in chain
    L.same_company(name, app_name)     -> True if name belongs to that app's own company

Sources (all offline, in data/lists/):
  services.json      Disconnect tracking-protection list
  android-tds.json   DuckDuckGo Tracker Radar (Android blocklist build): trackers, owners, CNAMEs
  EXTRA below        our mobile ad-SDK supplement (from tools/receipt_test.py, extended)
  ad_asn_ranges.tsv  ad/analytics network IP ranges (see ml/asn.py)
  apps.json          app name -> its own company + domains (never block those)

Exclusion removes a company from every one of those at once: blocklist, company map,
CNAME targets, ASN owners. A company matches an exclude term when the normalized term
(lowercase letters/digits) appears inside the normalized company name, e.g. "vungle"
matches "Liftoff (Vungle)". Every domain that any source attributes to an excluded
company is dropped from all sources, so a second list can't leak it back in.
"""
import json
import os
import re
import sqlite3
import threading

from ml.asn import AsnDB, RANGES_PATH

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data", "lists")

# Disconnect categories that mean "tracking". Content/Anti-fraud/ConsentManagers are
# known companies but not trackers.
DISCONNECT_TRACKING = {"Advertising", "Analytics", "FingerprintingInvasive",
                       "FingerprintingGeneral", "Social", "Cryptomining",
                       "Disconnect", "EmailAggressive"}
PRIORITY = ["Advertising", "Analytics", "FingerprintingInvasive", "FingerprintingGeneral",
            "Social", "Cryptomining", "Disconnect", "EmailAggressive", "Email",
            "Anti-fraud", "ConsentManagers", "Content"]
RANK = {c: i for i, c in enumerate(PRIORITY)}

DDG_AD = {"Advertising", "Ad Motivated Tracking", "Action Pixels", "Ad Fraud"}
DDG_ANALYTICS = {"Analytics", "Audience Measurement", "Third-Party Analytics Marketing",
                 "Session Replay"}

# Mobile ad SDKs that Disconnect's web-focused list misses (seen in real captures).
EXTRA = {
    "unity3d.com": ("Unity Ads", "Advertising"),
    "unityads.unity3d.com": ("Unity Ads", "Advertising"),
    "vungle.com": ("Liftoff (Vungle)", "Advertising"),
    "liftoff.io": ("Liftoff (Vungle)", "Advertising"),
    "moloco.com": ("Moloco", "Advertising"),
    "bidmachine.io": ("BidMachine", "Advertising"),
    "inner-active.mobi": ("Digital Turbine (Fyber)", "Advertising"),
    "fyber.com": ("Digital Turbine (Fyber)", "Advertising"),
    "mtgglobals.com": ("Mintegral", "Advertising"),
    "rayjump.com": ("Mintegral", "Advertising"),
    "mintegral.com": ("Mintegral", "Advertising"),
    "inmobi.com": ("InMobi", "Advertising"),
    "inmobicdn.net": ("InMobi", "Advertising"),
    "superawesome.tv": ("SuperAwesome", "Advertising"),
    "kwcdn.com": ("Kwai (Kuaishou) Ads", "Advertising"),
    "applovin.com": ("AppLovin", "Advertising"),
    "applvn.com": ("AppLovin", "Advertising"),
    "ironsrc.mobi": ("ironSource (Unity)", "Advertising"),
    "supersonicads.com": ("ironSource (Unity)", "Advertising"),
    "chartboost.com": ("Chartboost", "Advertising"),
    "pangle.io": ("Pangle (ByteDance)", "Advertising"),
    "pangleglobal.com": ("Pangle (ByteDance)", "Advertising"),
    "googlesyndication.com": ("Google", "Advertising"),
    "googleadservices.com": ("Google", "Advertising"),
    "googletagservices.com": ("Google", "Advertising"),
    "doubleclick.net": ("Google", "Advertising"),
    "firebaselogging.googleapis.com": ("Google", "Analytics"),
    "firebaselogging-pa.googleapis.com": ("Google", "Analytics"),
    "app-measurement.com": ("Google", "Analytics"),
    "crashlytics.com": ("Google", "Analytics"),
    "appsflyer.com": ("AppsFlyer", "Analytics"),
    "appsflyersdk.com": ("AppsFlyer", "Analytics"),
    "adjust.com": ("Adjust (AppLovin)", "Analytics"),
    "adjust.io": ("Adjust (AppLovin)", "Analytics"),
    "branch.io": ("Branch", "Analytics"),
    "helpshift.com": ("Helpshift", "Content"),
    "graph.facebook.com": ("Meta", "Advertising"),
}
EXTRA_NON_TRACKING = {"Content"}

# Never block, never flag. Most-specific match wins, so a tracker listed at a deeper
# level (firebaselogging.googleapis.com) still beats an allowlisted parent (googleapis.com).
ALLOWLIST = {
    # phone system / connectivity
    "connectivitycheck.gstatic.com", "connectivitycheck.android.com", "clients3.google.com",
    "captive.apple.com", "time.android.com", "pool.ntp.org", "time.apple.com",
    "mtalk.google.com", "play.googleapis.com", "android.clients.google.com",
    "play.google.com", "android.googleapis.com", "push.apple.com", "icloud.com",
    "apple.com", "mzstatic.com", "googleapis.com", "gstatic.com", "gvt1.com", "gvt2.com",
    # CDNs / clouds
    "akamaized.net", "akamaihd.net", "akamaiedge.net", "edgekey.net", "edgesuite.net",
    "fastly.net", "fastlylb.net", "cloudfront.net", "cloudflare.com", "cloudflare.net",
    "amazonaws.com", "azureedge.net", "googleusercontent.com", "ggpht.com", "ytimg.com",
    "googlevideo.com", "jsdelivr.net",
}


def _read_suffix_file(path):
    try:
        with open(path) as f:
            return {ln.strip().lower() for ln in f if ln.strip() and not ln.startswith("#")}
    except OSError:
        return set()


# Extra infrastructure suffixes kept in a data file so they can grow without code edits.
for _f in ("allowlist_trackerradar.txt", "infrastructure_allowlist.txt"):
    ALLOWLIST |= _read_suffix_file(os.path.join(DATA, _f))


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def company_matches(company, terms):
    c = norm(company)
    return any(t and t in c for t in terms)


def _suffixes(name):
    parts = (name or "").lower().strip(".").split(".")
    return [".".join(parts[i:]) for i in range(len(parts) - 1)]


def _read_json(path):
    with open(path) as f:
        return json.load(f)


def _load_disconnect(path):
    out = {}
    if not os.path.exists(path):
        return out
    data = _read_json(path)
    for category, entries in data["categories"].items():
        for entry in entries:
            for company, sites in entry.items():
                for _home, domains in sites.items():
                    if not isinstance(domains, list):
                        continue
                    for d in domains:
                        d = d.lower().strip(".")
                        old = out.get(d)
                        if old is None or RANK.get(category, 99) < RANK.get(old[1], 99):
                            out[d] = (company, category)
    return out


def _load_ddg(path):
    """-> trackers {domain: (company, category)}, owners {domain: company}, cnames {name: target}"""
    if not os.path.exists(path):
        return {}, {}, {}
    t = _read_json(path)
    trackers = {}
    for d, v in t.get("trackers", {}).items():
        cats = set(v.get("categories") or [])
        owner = (v.get("owner") or {}).get("displayName") or (v.get("owner") or {}).get("name")
        if not owner:
            continue
        if cats & DDG_AD:
            cat = "Advertising"
        elif cats & DDG_ANALYTICS:
            cat = "Analytics"
        elif v.get("default") == "block":
            cat = "Advertising"
        else:
            continue
        trackers[d.lower()] = (owner, cat)
    names = {k: (v.get("displayName") or k) for k, v in t.get("entities", {}).items()}
    owners = {d.lower(): names.get(e, e) for d, e in t.get("domains", {}).items()}
    cnames = {k.lower(): v.lower() for k, v in t.get("cnames", {}).items()}
    return trackers, owners, cnames


def _load_apps(path):
    if not os.path.exists(path):
        return {}
    return {k.lower(): v for k, v in _read_json(path).items() if not k.startswith("_")}


class Lists:
    def __init__(self, exclude_companies=(), data_dir=DATA):
        self.excluded = sorted({c for c in exclude_companies if c})
        terms = [norm(c) for c in self.excluded]
        drop = lambda co: company_matches(co, terms)  # noqa: E731

        disc = _load_disconnect(os.path.join(data_dir, "services.json"))
        ddg, owners, cnames = _load_ddg(os.path.join(data_dir, "android-tds.json"))
        extra = dict(EXTRA)

        # domains any source attributes to an excluded company -> dropped everywhere
        bad = set()
        for src in (disc, ddg, extra):
            bad |= {d for d, (co, _) in src.items() if drop(co)}
        bad |= {d for d, co in owners.items() if drop(co)}
        self.excluded_domains = bad

        # company map (receipt), most specific source first: EXTRA > Disconnect > DDG
        self._company = {}
        self._tracking = set()
        for src, non_tracking in ((ddg, set()), (disc, set(PRIORITY) - DISCONNECT_TRACKING),
                                  (extra, EXTRA_NON_TRACKING)):
            for d, (co, cat) in src.items():
                if d in bad:
                    continue
                self._company[d] = (co, cat)
                if cat in non_tracking:
                    self._tracking.discard(d)
                else:
                    self._tracking.add(d)
        self._owner = {d: co for d, co in owners.items() if d not in bad}
        # DDG's known cloaked names: first-party name -> tracker target. Drop when the
        # target (or the name) belongs to an excluded company.
        self.cnames = {k: v for k, v in cnames.items()
                       if not self._in(k, bad) and not self._in(v, bad)}
        self.asn = AsnDB(os.path.join(data_dir, "ad_asn_ranges.tsv")
                         if data_dir != DATA else RANGES_PATH, drop_company=drop)
        self.apps = _load_apps(os.path.join(data_dir, "apps.json"))

    @staticmethod
    def _in(name, domset):
        return any(s in domset for s in _suffixes(name))

    # ---- lookups -------------------------------------------------------------
    def _first(self, name):
        """Most specific suffix that is in the company map or the allowlist."""
        for s in _suffixes(name):
            if s in ALLOWLIST and s not in self._company:
                return s, None
            if s in self._company:
                return s, self._company[s]
        return None, None

    def company(self, name):
        """(company, category) for the receipt, or None. Includes non-tracking companies."""
        _, hit = self._first(name)
        return hit

    def owner(self, name):
        """Company that owns the domain (tracker or not), or None."""
        hit = self.company(name)
        if hit:
            return hit[0]
        for s in _suffixes(name):
            if s in self._owner:
                return self._owner[s]
        return None

    def is_allowlisted(self, name):
        if self.is_tracker(name):
            return False
        return any(s in ALLOWLIST for s in _suffixes(name))

    def is_tracker(self, name):
        s, hit = self._first(name)
        return hit is not None and s in self._tracking

    def is_known(self, name):
        """On any list at all (tracker, known company, allowlist): not for the model to score."""
        s, _ = self._first(name)
        return s is not None

    def ip_owner(self, ip):
        return self.asn.lookup(ip)

    def cname_tracker(self, chain, extra_flagged=()):
        """First name in a CNAME chain (excluding chain[0], the queried name) that is a
        list tracker, a DDG-known cloaking target, or in extra_flagged (model flags)."""
        flagged = set(extra_flagged)
        names = list(chain or [])
        for n in names[1:]:
            n = n.lower().strip(".")
            if self.is_tracker(n):
                co, cat = self.company(n)
                return n, co, cat
            if any(s in flagged for s in _suffixes(n)):
                return n, None, "Suspected"
        if names:
            q = names[0].lower().strip(".")
            if q in self.cnames:
                t = self.cnames[q]
                hit = self.company(t)
                return t, (hit[0] if hit else self.owner(t)), (hit[1] if hit else "Advertising")
        return None

    def same_company(self, name, app_name):
        """True when `name` belongs to the app's own company (never block it)."""
        app = self.apps.get((app_name or "").lower())
        if not app:
            return False
        if any(s in {d.lower() for d in app.get("domains", [])} for s in _suffixes(name)):
            return True
        own = norm(app.get("company"))
        return bool(own) and norm(self.owner(name)) == own

    def domains_of(self, company):
        """Every list domain attributed to a company (for demo/eval ground truth)."""
        t = [norm(company)]
        return sorted({d for d, (co, _) in self._company.items() if company_matches(co, t)}
                      | {d for d, co in self._owner.items() if company_matches(co, t)})

    def companies(self):
        return sorted({co for co, _ in self._company.values()})


_cache = {}
_lock = threading.Lock()


def load(exclude_companies=(), data_dir=DATA):
    """Cached per exclusion set. Loading takes ~0.3 s, so don't call it per query."""
    key = (tuple(sorted({c for c in exclude_companies if c})), data_dir)
    with _lock:
        if key not in _cache:
            _cache[key] = Lists(key[0], data_dir)
        return _cache[key]


def demo_holdout(conn):
    """Companies currently removed for demo mode (settings.demo_holdout, comma-separated)."""
    try:
        row = conn.execute("SELECT value FROM settings WHERE key='demo_holdout'").fetchone()
    except sqlite3.Error:
        return []
    return [c.strip() for c in (row[0] if row and row[0] else "").split(",") if c.strip()]


def load_live(conn):
    """What digin_dns.py, score.py and the dashboard should call every few seconds:
    lists with the live demo holdout applied."""
    return load(exclude_companies=demo_holdout(conn))
