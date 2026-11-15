import json
import unittest

import numpy as np

from ml import lists, reasons, score
from ml_eval import import_dnsmasq as imp


class Stub:
    def __init__(self, p):
        self.p = p

    def predict_proba(self, X):
        return np.c_[1 - np.full(len(X), self.p), np.full(len(X), self.p)]


def _db():
    c = imp.create_db(":memory:")
    c.execute("INSERT INTO sessions VALUES (1,'d','Block Blast',1000,NULL)")
    for ts, q, ch in [(1001, "metrics.newapp.com", ["x.ssp.inmobi.com"]),
                      (1002, "ads.doubleclick.net", []), (1003, "hungrystudio.com", [])]:
        c.execute("INSERT INTO raw_lookups(ts,device,session_id,qname,cname_chain,answer_ips) "
                  "VALUES (?,?,?,?,?,?)", (ts, "d", 1, q, json.dumps(ch), "[]"))
    return c


class TestScore(unittest.TestCase):
    def test_auth_endpoints_never_scored(self):
        for d in ("login.aa.com", "eu-auth2.samsungosp.com", "accounts.google.com", "sso.example.com",
                  "oauth2.foo.net", "signin.aws.amazon.com"):
            self.assertTrue(score.is_auth_endpoint(d), d)
        for d in ("id5-sync.com", "authority-ads.com", "api.locketcamera.com", "collect.ureca-lab.com",
                  "a.bids.ws", "metrics.rapidedge.io"):
            self.assertFalse(score.is_auth_endpoint(d), d)

    def test_call_infrastructure_never_scored(self):
        for d in ("stun.ekiga.net", "stun1.l.google.com", "turn.example.com"):
            self.assertTrue(score.is_call_infra(d), d)
        for d in ("stunt-ads.com", "metrics.newapp.com", "turnkey.example.com"):
            self.assertFalse(score.is_call_infra(d), d)


    def test_only_unlisted_scored_and_reasons(self):
        c = _db()
        L = lists.load()
        s = score.score_domains(c, L, Stub(0.8))
        self.assertIn("metrics.newapp.com", s)
        self.assertNotIn("ads.doubleclick.net", s)  # on the blocklist: not ML's job
        n = score.write_flags(c, L, s, "t1", threshold=0.6)
        self.assertGreaterEqual(n, 1)
        row = c.execute("SELECT status, reasons FROM ml_flags WHERE domain='metrics.newapp.com'").fetchone()
        self.assertEqual(row[0], "suspected")
        self.assertTrue(any("CNAME" in r for r in json.loads(row[1])))

    def test_auto_block_and_dismissed_never_overwritten(self):
        c = _db()
        L = lists.load()
        score.write_flags(c, L, score.score_domains(c, L, Stub(0.99)), "t1", auto_threshold=0.97)
        self.assertEqual(c.execute("SELECT status FROM ml_flags WHERE domain='metrics.newapp.com'").fetchone()[0],
                         "auto_blocked")
        c.execute("UPDATE ml_flags SET status='dismissed' WHERE domain='metrics.newapp.com'")
        score.write_flags(c, L, score.score_domains(c, L, Stub(0.99)), "t2", auto_threshold=0.97)
        self.assertEqual(c.execute("SELECT status, model_version FROM ml_flags").fetchone(),
                         ("dismissed", "t1"))

    def test_reason_order(self):
        r = reasons.explain("d", {"n_apps": 7, "launch_burst": 1, "co_occur_rate": 0.9,
                                  "asn_ad_network": 1}, {"cname": None, "asn": {"AppLovin"}})
        self.assertEqual(r[0], "servers owned by AppLovin")
        self.assertEqual(len(r), 3)


    def test_persons_block_survives_rescoring(self):
        """Dashboard Block sets status='auto_blocked'; Shield reads it. A pass must not undo it,
        and must not touch a Dismiss either."""
        c = _db()
        L = lists.load()
        s = score.score_domains(c, L, Stub(0.8))
        score.write_flags(c, L, s, "t1", threshold=0.6)
        c.execute("UPDATE ml_flags SET status='auto_blocked' WHERE domain='metrics.newapp.com'")
        c.commit()
        score.write_flags(c, L, s, "t1", threshold=0.6)   # auto-block is off: must keep the person's choice
        self.assertEqual(c.execute("SELECT status FROM ml_flags WHERE domain='metrics.newapp.com'")
                         .fetchone()[0], "auto_blocked")
        c.execute("UPDATE ml_flags SET status='dismissed' WHERE domain='metrics.newapp.com'")
        c.commit()
        score.write_flags(c, L, s, "t1", threshold=0.6)
        self.assertEqual(c.execute("SELECT status FROM ml_flags WHERE domain='metrics.newapp.com'")
                         .fetchone()[0], "dismissed")


if __name__ == "__main__":
    unittest.main()
