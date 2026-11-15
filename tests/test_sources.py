import json
import math
import os
import tempfile
import unittest

from ml import features, lists
from ml_eval import import_dnsmasq as imp
from ml_eval.merge_dbs import merge


def _src(path, chain):
    c = imp.create_db(path)
    c.execute("INSERT INTO sessions VALUES (1,'d','App',1000,2000)")
    c.execute("INSERT INTO raw_lookups(ts,device,session_id,qname,cname_chain,answer_ips) "
              "VALUES (1001,'d',1,'api.example-app.com',?,'[]')", (chain,))
    c.execute("INSERT INTO raw_flows(ts,device,session_id,dst_ip,bytes_up,bytes_down) "
              "VALUES (1001,'d',1,'1.1.1.1',10,20)")
    c.commit()
    c.close()


class TestSources(unittest.TestCase):
    def test_null_chain_is_unknown_not_zero(self):
        with tempfile.TemporaryDirectory() as d:
            _src(os.path.join(d, "a.db"), None)          # PCAPdroid-style: no CNAME data
            _src(os.path.join(d, "b.db"), json.dumps([]))  # DNS-style: known, no CNAME
            import sqlite3
            for name, known in (("a.db", False), ("b.db", True)):
                f = features.build(sqlite3.connect(os.path.join(d, name)), lists.load())
                v = f["api.example-app.com"]["cname_depth"]
                self.assertEqual(not math.isnan(v), known)

    def test_merge_renumbers_sessions(self):
        with tempfile.TemporaryDirectory() as d:
            _src(os.path.join(d, "a.db"), None)
            _src(os.path.join(d, "b.db"), "[]")
            st = merge(os.path.join(d, "m.db"), [os.path.join(d, "a.db"), os.path.join(d, "b.db")])
            self.assertEqual(st, {"sessions": 2, "raw_lookups": 2, "raw_flows": 2})

    def test_infrastructure_allowlist_loaded(self):
        L = lists.load()
        self.assertTrue(L.is_allowlisted("a1.aaplimg.com"))
        self.assertFalse(L.is_allowlisted("ads.inmobi.com"))


if __name__ == "__main__":
    unittest.main()
