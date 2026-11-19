"""Lookup data: callsign -> US state (FCC ULS), LoTW last upload (ARRL), DXCC (cty.dat).

Downloads go to the cache directory and are refreshed weekly; a failed refresh keeps the old file.
"""
import email.utils
import io
import os
import re
import time
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone

from . import __version__

UA = f"award-hunter/{__version__} (+https://kz4iw.link/award-hunter/)"


# ---------------------------------------------------------------- FCC ULS
def parse_uls(zf):
    """Yield (call, state) for active licenses from an opened l_amat.zip (HD.dat + EN.dat)."""
    active = set()
    with zf.open("HD.dat") as fh:
        for raw in io.TextIOWrapper(fh, encoding="latin-1", newline=""):
            f = raw.split("|")
            if len(f) > 5 and f[0] == "HD" and f[5] == "A":
                active.add(f[1])
    seen = set()
    with zf.open("EN.dat") as fh:
        for raw in io.TextIOWrapper(fh, encoding="latin-1", newline=""):
            f = raw.rstrip("\r\n").split("|")
            if len(f) < 18 or f[0] != "EN" or f[1] not in active:
                continue
            call, st = f[4].strip().upper(), f[17].strip().upper()
            if call and len(st) == 2 and call not in seen:
                seen.add(call)
                yield call, st


def build_states_tsv(zip_path, out_path):
    n = 0
    tmp = out_path + ".tmp"
    with zipfile.ZipFile(zip_path) as zf, open(tmp, "w") as out:
        for call, st in parse_uls(zf):
            out.write(f"{call}\t{st}\n")
            n += 1
    if n < 1000:
        os.unlink(tmp)
        raise ValueError(f"only {n} licenses parsed from {zip_path}; not replacing the old table")
    os.replace(tmp, out_path)
    return n


def load_states(path):
    out = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                call, _, st = line.rstrip("\n").partition("\t")
                if st:
                    out[call] = st
    except OSError:
        pass
    return out


# ---------------------------------------------------------------- LoTW
def load_lotw(path):
    """{call: 'YYYY-MM-DD'} of last LoTW upload."""
    out = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                c, _, rest = line.partition(",")
                if rest[:4].isdigit():
                    out[c.strip().upper()] = rest[:10]
    except OSError:
        pass
    return out


def lotw_cutoff(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- cty.dat (DXCC)
class Cty:
    def __init__(self):
        self.exact, self.prefix, self.entities = {}, {}, set()

    @classmethod
    def load(cls, path):
        c = cls()
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            return c
        for block in text.split(";"):
            block = block.strip()
            if not block:
                continue
            head, _, aliases = block.partition("\n") if block.count(":") >= 8 else ("", "", "")
            parts = head.split(":")
            if len(parts) < 8:
                continue
            name = parts[0].strip()
            primary = parts[7].strip()
            if primary.startswith("*"):
                continue                     # WAE/CQ-only entity: not DXCC, falls back to its parent
            c.entities.add(name)
            c.prefix.setdefault(primary, name)
            for a in re.split(r"[,\s]+", aliases):
                a = re.sub(r"[\(\[<{~].*$", "", a.strip())
                if not a:
                    continue
                if a.startswith("="):
                    c.exact[a[1:]] = name
                else:
                    c.prefix[a] = name
        return c

    def lookup(self, call):
        call = call.strip("<>").upper()
        if call in self.exact:
            return self.exact[call]
        parts = call.split("/")
        if len(parts) > 1:
            short = [p for p in parts if p not in ("P", "M", "MM", "AM", "QRP", "R", "A") and not p.isdigit()]
            call = min(short, key=len) if short else parts[0]
        for n in range(len(call), 0, -1):
            e = self.prefix.get(call[:n])
            if e:
                return e
        return None


# ---------------------------------------------------------------- downloads
def download(url, dest, timeout=300):
    """Fetch url to dest if newer (If-Modified-Since). Returns True if the file changed."""
    headers = {"User-Agent": UA}
    if os.path.exists(dest):
        headers["If-Modified-Since"] = email.utils.formatdate(os.path.getmtime(dest), usegmt=True)
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 304:
            os.utime(dest)
            return False
        raise
    tmp = dest + ".part"
    with resp, open(tmp, "wb") as out:
        while chunk := resp.read(1 << 16):
            out.write(chunk)
    os.replace(tmp, dest)
    return True


def age_days(path):
    try:
        return (time.time() - os.path.getmtime(path)) / 86400
    except OSError:
        return None
