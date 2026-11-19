"""Turns decodes into Stations, asks the award plugins, tracks heard stations and highlights."""
import threading
import time
from collections import deque

from . import wsjtx
from .callsign import GRID4_RE, is_us, split_call
from .data import lotw_cutoff
from .plugins import Station

HEARD_WINDOW = 15 * 60


def parse_message(msg):
    """FT8/FT4 message -> (sender token or None, grid or '', is_cq, [all call tokens])."""
    toks = msg.upper().split()
    calls = [t for t in toks if split_call(t)]
    sender, grid, cq = None, "", False
    if toks and toks[0] == "CQ":
        cq = True
        for i, t in enumerate(toks[1:], 1):
            if split_call(t):
                sender = t
                nxt = toks[i + 1] if i + 1 < len(toks) else ""
                if GRID4_RE.match(nxt) and nxt != "RR73":
                    grid = nxt
                break
    elif len(toks) >= 2 and split_call(toks[1]):
        sender = toks[1]
        if len(toks) >= 3 and GRID4_RE.match(toks[2]) and toks[2] != "RR73":
            grid = toks[2]
    return sender, grid, cq, calls


class Highlighter:
    def __init__(self, cfg):
        h = cfg["highlight"]
        self.enabled = h["enabled"]
        self.lotw = (wsjtx.parse_color(h["lotw_bg"]), wsjtx.parse_color(h["lotw_fg"]))
        self.other = (wsjtx.parse_color(h["other_bg"]), wsjtx.parse_color(h["other_fg"]))
        self.resend = h["resend_seconds"]
        self.sent = {}             # call token -> (monotonic time, colors)

    def colors(self, lotw):
        return self.lotw if lotw else self.other

    def want(self, call, colors, now):
        """Return True if a highlight for call should be (re)sent now."""
        prev = self.sent.get(call)
        if prev and prev[1] == colors and now - prev[0] < self.resend:
            return False
        self.sent[call] = (now, colors)
        return True

    def reset(self):
        self.sent.clear()


class Engine:
    def __init__(self, cfg, plugins, lookups, notifier=None, log=print):
        self.cfg = cfg
        self.plugins = plugins          # {key: Award}
        self.lk = lookups               # object with .states .lotw .cty
        self.notifier = notifier
        self.log = log
        self.lock = threading.RLock()
        self.hl = Highlighter(cfg)
        self.heard = {}                 # call -> record
        self.events = deque(maxlen=200)
        self.cutoff = lotw_cutoff(cfg["highlight"]["lotw_days"])
        self.generation = 0
        self._recheck = False
        self.band = ""
        self.mode = ""
        self.last_decode = 0.0
        self.notified = {}

    # ------------------------------------------------------------- lookups
    def station(self, token, grid="", band="", mode="", snr=0, message="", cq=False):
        call, base = split_call(token)
        state = self.lk.states.get(base, "") if is_us(base) else ""
        lotw = self.lk.lotw.get(base, "") >= self.cutoff
        dxcc = self.lk.cty.lookup(call) if self.lk.cty else ""
        return Station(call=call, base=base, state=state, lotw=lotw, grid=grid, dxcc=dxcc or "",
                       band=band, mode=mode, snr=snr, message=message, is_cq=cq)

    def classify(self, st):
        best = None
        for p in self.plugins.values():
            try:
                n = p.classify(st)
            except Exception as e:                       # a plugin bug must not stop decoding
                self.log(f"plugin {p.key} error: {e!r}")
                continue
            if n and (best is None or n.priority > best.priority):
                best = n
        return best

    def needed_states(self):
        out = set()
        for p in self.plugins.values():
            out |= p.needed_states()
        return out

    # ------------------------------------------------------------- events
    def reloaded(self):
        """Needed lists changed: re-evaluate everything we have highlighted."""
        with self.lock:
            self.generation += 1
            self._recheck = True
            self.cutoff = lotw_cutoff(self.cfg["highlight"]["lotw_days"])

    def on_status(self, band, mode):
        self.band, self.mode = band or self.band, mode or self.mode

    def on_decode(self, message, snr=0, mode="", band=None, send=None):
        """Process one decode. send(call, colors|None) transmits highlight changes to WSJT-X."""
        band = band or self.band
        now = time.monotonic()
        self.last_decode = time.time()
        sender, grid, cq, calls = parse_message(message)
        with self.lock:
            if self._recheck and send:
                self._recheck = False
                self._revalidate(send)
            for tok in calls:
                is_sender = tok == sender
                st = self.station(tok, grid if is_sender else "", band, mode or self.mode, snr, message, cq and is_sender)
                need = self.classify(st)
                if not need:
                    continue
                lotw = st.lotw or need.force_lotw
                colors = self.hl.colors(lotw)
                if send and self.hl.enabled and self.hl.want(st.call, colors, now):
                    send(st.call, colors)
                if is_sender:
                    self._record(st, need, lotw)

    def _revalidate(self, send):
        for call in list(self.hl.sent):
            s = split_call(call)
            st = self.station(call) if s else None
            need = self.classify(st) if st else None
            if not need:
                send(call, None)
                self.hl.sent.pop(call, None)
                self.heard.pop(call, None)
            else:
                self.hl.sent.pop(call, None)           # colors re-evaluated on next decode

    def on_logged(self, call, grid="", band="", send=None):
        with self.lock:
            for p in self.plugins.values():
                try:
                    p.on_logged(call, grid, band or self.band)
                except Exception as e:
                    self.log(f"plugin {p.key} on_logged error: {e!r}")
            s = split_call(call)
            base = s[1] if s else call.upper()
            for tok in list(self.hl.sent):
                ts = split_call(tok)
                if ts and ts[1] == base and not self.classify(self.station(tok)):
                    if send:
                        send(tok, None)
                    self.hl.sent.pop(tok, None)
            for tok in list(self.heard):
                ts = split_call(tok)
                if ts and ts[1] == base and not self.classify(self.station(tok)):
                    self.heard.pop(tok)
            self.event(f"logged {call}")

    def on_clear(self):
        with self.lock:
            self.hl.reset()

    def _record(self, st, need, lotw):
        t = time.time()
        r = self.heard.get(st.call)
        first = r is None
        if first:
            r = self.heard[st.call] = {"call": st.call, "first": t, "count": 0}
        r.update(award=need.award, label=need.label, reason=need.reason, lotw=lotw, band=st.band,
                 mode=st.mode, snr=st.snr, message=st.message, last=t, state=st.state, grid=st.grid,
                 dxcc=st.dxcc)
        r["count"] += 1
        if first:
            self.event(f"{st.call} {need.label} ({need.reason}) on {st.band or '?'}")
        if self.notifier and self.cfg["notify"]["on_needed"]:
            key = (st.call, st.band)
            cd = self.cfg["notify"]["cooldown_seconds"]
            if t - self.notified.get(key, 0) > cd:
                self.notified[key] = t
                self.notifier.send(f"Needed: {st.call} {need.label}",
                                   f"{need.reason}; {st.band or ''} {st.mode} {st.snr:+d} dB  {st.message}",
                                   tags="radio", priority="high" if need.priority >= 90 else "default")

    def event(self, text):
        self.events.append({"t": time.time(), "text": text})
        self.log(text)

    def heard_now(self):
        cut = time.time() - HEARD_WINDOW
        with self.lock:
            for k in [k for k, v in self.heard.items() if v["last"] < cut - 3600]:
                del self.heard[k]
            rows = [dict(v) for v in self.heard.values() if v["last"] >= cut]
        rows.sort(key=lambda r: -r["last"])
        return rows
