import json
import unittest

from ml import features, lists
from ml_eval import import_dnsmasq as imp


def _db():
    c = imp.create_db(":memory:")
    c.execute("INSERT INTO sessions VALUES (1,'d','Block Blast',1000,2000)")
    rows = [(1001, "d", 1, "x.cdn-metrics.com", ["telemetry.ssp.inmobi.com"], ["1.1.1.1"]),
            (1002, "d", 1, "telemetry.ssp.inmobi.com", [], []),
            (1500, "d", 1, "ads.doubleclick.net", [], [])]
    for ts, dev, sid, q, ch, ips in rows:
        c.execute("INSERT INTO raw_lookups(ts,device,session_id,qname,cname_chain,answer_ips) "
                  "VALUES (?,?,?,?,?,?)", (ts, dev, sid, q, json.dumps(ch), json.dumps(ips)))
    return c


class TestFeatures(unittest.TestCase):
    def test_heldout_company_is_not_known(self):
        c = _db()
        full = features.build(c, lists.load())
        held = features.build(c, lists.load(exclude_companies=["InMobi"]))
        self.assertEqual(full["x.cdn-metrics.com"]["cname_to_tracker"], 1)
        self.assertEqual(held["x.cdn-metrics.com"]["cname_to_tracker"], 0)
        # co-occurrence must not count a held-out company's lookup as a "known tracker"
        self.assertEqual(held["x.cdn-metrics.com"]["co_occur_rate"], 0.0)
        self.assertEqual(full["x.cdn-metrics.com"]["co_occur_rate"], 1.0)

    def test_launch_and_apps(self):
        f = features.build(_db(), lists.load())["x.cdn-metrics.com"]
        self.assertEqual((f["launch_burst"], f["n_apps"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
