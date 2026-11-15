# Aarav's recorded sessions (Sat Nov 14, real iPhone, digin.db schema)

Open with `sqlite3 <file>` or Python `sqlite3`. Schema = `db/schema.sql`. `raw_flows` is empty in all three.

## aarav_laptop_shield_off.db — use for training (nothing blocked)
Laptop as the box (Plan B, UWNet). 1165 lookups, 8 sessions (~2 min each):
Block Blast 356, knife hit 153, instagram 16, linkdin 43, snapchat 20, 234playergames 32 (36 s),
colourswitch 177, amazon 99.
- session_id NULL rows = phone background traffic between sessions.
- device 127.0.0.1 = test lookups from the laptop; the phone is the 10.140.x.x device.
- Instagram/Snapchat are small: they mostly connect to raw IPs, not DNS.

## aarav_laptop_shield_on.db — Shield ON (blocked=1 rows were answered 0.0.0.0)
700 lookups: Block Blast 163 (87 blocked), knife hit 83 (71), clash of clans 26 (16), nytimes 9 (3).

## aarav_board2_blockblast.db — on the Uno Q (board #2, DigIn2, Pranshu's hotspot + our logger)
1452 lookups. Phone = 10.42.0.211; 10.42.0.1 / 127.0.0.1 = test lookups from the box.
| session | Shield | length | lookups | blocked |
|---|---|---|---|---|
| 1 Block Blast | OFF | 142 s | 395 | 0 |
| 2 Block Blast | ON | 134 s | 109 | 62 |
| 3, 4 | — | false starts, 0 lookups (ignore) | | |
| 5 Block Blast | ON (+ Pangle blocks) | 188 s | 227 | 134 |
Rows after session 5 include a reboot test and a few web pages (bbc.com).

**Key finding (session 5):** with the big networks blocked, Block Blast falls back to ad networks on
no list: Bidease (creative-cdn77.bidease.com, ntusus.bidease.com), StartApp (ssp-*.startappservice.com,
startappelb.com) and Aarki (spire.aarki.net). Full-screen ads after losing at 59 s, 89 s and 159 s
line up with those lookups. Good demo targets for the model.

Other unlisted ad networks seen on the laptop: svr-algorix.com / bid-algorix.com, 4dvertible.com,
ipxserve.com, ampaeservices.com, moloco.rtb.afafb.com (Moloco under Block Blast's own domain).
