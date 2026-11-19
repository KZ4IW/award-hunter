"""Band tips from PSK Reporter: which band brings needed stations into your grid right now.

PSK Reporter asks clients not to query more often than every 5 minutes; the interval is
clamped to >= 300 s, the result is cached, queries pause while WSJT-X is idle, and errors
back off exponentially up to an hour.
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from . import __version__
from .callsign import BYNAME, band_of_hz, split_call, BAND_ORDER

URL = "https://retrieve.pskreporter.info/query"
WINDOW = 900
MIN_INTERVAL = 300
MAX_BACKOFF = 3600
IDLE = 600


def query_url(grid):
    return URL + "?" + urllib.parse.urlencode({
        "callsign": grid, "modify": "grid", "flowStartSeconds": -WINDOW, "rronly": 1,
        "noactive": 1, "nolocator": 1, "rptlimit": 5000})


def parse_reports(xml_bytes):
    root = ET.fromstring(xml_bytes)
    return [e.attrib for e in root.iter("receptionReport")]


def analyze(reports, grid, engine, modes):
    """Group needed senders by band. engine.station()/classify() decide what is needed."""
    from .data import lotw_cutoff
    cutoff = lotw_cutoff(engine.cfg["highlight"]["lotw_days"])
    bands = {}
    for a in reports:
        if a.get("mode") not in modes:
            continue
        if not a.get("receiverLocator", "").upper().startswith(grid):
            continue
        try:
            b = band_of_hz(int(a["frequency"]))
        except (KeyError, ValueError):
            continue
        tok = a.get("senderCallsign", "").upper()
        if not b or not split_call(tok):
            continue
        st = engine.station(tok, band=b, mode=a.get("mode", ""))
        region = BYNAME.get((a.get("senderRegion") or "").strip().lower())
        if region and not st.state and a.get("senderDXCC") == "United States":
            st.state = region
        if a.get("senderLotwUpload"):
            st.lotw = a["senderLotwUpload"][:10] >= cutoff
        loc = (a.get("senderLocator") or "")[:4].upper()
        if re.fullmatch(r"[A-R]{2}\d{2}", loc):
            st.grid = loc
        need = engine.classify(st)
        if not need:
            continue
        lotw = st.lotw or need.force_lotw
        e = bands.setdefault(b, {}).setdefault(st.call, {"label": need.label, "reason": need.reason, "lotw": lotw,
                                                         "w1aw": need.priority >= 90, "rx": set(), "snr": -99})
        e["rx"].add(a.get("receiverCallsign", "").upper())
        try:
            e["snr"] = max(e["snr"], int(a.get("sNR", -99)))
        except ValueError:
            pass
    out = {}
    for b, calls in bands.items():
        lst = [dict(call=c, label=v["label"], reason=v["reason"], lotw=v["lotw"], snr=v["snr"],
                    rx=sorted(v["rx"])[:5]) for c, v in calls.items()]
        lst.sort(key=lambda x: (not x["call"].startswith("W1AW/"), not x["lotw"], -x["snr"]))
        score = sum(5 if calls[x["call"]]["w1aw"] else 2 if x["lotw"] else 1 for x in lst)
        out[b] = {"score": score, "stations": lst}
    return dict(sorted(out.items(), key=lambda kv: BAND_ORDER.index(kv[0]) if kv[0] in BAND_ORDER else 99))


class BandTipper:
    def __init__(self, cfg, engine, cache_path, notifier=None, log=print, fetch=None):
        self.cfg, self.engine, self.cache_path = cfg, engine, cache_path
        self.notifier, self.log = notifier, log
        self.fetch = fetch or self._fetch
        self.interval = max(MIN_INTERVAL, cfg["bandtips"]["interval_seconds"])
        self.modes = set(cfg["bandtips"]["modes"])
        self.next_at = 0.0
        self.backoff = 0
        self.result = None
        self.error = ""
        self.last_tip = {}
        try:
            with open(cache_path) as fh:
                c = json.load(fh)
            self.raw, self.raw_time = c["reports"], c["time"]
            self.next_at = c["time"] + self.interval
        except (OSError, ValueError, KeyError):
            self.raw, self.raw_time = None, 0

    def grid_changed(self, now=None):
        """Station grid changed: old reports are for the wrong area. Next query still >= 300 s after the last."""
        now = now or time.time()
        self.raw, self.result = None, None
        self.next_at = max(now, self.raw_time + MIN_INTERVAL, self.next_at if self.backoff else 0)

    def _fetch(self, grid):
        req = urllib.request.Request(query_url(grid), headers={"User-Agent": f"award-hunter/{__version__} (+https://kz4iw.link/award-hunter/)"})
        with urllib.request.urlopen(req, timeout=90) as r:
            return parse_reports(r.read())

    def due(self, now=None):
        now = now or time.time()
        if not self.cfg["bandtips"]["enabled"] or not self.cfg["station"]["grid"]:
            return False
        if now < self.next_at:
            return False
        return now - self.engine.last_decode < IDLE      # station idle: don't query

    def tick(self, now=None):
        now = now or time.time()
        if self.due(now):
            grid = self.cfg["station"]["grid"][:4]
            try:
                self.raw, self.raw_time = self.fetch(grid), now
                self.backoff, self.error = 0, ""
                self.next_at = now + self.interval
                try:
                    os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
                    with open(self.cache_path + ".tmp", "w") as fh:
                        json.dump({"time": now, "reports": self.raw}, fh)
                    os.replace(self.cache_path + ".tmp", self.cache_path)
                except OSError:
                    pass
                self.recompute()
                self.maybe_tip()
            except Exception as e:
                self.backoff = min(MAX_BACKOFF, max(self.interval, self.backoff * 2 or self.interval))
                self.next_at = now + self.backoff
                self.error = f"{e.__class__.__name__}: {e}"[:200]
                self.log(f"PSK Reporter query failed ({self.error}); next try in {self.backoff // 60} min")
        elif self.raw is not None and self.result is None:
            self.recompute()

    def recompute(self):
        if self.raw is None:
            return
        grid = self.cfg["station"]["grid"][:4]
        self.result = {"time": self.raw_time, "grid": grid, "reports": len(self.raw),
                       "bands": analyze(self.raw, grid, self.engine, self.modes)}

    def maybe_tip(self):
        if not self.notifier or not self.cfg["notify"]["on_bandtip"] or not self.result:
            return
        bands = self.result["bands"]
        if not bands:
            return
        cur = self.engine.band
        best, v = max(bands.items(), key=lambda kv: kv[1]["score"])
        if best == cur or not any(x["lotw"] for x in v["stations"]):
            return
        if v["score"] <= bands.get(cur, {}).get("score", 0):
            return
        if time.time() - self.last_tip.get(best, 0) < 1800:
            return
        self.last_tip[best] = time.time()
        top = v["stations"][:4]
        body = "; ".join(f"{x['label']} {x['call']} ({'LoTW' if x['lotw'] else 'no LoTW'}, {x['snr']:+d} dB)"
                         for x in top) + f". You're on {cur or '?'}."
        self.notifier.send(f"Band tip: try {best}", body, tags="bulb")
        self.engine.event(f"band tip: {best} ({v['score']}) vs {cur or '?'}")
