-- Tiny example rows so every team can build against the real shape before the box is live.
-- Load into a scratch DB:  sqlite3 /tmp/seed.db < db/schema.sql && sqlite3 /tmp/seed.db < db/seed.sql
INSERT INTO sessions(session_id, device, app_name, start_ts, end_ts) VALUES
 (1, '10.42.0.57', 'Block Blast', 1794700000.0, 1794700120.0),
 (2, '10.42.0.57', 'Instagram',   1794700200.0, NULL);
INSERT INTO raw_lookups(ts, device, session_id, qname, qtype, cname_chain, answer_ips, blocked, block_reason) VALUES
 (1794700001.2, '10.42.0.57', 1, 'ads.vungle.com',           'A', '[]', '["0.0.0.0"]', 1, 'list'),
 (1794700001.9, '10.42.0.57', 1, 'cdn-f.adsmoloco.com',      'A', '["cdn-f.adsmoloco.com.cdn.cloudflare.net"]', '["104.18.2.10"]', 0, NULL),
 (1794700003.4, '10.42.0.57', 1, 'metrics.someapp.com',      'A', '["someapp.tracker.net"]', '["0.0.0.0"]', 1, 'cname'),
 (1794700004.0, '10.42.0.57', 1, 'gw-x.us-east-1.pai-eas.aliyuncs.com', 'A', '[]', '["47.88.1.2"]', 0, NULL),
 (1794700201.0, '10.42.0.57', 2, 'i.instagram.com',          'A', '["dgw.c10r.facebook.com"]', '["157.240.254.11"]', 0, NULL);
INSERT INTO settings(key, value) VALUES ('shield','on'), ('demo_mode','off'), ('demo_holdout','');
INSERT INTO ml_flags(domain, score, reasons, status, model_version, flagged_ts, updated_ts) VALUES
 ('gw-x.us-east-1.pai-eas.aliyuncs.com', 0.91, '["seen in 5 unrelated apps","fires at launch"]', 'suspected', 'seed', 1794700010.0, NULL);
INSERT INTO raw_flows(ts, device, session_id, dst_ip, dst_port, proto, bytes_up, bytes_down, pkts_up, pkts_down, duration_s) VALUES
 (1794700005.0, '10.42.0.57', 1, '47.88.1.2', 443, 'tcp', 4210, 980, 9, 6, 1.4);
