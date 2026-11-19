"""WSJT-X ALL.TXT lines and a polling tail."""
import os
import re

# 261119_012015     3.573 Rx FT8      0  0.1 2027 CQ N9AAA EN52
LINE_RE = re.compile(r"^(\d{6}_\d{6})\s+([\d.]+)\s+(Rx|Tx)\s+(\S+)\s+(-?\d+)\s+(-?[\d.]+)\s+(\d+)\s+(.*?)\s*$")


def parse_line(line):
    m = LINE_RE.match(line)
    if not m:
        return None
    ts, mhz, rxtx, mode, snr, dt, df, msg = m.groups()
    return {"utc": ts, "mhz": float(mhz), "tx": rxtx == "Tx", "mode": mode, "snr": int(snr),
            "dt": float(dt), "df": int(df), "message": msg}


class Tail:
    """Call poll() regularly; returns new complete lines. Starts at end of file; handles rotation."""

    def __init__(self, path):
        self.path, self.pos, self.ino, self.buf = path, None, None, b""

    def poll(self):
        try:
            st = os.stat(self.path)
        except OSError:
            return []
        if self.pos is None or st.st_ino != self.ino or st.st_size < self.pos:
            first = self.pos is None
            self.ino, self.pos, self.buf = st.st_ino, (st.st_size if first else 0), b""
        if st.st_size == self.pos:
            return []
        with open(self.path, "rb") as fh:
            fh.seek(self.pos)
            data = fh.read(1 << 20)
        self.pos += len(data)
        *lines, self.buf = (self.buf + data).split(b"\n")
        return [l.decode("utf-8", "replace").rstrip("\r") for l in lines]

    def last_line(self):
        try:
            with open(self.path, "rb") as fh:
                fh.seek(max(0, os.path.getsize(self.path) - 4096))
                lines = fh.read().decode("utf-8", "replace").strip().splitlines()
                return lines[-1] if lines else ""
        except OSError:
            return ""
