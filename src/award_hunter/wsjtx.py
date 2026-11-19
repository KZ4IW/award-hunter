"""WSJT-X UDP protocol (NetworkMessage.hpp): parse incoming messages, build Highlight Callsign.

Wire format: big-endian Qt QDataStream. utf8 = u32 length + bytes (0xFFFFFFFF = null).
QDateTime = i64 julian day, u32 ms since midnight, u8 timespec (+ i32 offset if timespec == 2).
QColor = i8 spec, u16 alpha, u16 r, u16 g, u16 b, u16 pad; spec 0 (invalid) clears a highlight.
"""
import struct

MAGIC = 0xADBCCBDA
SCHEMA = 2
HEARTBEAT, STATUS, DECODE, CLEAR, REPLY, QSO_LOGGED, CLOSE = 0, 1, 2, 3, 4, 5, 6
HIGHLIGHT_CALLSIGN = 13


class Reader:
    def __init__(self, data):
        self.d, self.p = data, 0

    def _take(self, fmt):
        v = struct.unpack_from(fmt, self.d, self.p)[0]
        self.p += struct.calcsize(fmt)
        return v

    def u8(self): return self._take(">B")
    def boolean(self): return self._take(">B") != 0
    def u32(self): return self._take(">I")
    def i32(self): return self._take(">i")
    def u64(self): return self._take(">Q")
    def i64(self): return self._take(">q")
    def double(self): return self._take(">d")

    def utf8(self):
        n = self.u32()
        if n == 0xFFFFFFFF:
            return ""
        if self.p + n > len(self.d):
            raise struct.error("string past end")
        s = self.d[self.p:self.p + n].decode("utf-8", "replace")
        self.p += n
        return s

    def qdatetime(self):
        jd, ms, spec = self.i64(), self.u32(), self.u8()
        if spec == 2:
            self.i32()
        return jd, ms


def parse(data):
    """Return a dict for a WSJT-X datagram, or None if it isn't one / is truncated."""
    r = Reader(data)
    try:
        if r.u32() != MAGIC:
            return None
        schema, mtype, wid = r.u32(), r.u32(), r.utf8()
    except struct.error:
        return None
    m = {"schema": schema, "type": mtype, "id": wid}
    try:
        if mtype == HEARTBEAT:
            m.update(max_schema=r.u32(), version=r.utf8(), revision=r.utf8())
        elif mtype == STATUS:
            m.update(dial=r.u64(), mode=r.utf8(), dx_call=r.utf8(), report=r.utf8(), tx_mode=r.utf8(),
                     tx_enabled=r.boolean(), transmitting=r.boolean(), decoding=r.boolean(),
                     rx_df=r.u32(), tx_df=r.u32(), de_call=r.utf8(), de_grid=r.utf8(), dx_grid=r.utf8())
            try:
                m.update(watchdog=r.boolean(), submode=r.utf8(), fast=r.boolean(), special=r.u8(),
                         tol=r.u32(), tr_period=r.u32(), config=r.utf8(), tx_message=r.utf8())
            except struct.error:
                pass                                   # older WSJT-X: fewer trailing fields
        elif mtype == DECODE:
            m.update(new=r.boolean(), time=r.u32(), snr=r.i32(), dt=r.double(), df=r.u32(),
                     mode=r.utf8(), message=r.utf8(), low_conf=r.boolean())
            try:
                m["off_air"] = r.boolean()
            except struct.error:
                m["off_air"] = False
        elif mtype == CLEAR:
            try:
                m["window"] = r.u8()
            except struct.error:
                m["window"] = 0
        elif mtype == QSO_LOGGED:
            r.qdatetime()
            m.update(dx_call=r.utf8(), dx_grid=r.utf8(), tx_freq=r.u64(), mode=r.utf8())
    except struct.error:
        return None
    return m


# ---------------------------------------------------------------- writing
def w_utf8(s):
    b = s.encode("utf-8")
    return struct.pack(">I", len(b)) + b


def header(mtype, wid, schema=SCHEMA):
    return struct.pack(">III", MAGIC, schema, mtype) + w_utf8(wid)


def qcolor(rgb):
    if rgb is None:
        return struct.pack(">bHHHHH", 0, 0, 0, 0, 0, 0)
    r, g, b = rgb
    return struct.pack(">bHHHHH", 1, 0xFFFF, r * 257, g * 257, b * 257, 0)


def parse_color(text):
    """'#0D47A1' or '0D47A1' -> (13, 71, 161)."""
    h = text.strip().lstrip("#")
    if len(h) != 6:
        raise ValueError(f"colour must be #RRGGBB, got {text!r}")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def build_highlight(wid, call, bg, fg, schema=SCHEMA, last_only=False):
    """Highlight Callsign (13). bg/fg = (r, g, b) or None; both None clears the highlight."""
    return (header(HIGHLIGHT_CALLSIGN, wid, schema) + w_utf8(call) + qcolor(bg) + qcolor(fg)
            + struct.pack(">B", 1 if last_only else 0))


def parse_highlight(data):
    """Inverse of build_highlight (used by tests and the simulator)."""
    r = Reader(data)
    if r.u32() != MAGIC:
        return None
    r.u32()
    if r.u32() != HIGHLIGHT_CALLSIGN:
        return None
    wid, call = r.utf8(), r.utf8()
    cols = []
    for _ in range(2):
        spec, _a, cr, cg, cb, _pad = struct.unpack_from(">bHHHHH", r.d, r.p)
        r.p += 11
        cols.append((cr // 257, cg // 257, cb // 257) if spec else None)
    return {"id": wid, "call": call, "bg": cols[0], "fg": cols[1], "last_only": r.boolean()}


# Builders for what WSJT-X sends (simulator / tests).
def build_heartbeat(wid, version="3.1.0", schema=SCHEMA):
    return header(HEARTBEAT, wid, schema) + struct.pack(">I", 3) + w_utf8(version) + w_utf8("sim")


def build_status(wid, dial_hz, mode="FT8", de_call="N0CALL", de_grid="EM48", decoding=False, schema=SCHEMA):
    return (header(STATUS, wid, schema) + struct.pack(">Q", dial_hz) + w_utf8(mode) + w_utf8("") + w_utf8("")
            + w_utf8(mode) + struct.pack(">BBB", 0, 0, 1 if decoding else 0) + struct.pack(">II", 1500, 1500)
            + w_utf8(de_call) + w_utf8(de_grid) + w_utf8("") + struct.pack(">B", 0) + w_utf8("")
            + struct.pack(">BB", 0, 0) + struct.pack(">II", 0, 15) + w_utf8("Default") + w_utf8(""))


def build_decode(wid, message, snr=-10, mode="~", time_ms=0, df=1500, dt=0.1, new=True, schema=SCHEMA):
    return (header(DECODE, wid, schema) + struct.pack(">BIi", 1 if new else 0, time_ms, snr)
            + struct.pack(">dI", dt, df) + w_utf8(mode) + w_utf8(message) + struct.pack(">BB", 0, 0))


def build_clear(wid, schema=SCHEMA):
    return header(CLEAR, wid, schema)


def build_qso_logged(wid, dx_call, dx_grid="", freq_hz=14074000, mode="FT8", schema=SCHEMA):
    dtime = struct.pack(">qIB", 2461000, 0, 1)
    return (header(QSO_LOGGED, wid, schema) + dtime + w_utf8(dx_call) + w_utf8(dx_grid)
            + struct.pack(">Q", freq_hz) + w_utf8(mode))
