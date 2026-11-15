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




class TestRoutedModel(unittest.TestCase):
    def test_rows_without_flow_data_use_the_noflow_model(self):
        import numpy as np
        from ml import train
        rng = np.random.default_rng(0)
        n, k = 120, len(features.FEATURES)
        X = rng.normal(size=(n, k))
        y = (X[:, 0] > 0).astype(int)
        flow = [i for i, f in enumerate(features.FEATURES) if f.startswith("flow_")]
        X[: n // 2][:, flow] = np.nan            # half the rows have no raw_flows
        X[: n // 2, flow] = np.nan
        m = train.Model(X, y)
        self.assertIsNotNone(m.clf1)
        p = m.predict_proba(X)
        self.assertEqual(p.shape, (n, 2))
        # a no-flow row's score must not change when flow columns are filled in elsewhere
        a = m.predict_proba(X[:1])[0, 1]
        self.assertFalse(np.isnan(a))
        allnan = X.copy(); allnan[:, flow] = np.nan
        self.assertEqual(train.Model(allnan, y).clf1, None)  # no flow data at all: still works


if __name__ == "__main__":
    unittest.main()
