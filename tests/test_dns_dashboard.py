"""Tests for dns/digin_dns.py (logger + Shield), dashboard/app.py and deploy/ scripts (Aarav).
No network needed: upstream answers are faked.

    python3 -m unittest discover tests -v
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "dns"))
sys.path.insert(0, os.path.join(ROOT, "dashboard"))

try:
    from dnslib import QTYPE, RR, A, CNAME, DNSRecord
    import digin_dns
except ImportError:  # dnslib missing: skip the DNS tests, keep the rest
    digin_dns = None


class FakeHandler:
    def __init__(self, ip):
        self.client_address = (ip, 5353)


@unittest.skipIf(digin_dns is None, "dnslib not installed")
class ShieldTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "digin.db")
        digin_dns.connect(self.db, init=True).close()
        self.r = digin_dns.DigInResolver(self.db, ["192.0.2.1"], digin_dns.BLOCKLIST_PATH,
                                         digin_dns.ALLOWLIST_PATH, timeout=0.2)
        self.r.state["shield"] = True

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def blocked(self, name, chain=(), app=None):
        return self.r.block_reason(name, list(chain), app)

    def test_shield_off_blocks_nothing(self):
        self.r.state["shield"] = False
        self.assertIsNone(self.blocked("ads.vungle.com"))

    def test_known_ad_networks_blocked(self):
        for name in ["ads.vungle.com", "d.applovin.com", "pubads.g.doubleclick.net",
                     "api16-access-ttp.tiktokpangle-b.us", "match.adsrvr.org", "an.facebook.com"]:
            self.assertEqual(self.blocked(name), "list", name)

    def test_disguised_moloco_blocked_but_game_works(self):
        # Moloco hides under Block Blast's own domain; the blocklist entry beats the allowlist.
        self.assertEqual(self.blocked("moloco.rtb.afafb.com"), "list")
        self.assertEqual(self.blocked("global.moloco.map.fastly.net"), "list")
        self.assertIsNone(self.blocked("hella-ping.afafb.com"))
        self.assertIsNone(self.blocked("www.fastly.net"))

    def test_phone_and_facebook_app_keep_working(self):
        for name in ["connectivitycheck.gstatic.com", "captive.apple.com", "www.google.com",
                     "www.facebook.com", "graph.facebook.com", "fbsbx.com", "nytimes.com"]:
            self.assertIsNone(self.blocked(name), name)

    def test_ml_auto_blocked_flag(self):
        self.r.state["ml_blocked"] = {"bidease.com"}
        self.assertEqual(self.blocked("ntusus.bidease.com"), "ml")

    def test_resolve_answers_zero_and_logs_row(self):
        def fake_forward(request):
            reply = request.reply()
            reply.add_answer(RR(request.q.qname, QTYPE.A, rdata=A("203.0.113.9"), ttl=60))
            return reply
        self.r.forward = fake_forward
        self.r.state_ts = time.time()  # keep the shield=True state set in setUp
        ans = self.r.resolve(DNSRecord.question("ads.vungle.com"), FakeHandler("10.42.0.50"))
        self.assertEqual(str(ans.rr[0].rdata), "0.0.0.0")
        ok = self.r.resolve(DNSRecord.question("nytimes.com"), FakeHandler("10.42.0.50"))
        self.assertEqual(str(ok.rr[0].rdata), "203.0.113.9")
        conn = sqlite3.connect(self.db)
        for _ in range(50):  # writes happen off the answer path (writer thread)
            rows = conn.execute("SELECT qname, device, blocked, block_reason, answer_ips"
                                " FROM raw_lookups ORDER BY id").fetchall()
            if len(rows) == 2:
                break
            time.sleep(0.05)
        self.assertEqual(rows[0][:4], ("ads.vungle.com", "10.42.0.50", 1, "list"))
        self.assertEqual((rows[1][0], rows[1][2], json.loads(rows[1][4])), ("nytimes.com", 0, ["203.0.113.9"]))

    def test_private_relay_canary_nxdomain(self):
        ans = self.r.resolve(DNSRecord.question("mask.icloud.com"), FakeHandler("10.42.0.50"))
        self.assertEqual(ans.header.rcode, 3)  # NXDOMAIN


class DashboardTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "digin.db")
        conn = sqlite3.connect(self.db)
        conn.executescript(open(os.path.join(ROOT, "db", "schema.sql")).read())
        conn.executescript(open(os.path.join(ROOT, "db", "seed.sql")).read())
        conn.close()
        import app
        self.app = app
        app.DB = self.db

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_receipt_groups_by_company(self):
        s = self.app.state("all")
        self.assertGreater(s["totals"]["lookups"], 0)
        companies = {c["company"] for g in s["receipt"] for c in g["companies"]}
        self.assertTrue(companies, "receipt should name at least one company")
        self.assertIn(s["shield"], ("on", "off"))

    def test_classify_by_function_not_owner(self):
        # Amazon owns both; its ad servers are Advertising, its shop is not.
        self.assertEqual(self.app.classify("aax.advertising.a2z.com", {}), ("Amazon", "Advertising"))
        self.assertEqual(self.app.classify("c.amazon-adsystem.com", {}), ("Amazon", "Advertising"))
        self.assertEqual(self.app.classify("ad-host-backup-america.oss-us-west-1.aliyuncs.com", {})[1], "Advertising")

    def test_cname_to_cloud_host_is_not_ownership(self):
        self.assertIsNone(self.app.classify("thinkingdata.cn", {}, '["alb-x.cn-shanghai.alb.aliyuncs.com"]'))
        self.assertEqual(self.app.classify("dvrtr.innovid.com", {}, '["vast.doubleverify.com"]')[0], "Innovid")

    def test_readable_company_names(self):
        self.assertEqual(self.app.classify("cdn.jsdelivr.net", {})[0], "jsDelivr (code CDN)")

    def test_background_apps_not_counted(self):
        import company
        self.assertEqual(company.background(["gateway.icloud.com"], "Block Blast"), "Apple / iCloud")
        self.assertEqual(company.background(["g.whatsapp.net"], "Block Blast"), "WhatsApp")
        self.assertIsNone(company.background(["aws.api.snapchat.com"], "Snapchat"))  # the tested app itself
        self.assertIsNone(company.background(["ads.vungle.com"], "Block Blast"))
        conn = sqlite3.connect(self.db)
        sid = conn.execute("SELECT session_id FROM sessions LIMIT 1").fetchone()[0]
        conn.execute("INSERT INTO raw_lookups(ts, device, session_id, qname) VALUES (?,?,?,?)",
                     (time.time(), "10.42.0.9", sid, "g.whatsapp.net"))
        conn.commit()
        s = self.app.state(str(sid))
        self.assertIn("WhatsApp", [b["label"] for b in s["background"]])
        self.assertNotIn("WhatsApp", [c["company"] for g in s["receipt"] for c in g["companies"]])

    def test_tracker_web_and_flags_load(self):
        web = self.app.tracker_web()
        self.assertIn("edges", web)
        self.assertIsInstance(self.app.flags_map(self.app.db()), dict)


class DetectorTest(unittest.TestCase):
    """rules-v1: flags only names that are new with Shield ON, after an ad company was blocked."""

    def setUp(self):
        import detector
        self.D = detector
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "digin.db")
        conn = sqlite3.connect(self.db)
        conn.executescript(open(os.path.join(ROOT, "db", "schema.sql")).read())
        add = lambda sid, t, q, chain="[]", b=0: conn.execute(  # noqa: E731
            "INSERT INTO raw_lookups(ts, device, session_id, qname, cname_chain, blocked, block_reason)"
            " VALUES (?,?,?,?,?,?,?)", (1000 * sid + t, "10.42.0.9", sid, q, chain, b, "list" if b else None))
        conn.execute("INSERT INTO sessions VALUES (1, '', 'Test Game', 1000, 1100)")   # Shield OFF
        conn.execute("INSERT INTO sessions VALUES (2, '', 'Test Game', 2000, 2100)")   # Shield ON
        add(1, 1, "api.testgame.com")
        add(1, 2, "ads.vungle.com")
        add(1, 3, "old-adnet.example")                      # seen with Shield OFF -> never new
        add(2, 1, "early-thing.example")                    # before the first block -> not judged
        add(2, 2, "ads.vungle.com", b=1)                    # Shield blocks an ad company
        add(2, 3, "api.testgame.com")
        add(2, 4, "old-adnet.example")
        add(2, 5, "ssp-win.newadnet.io")                    # new + ad words -> flagged
        add(2, 6, "img.contentthing.org")                   # new, no ad words -> flagged, lower score
        add(2, 7, "d1234.cloudfront.net")                   # plain CDN -> never
        add(2, 8, "x.gstatic.com")                          # allowlisted -> never
        add(2, 9, "gateway.icloud.com")                     # phone background -> never
        add(2, 10, "doubleclick.net")                       # already on a list -> never (Shield's job)
        add(2, 11, "trk.cloakhost.net", '["ads.vungle.com"]')  # CNAME to a known tracker -> flagged, high
        add(2, 12, "kapublicapiprod.azurewebsites.net",        # game's own API on Azure -> never (Knife Hit)
            '["waws-prod-am2-287.westeurope.cloudapp.azure.com"]')
        conn.commit()
        self.conn = conn

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_flags_only_new_after_block(self):
        flags = {f["domain"]: f for f in self.D.detect(self.conn)}
        self.assertEqual(set(flags), {"newadnet.io", "contentthing.org", "cloakhost.net"})
        self.assertGreater(flags["newadnet.io"]["score"], flags["contentthing.org"]["score"])
        self.assertGreater(flags["cloakhost.net"]["score"], flags["contentthing.org"]["score"])
        self.assertTrue(any("ssp" in r for r in flags["newadnet.io"]["reasons"]))
        self.assertTrue(any("Shield-OFF" in r for r in flags["contentthing.org"]["reasons"]))
        for f in flags.values():
            self.assertTrue(0 <= f["score"] <= 1)

    def test_no_baseline_no_flags(self):
        self.conn.execute("DELETE FROM raw_lookups WHERE session_id=1")
        self.assertEqual(self.D.detect(self.conn), [])

    def test_never_overwrites_other_models_and_keeps_user_choice(self):
        self.conn.execute("INSERT INTO ml_flags VALUES ('newadnet.io', 0.99, '[\"ved\"]', 'auto_blocked', 'hgb-v2', 1, 1)")
        self.conn.commit()
        self.D.write_flags(self.conn, self.D.detect(self.conn))
        row = self.conn.execute("SELECT score, status, model_version FROM ml_flags WHERE domain='newadnet.io'").fetchone()
        self.assertEqual(row, (0.99, "auto_blocked", "hgb-v2"))
        self.conn.execute("UPDATE ml_flags SET status='dismissed' WHERE domain='contentthing.org'")
        self.conn.commit()
        self.D.write_flags(self.conn, self.D.detect(self.conn))
        row = self.conn.execute("SELECT status, model_version FROM ml_flags WHERE domain='contentthing.org'").fetchone()
        self.assertEqual(row, ("dismissed", "rules-v1"))

    def test_stale_own_suggestions_removed(self):
        self.conn.execute("INSERT INTO ml_flags VALUES ('azure.com', 0.5, '[]', 'suspected', 'rules-v1', 1, 1)")
        self.conn.execute("INSERT INTO ml_flags VALUES ('other.io', 0.9, '[]', 'suspected', 'hgb-v2', 1, 1)")
        self.conn.commit()
        self.D.write_flags(self.conn, self.D.detect(self.conn))
        left = {r[0] for r in self.conn.execute("SELECT domain FROM ml_flags")}
        self.assertNotIn("azure.com", left)          # our stale suggestion: gone
        self.assertIn("other.io", left)              # another model's row: untouched
        self.assertIn("newadnet.io", left)


class DeployScriptsTest(unittest.TestCase):
    def test_shell_syntax(self):
        for name in ["start_box.sh", "fallback_dnsmasq.sh"]:
            r = subprocess.run(["bash", "-n", os.path.join(ROOT, "deploy", name)], capture_output=True)
            self.assertEqual(r.returncode, 0, r.stderr.decode())

    def test_never_overwrites_pranshus_dnsmasq_conf(self):
        for name in ["start_box.sh", "fallback_dnsmasq.sh"]:
            text = open(os.path.join(ROOT, "deploy", name)).read()
            self.assertNotIn("dnsmasq-shared.d/digin.conf", text, name)


if __name__ == "__main__":
    unittest.main()
