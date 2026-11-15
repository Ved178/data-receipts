"""Minimal stdlib reader for MaxMind .mmdb files (GeoLite2-ASN), IPv4 + IPv6.

No `maxminddb` dependency: the box stays pip-free for this. Spec:
https://maxmind.github.io/MaxMind-DB/

    db = MMDB("ml/data/GeoLite2-ASN.mmdb")
    db.get("8.8.8.8")  -> {'autonomous_system_number': 15169, 'autonomous_system_organization': 'GOOGLE'}
"""
import ipaddress
import mmap
import struct

META_MARKER = b"\xab\xcd\xefMaxMind.com"


class MMDB:
    def __init__(self, path):
        with open(path, "rb") as f:
            self.buf = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        m = self.buf.rfind(META_MARKER)
        if m < 0:
            raise ValueError(f"{path}: not an MMDB file")
        meta_start = m + len(META_MARKER)
        self.meta, _ = self._decode(meta_start, meta_start)
        self.node_count = self.meta["node_count"]
        self.record_size = self.meta["record_size"]
        self.ip_version = self.meta["ip_version"]
        self.node_bytes = self.record_size * 2 // 8
        self.tree_size = self.node_bytes * self.node_count
        self.data_start = self.tree_size + 16
        self._v4_start = None
        self._cache = {}

    # ---- search tree -------------------------------------------------------------
    def _record(self, node, bit):
        b = self.buf
        o = node * self.node_bytes
        rs = self.record_size
        if rs == 24:
            o += 3 * bit
            return int.from_bytes(b[o:o + 3], "big")
        if rs == 28:
            mid = b[o + 3]
            if bit == 0:
                return ((mid & 0xF0) << 20) | int.from_bytes(b[o:o + 3], "big")
            return ((mid & 0x0F) << 24) | int.from_bytes(b[o + 4:o + 7], "big")
        if rs == 32:
            o += 4 * bit
            return int.from_bytes(b[o:o + 4], "big")
        raise ValueError(f"unsupported record size {rs}")

    def _ipv4_start(self):
        if self._v4_start is None:
            node = 0
            if self.ip_version == 6:
                for _ in range(96):
                    if node >= self.node_count:
                        break
                    node = self._record(node, 0)
            self._v4_start = node
        return self._v4_start

    def get(self, ip):
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            return None
        if a.version == 6 and self.ip_version == 4:
            return None
        packed = a.packed
        node = self._ipv4_start() if a.version == 4 else 0
        bits = len(packed) * 8
        for i in range(bits):
            if node >= self.node_count:
                break
            bit = (packed[i >> 3] >> (7 - (i & 7))) & 1
            node = self._record(node, bit)
        if node == self.node_count or node < self.node_count:
            return None
        off = node - self.node_count - 16 + self.data_start
        if off in self._cache:
            return self._cache[off]
        val, _ = self._decode(off, self.data_start)
        if len(self._cache) < 50000:
            self._cache[off] = val
        return val

    # ---- data section ------------------------------------------------------------
    def _decode(self, off, base):
        b = self.buf
        ctrl = b[off]
        off += 1
        typ = ctrl >> 5
        if typ == 1:  # pointer
            ss = (ctrl >> 3) & 3
            v = ctrl & 7
            if ss == 0:
                p = (v << 8) | b[off]
                off += 1
            elif ss == 1:
                p = ((v << 16) | int.from_bytes(b[off:off + 2], "big")) + 2048
                off += 2
            elif ss == 2:
                p = ((v << 24) | int.from_bytes(b[off:off + 3], "big")) + 526336
                off += 3
            else:
                p = int.from_bytes(b[off:off + 4], "big")
                off += 4
            val, _ = self._decode(base + p, base)
            return val, off
        if typ == 0:
            typ = 7 + b[off]
            off += 1
        size = ctrl & 0x1F
        if size == 29:
            size = 29 + b[off]
            off += 1
        elif size == 30:
            size = 285 + int.from_bytes(b[off:off + 2], "big")
            off += 2
        elif size == 31:
            size = 65821 + int.from_bytes(b[off:off + 3], "big")
            off += 3
        if typ == 2:
            return b[off:off + size].decode("utf-8", "replace"), off + size
        if typ == 7:
            out = {}
            for _ in range(size):
                k, off = self._decode(off, base)
                v, off = self._decode(off, base)
                out[k] = v
            return out, off
        if typ == 11:
            out = []
            for _ in range(size):
                v, off = self._decode(off, base)
                out.append(v)
            return out, off
        if typ in (5, 6, 9, 10):
            return int.from_bytes(b[off:off + size], "big"), off + size
        if typ == 8:
            raw = b[off:off + size].rjust(4, b"\0")
            return struct.unpack(">i", raw)[0], off + size
        if typ == 3:
            return struct.unpack(">d", b[off:off + 8])[0], off + 8
        if typ == 15:
            return struct.unpack(">f", b[off:off + 4])[0], off + 4
        if typ == 14:
            return bool(size), off
        if typ == 4:
            return bytes(b[off:off + size]), off + size
        raise ValueError(f"unsupported MMDB data type {typ} at {off}")
