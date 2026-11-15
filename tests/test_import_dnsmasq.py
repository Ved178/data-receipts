import sqlite3
import unittest

from ml_eval import import_dnsmasq as imp

LOG = """\
Nov 14 23:30:00 dnsmasq[1]: query[A] old.example from 10.42.0.9
Nov 14 23:36:27 dnsmasq[2]: query[A] metrics.app.com from 10.42.0.33
Nov 14 23:36:27 dnsmasq[2]: forwarded metrics.app.com to 8.8.8.8
Nov 14 23:36:27 dnsmasq[2]: reply metrics.app.com is <CNAME>
Nov 14 23:36:27 dnsmasq[2]: reply hop.tracker.net is <CNAME>
Nov 14 23:36:27 dnsmasq[2]: reply end.tracker.net is 1.2.3.4
Nov 14 23:36:28 dnsmasq[2]: query[A] ads.doubleclick.net from 10.42.0.33
Nov 14 23:36:28 dnsmasq[2]: config ads.doubleclick.net is 0.0.0.0
Nov 14 23:36:29 dnsmasq[2]: query[HTTPS] x.com from 10.42.0.33
Nov 14 23:36:30 dnsmasq[2]: query[A] plain.com from 10.42.0.33
Nov 14 23:36:30 dnsmasq[2]: reply plain.com is NODATA-IPv6
Nov 14 23:36:30 dnsmasq[2]: reply plain.com is 5.6.7.8
"""


class TestImport(unittest.TestCase):
    def test_parse(self):
        rows = {r["qname"]: r for r in imp.parse_log(LOG.splitlines(), 2026)}
        self.assertEqual(set(rows), {"metrics.app.com", "ads.doubleclick.net", "plain.com"})
        m = rows["metrics.app.com"]
        self.assertEqual(m["cname_chain"], ["hop.tracker.net", "end.tracker.net"])
        self.assertEqual(m["answer_ips"], ["1.2.3.4"])
        self.assertEqual(rows["ads.doubleclick.net"]["blocked"], 1)
        self.assertEqual(rows["plain.com"]["answer_ips"], ["5.6.7.8"])
        self.assertEqual(rows["plain.com"]["cname_chain"], [])

    def test_sessions_and_overlap(self):
        conn = imp.create_db(":memory:")
        st = imp.import_log(conn, LOG.splitlines(), 2026, None,
                            ["A=23:36-23:40", "B=23:36-23:40"])
        self.assertEqual(st["ambiguous"], 3)
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM raw_lookups WHERE session_id IS NOT NULL").fetchone()[0], 0)
        self.assertEqual(imp.parse_sessions(["A=00:01-00:02"], 2026, 86400 * 3)[0][1:], (259260, 259320))


if __name__ == "__main__":
    unittest.main()
