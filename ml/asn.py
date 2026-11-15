"""IP -> network owner -> ad/analytics company (offline).

Data: data/lists/ad_asn_ranges.tsv, built from iptoasn.com's ip2asn-v4.tsv.gz by
    python3 -m ml.asn build /path/to/ip2asn-v4.tsv.gz
It keeps only the ranges announced by the ad/analytics networks in AD_ASNS, so the
file is small enough to commit and the box never needs to be online.

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
    """Sorted IPv4 ranges -> (asn, company, category). IPv6 returns None for now."""

    def __init__(self, path=RANGES_PATH, drop_company=None):
        self.starts, self.rows = [], []
        if not os.path.exists(path):
            return
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

    def __len__(self):
        return len(self.rows)

    def lookup(self, ip):
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
        print(AsnDB().lookup(sys.argv[2]))
    else:
        print("usage: python3 -m ml.asn build ip2asn-v4.tsv.gz | ip 1.2.3.4")
