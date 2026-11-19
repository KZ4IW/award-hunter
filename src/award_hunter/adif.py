"""Minimal ADIF reader: records as {lowercase field: value}."""
import re

_FIELD = re.compile(r"<(\w+):(\d+)(?::\w)?>", re.I)
_EOH = re.compile(r"<eoh>", re.I)
_EOR = re.compile(r"<eor>", re.I)


def parse(text):
    """Parse ADIF text using the declared field lengths (values may contain '<')."""
    m = _EOH.search(text)
    if m:
        text = text[m.end():]
    records, cur, pos = [], {}, 0
    while True:
        f = _FIELD.search(text, pos)
        e = _EOR.search(text, pos)
        if e and (not f or e.start() < f.start()):
            if cur:
                records.append(cur)
            cur, pos = {}, e.end()
            continue
        if not f:
            break
        n = int(f.group(2))
        cur[f.group(1).lower()] = text[f.end():f.end() + n].strip()
        pos = f.end() + n
    if cur:
        records.append(cur)
    return records


def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return parse(fh.read())
    except OSError:
        return []
