"""Tests for ml/lists.py, ml/asn.py, ml/cname.py, ml/demo.py, ml_eval/.

    python3 -m unittest discover tests -v
"""
import json
import os
import sqlite3
import tempfile
import unittest

from ml import cname, demo, lists
from ml_eval import evaluate

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SCHEMA = open(os.path.join(ROOT, "db", "schema.sql")).read()

INMOBI_IP = "103.251.108.10"
CRITEO_IP = "74.119.117.5"


def make_db():
    """Tiny seed DB: one session with list trackers, a held-out company, a cloaked
    tracker, an ad-network-hosted unknown name and a content domain."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    with conn:
        conn.execute("INSERT INTO sessions VALUES (1,'10.42.0.57','Block Blast',1000,NULL)")
        rows = [
            (1001, "googleads.g.doubleclick.net", None, ["142.250.1.1"]),
            (1002, "api.inmobi.com", None, [INMOBI_IP]),
            (1002.5, "sdk.inmobi.com", ["sdk.inmobi.com", "x.doubleclick.net"], [INMOBI_IP]),
            (1003, "metrics.someapp.com", ["metrics.someapp.com", "someapp.sc.omtrdc.net"], ["1.2.3.4"]),
            (1004, "px.newadco.io", None, [CRITEO_IP]),
            (1005, "api.hungrystudio.com", None, ["5.6.7.8"]),
            (1006, "s2.flylax.com", None, ["9.9.9.1"]),
        ]
        for ts, q, chain, ips in rows:
            conn.execute("INSERT INTO raw_lookups(ts, device, session_id, qname, cname_chain, answer_ips) "
                         "VALUES (?,?,?,?,?,?)",
                         (ts, "10.42.0.57", 1, q, json.dumps(chain) if chain else None, json.dumps(ips)))
    return conn, path


class TestLists(unittest.TestCase):
    def setUp(self):
        self.L = lists.load()

    def test_suffix_and_category(self):
        self.assertEqual(self.L.company("a.b.doubleclick.net"), ("Google", "Advertising"))
        self.assertTrue(self.L.is_tracker("api.inmobi.com"))
        self.assertTrue(self.L.is_tracker("ads.vungle.com"))
        self.assertIsNone(self.L.company("foo.example.com"))

    def test_allowlist_specific_tracker_wins(self):
        self.assertTrue(self.L.is_tracker("firebaselogging.googleapis.com"))
        self.assertFalse(self.L.is_allowlisted("firebaselogging.googleapis.com"))
        self.assertTrue(self.L.is_allowlisted("www.googleapis.com"))
        self.assertTrue(self.L.is_allowlisted("connectivitycheck.gstatic.com"))
        self.assertFalse(self.L.is_tracker("cdn.akamaized.net"))

    def test_exclusion_removes_everywhere(self):
        E = lists.load(["InMobi", "Vungle"])
        for d in ("api.inmobi.com", "inmobicdn.net", "ads.vungle.com", "liftoff.io"):
            self.assertFalse(E.is_known(d), d)
            self.assertFalse(E.is_tracker(d), d)
        self.assertIsNone(E.ip_owner(INMOBI_IP))
        self.assertEqual(self.L.ip_owner(INMOBI_IP)[1], "InMobi")
        self.assertTrue(E.is_tracker("doubleclick.net"))  # others untouched

    def test_exclusion_removes_cname_targets(self):
        self.assertIn("s2.flylax.com", self.L.cnames)
        E = lists.load(["Adform"])
        self.assertNotIn("s2.flylax.com", E.cnames)
        self.assertFalse(E.is_tracker("a1.adform.net"))

    def test_same_company(self):
        self.assertTrue(self.L.same_company("api.tiktokv.com", "TikTok"))
        self.assertFalse(self.L.same_company("doubleclick.net", "TikTok"))

    def test_load_is_cached(self):
        self.assertIs(lists.load(["InMobi"]), lists.load(["InMobi"]))


class TestCname(unittest.TestCase):
    def test_uncloak(self):
        L = lists.load()
        h = cname.uncloak("metrics.someapp.com", ["someapp.sc.omtrdc.net"], L)
        self.assertIsNotNone(h)
        self.assertEqual(h.target, "someapp.sc.omtrdc.net")
        self.assertIsNone(cname.uncloak("www.example.com", ["example.cdn.cloudflare.net"], L))
        # a name already on a list is Shield's job, not a flag
        self.assertIsNone(cname.uncloak("ad.doubleclick.net", ["x.doubleclick.net"], L))
        # model-flagged target counts too
        self.assertIsNotNone(cname.uncloak("t.shop.com", ["edge.newtracker.io"], L,
                                           flagged=["newtracker.io"]))

    def test_rule_flags_and_write(self):
        conn, path = make_db()
        try:
            L = lists.load()
            flags = cname.rule_flags(conn, L)
            self.assertIn("metrics.someapp.com", flags)
            self.assertIn("px.newadco.io", flags)           # Criteo IPs
            self.assertIn("ad network", flags["px.newadco.io"][1][0])
            self.assertNotIn("googleads.g.doubleclick.net", flags)
            self.assertNotIn("api.hungrystudio.com", flags)
            # a model row is never overwritten by rules
            conn.execute("INSERT INTO ml_flags VALUES ('px.newadco.io',0.8,'[\"m\"]','suspected','m1',1,1)")
            conn.commit()
            cname.write_flags(conn, flags)
            row = conn.execute("SELECT model_version FROM ml_flags WHERE domain='px.newadco.io'").fetchone()
            self.assertEqual(row[0], "m1")
            row = conn.execute("SELECT status, model_version FROM ml_flags WHERE domain='metrics.someapp.com'").fetchone()
            self.assertEqual(row, ("suspected", "rules-v1"))
            for (s,) in conn.execute("SELECT score FROM ml_flags"):
                self.assertLess(s, 0.97)  # rules never auto-block
        finally:
            os.unlink(path)


class TestDemo(unittest.TestCase):
    def test_set_status_clear(self):
        conn, path = make_db()
        try:
            conn.execute("INSERT INTO ml_flags VALUES ('api.inmobi.com',0.9,'[]','suspected','m1',1,1)")
            conn.commit()
            s = demo.set_holdout(conn, ["InMobi"])
            self.assertEqual(lists.demo_holdout(conn), ["InMobi"])
            self.assertFalse(lists.load_live(conn).is_known("api.inmobi.com"))
            c = s["companies"][0]
            self.assertEqual(c["seen"], 2)
            self.assertEqual(c["flagged"], 0)      # old flag cleared
            self.assertEqual(c["still_on_a_list"], [])
            demo.clear(conn)
            self.assertEqual(lists.demo_holdout(conn), [])
            self.assertTrue(lists.load_live(conn).is_tracker("api.inmobi.com"))
        finally:
            os.unlink(path)

    def test_unknown_company_refused(self):
        conn, path = make_db()
        try:
            with self.assertRaises(SystemExit):
                demo.set_holdout(conn, ["NoSuchAdCo"])
        finally:
            os.unlink(path)


class TestEval(unittest.TestCase):
    def test_grouped_holdout(self):
        conn, path = make_db()
        try:
            res = evaluate.evaluate(conn, ["InMobi"], {"hungrystudio.com"},
                                    scorers=[("list-only", evaluate.score_list),
                                             ("rules-only", evaluate.score_rules)])
            self.assertEqual(res["positives"], {"InMobi": 2})
            self.assertEqual(res["n_neg"], 1)
            self.assertEqual(res["leaks"], [])
            by = {s["name"]: s for s in res["scorers"]}
            self.assertEqual(by["list-only"]["tp"], 0)     # no leakage through lists
            # rules: InMobi's own ASN is excluded, so only the CNAME-to-doubleclick name is caught
            self.assertEqual(by["rules-only"]["caught"], ["sdk.inmobi.com"])
            self.assertEqual(by["rules-only"]["fp"], 0)
            self.assertIn("1 of 2", evaluate.table(res))
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
