"""IP -> network owner -> ad/analytics company (offline).

Data: data/lists/ad_asn_ranges.tsv, built from iptoasn.com's ip2asn-v4.tsv.gz by
    python3 -m ml.asn build /path/to/ip2asn-v4.tsv.gz
It keeps only the ranges announced by the ad/analytics networks in AD_ASNS, so the
file is small enough to commit and the box never needs to be online.

If ml/data/GeoLite2-ASN.mmdb exists it is used instead (IPv4 + IPv6), read by the
stdlib reader in ml/mmdb.py; the ASN -> company mapping is still AD_ASNS.

Big clouds (Google 15169, AWS, Cloudflare, Akamai...) are deliberately NOT here:
they host content and ads alike, so "IP is Google's" says nothing.

Exclusions (CLAUDE.md §6.1) are applied by ml.lists.load(), which builds an
AsnDB with the held-out companies removed. Use that, not AsnDB directly.
"""
import bisect
import gzip
import ipaddress
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RANGES_PATH = os.path.join(HERE, "..", "data", "lists", "ad_asn_ranges.tsv")
# GeoLite2-ASN (Ved's download; gitignored on main, copy it onto the box). Preferred
# when present: covers IPv6 and is newer. The TSV is the fallback that's always in git.
MMDB_PATHS = [os.path.join(HERE, "data", "GeoLite2-ASN.mmdb"),
              os.path.join(HERE, "..", "data", "lists", "GeoLite2-ASN.mmdb")]

# ASN -> (company, category). Company names match the EXTRA / Disconnect names
# closely enough for ml.lists.company_matches() to exclude them together.
AD_ASNS = {
    133025: ("InMobi", "Advertising"),
    394600: ("AppLovin", "Advertising"),
    204032: ("Unity Ads", "Advertising"),        # Unity Technologies ApS
    19750: ("Criteo", "Advertising"),
    44788: ("Criteo", "Advertising"),
    55569: ("Criteo", "Advertising"),
    198622: ("Adform", "Advertising"),
    200478: ("Taboola", "Advertising"),
    206614: ("Taboola", "Advertising"),
    22075: ("Outbrain", "Advertising"),
    26459: ("The Trade Desk", "Advertising"),
    26667: ("Magnite (Rubicon Project)", "Advertising"),
    27281: ("Quantcast", "Advertising"),
    27381: ("Index Exchange", "Advertising"),
    29990: ("Xandr (AppNexus)", "Advertising"),
    62713: ("PubMatic", "Advertising"),
    56396: ("Nexxen (Amobee)", "Advertising"),
    205184: ("Adjust (AppLovin)", "Analytics"),
    61273: ("Adjust (AppLovin)", "Analytics"),
    396535: ("Adjust (AppLovin)", "Analytics"),
}


def build(src_gz, out=RANGES_PATH):
    n = 0
    with gzip.open(src_gz, "rt", errors="replace") as f, open(out, "w") as w:
        w.write("# start\tend\tasn\tcompany\tcategory  (built from iptoasn.com ip2asn-v4)\n")
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 3:
                continue
            try:
                asn = int(p[2])
            except ValueError:
                continue
            if asn in AD_ASNS:
                co, cat = AD_ASNS[asn]
                w.write(f"{p[0]}\t{p[1]}\t{asn}\t{co}\t{cat}\n")
                n += 1
    return n


class AsnDB:
    """IP -> (asn, company, category) for ad/analytics networks only, else None.
    Uses GeoLite2-ASN when available, else the committed IPv4 TSV."""

    def __init__(self, path=RANGES_PATH, drop_company=None, mmdb_path=None):
        self.starts, self.rows = [], []
        self.asns = {a: v for a, v in AD_ASNS.items()
                     if not (drop_company and drop_company(v[0]))}
        self.mmdb = None
        self.source = "none"
        for p in ([mmdb_path] if mmdb_path else MMDB_PATHS):
            if p and os.path.exists(p):
                try:
                    from ml.mmdb import MMDB
                    self.mmdb = MMDB(p)
                    self.source = os.path.basename(p)
                    break
                except (OSError, ValueError) as e:
                    print(f"asn: can't read {p}: {e}; using TSV", file=sys.stderr)
        if self.mmdb is None and os.path.exists(path):
            rows = []
            with open(path) as f:
                for line in f:
                    if line.startswith("#") or not line.strip():
                        continue
                    s, e, asn, co, cat = line.rstrip("\n").split("\t")
                    if drop_company and drop_company(co):
                        continue
                    rows.append((int(ipaddress.IPv4Address(s)), int(ipaddress.IPv4Address(e)),
                                 int(asn), co, cat))
            rows.sort()
            self.starts = [r[0] for r in rows]
            self.rows = rows
            self.source = os.path.basename(path)

    def __len__(self):
        return len(self.asns) if self.mmdb else len(self.rows)

    def asn_of(self, ip):
        """(asn, organization) from GeoLite2 for any IP, or None (TSV mode: None)."""
        if not self.mmdb:
            return None
        r = self.mmdb.get(ip)
        if not r:
            return None
        return r.get("autonomous_system_number"), r.get("autonomous_system_organization")

    def lookup(self, ip):
        if self.mmdb:
            r = self.asn_of(ip)
            if r and r[0] in self.asns:
                co, cat = self.asns[r[0]]
                return r[0], co, cat
            return None
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            return None
        if a.version != 4:
            return None
        x = int(a)
        i = bisect.bisect_right(self.starts, x) - 1
        if i >= 0 and self.rows[i][0] <= x <= self.rows[i][1]:
            _, _, asn, co, cat = self.rows[i]
            return asn, co, cat
        return None


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "build":
        print(f"wrote {build(sys.argv[2])} ranges to {RANGES_PATH}")
    elif len(sys.argv) == 3 and sys.argv[1] == "ip":
        db = AsnDB()
        print(db.source, db.asn_of(sys.argv[2]), db.lookup(sys.argv[2]))
    else:
        print("usage: python3 -m ml.asn build ip2asn-v4.tsv.gz | ip 1.2.3.4")
