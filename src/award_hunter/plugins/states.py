"""Worked All States, and the 2026 ARRL America250 WAS event (adds the W1AW/n schedule)."""
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from importlib import resources

from ..callsign import ALL_REGIONS, BYNAME, STATES, base_call, split_call
from . import Award, Need

UTC = timezone.utc
WEEK = timedelta(days=7)


def adif_date(rec):
    return rec.get("qso_date", "")[:8]


# ---------------------------------------------------------------- parsers
def parse_credits(text):
    """Award-page credits paste -> (needed states, number of state headings found).

    The page lists 'State Name (XX)' followed by the credited QSO's callsign line if the state
    is credited; a state immediately followed by another heading (or nothing) is still needed.
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    head_re = re.compile(r".+?\s*\(([A-Z]{2})\)")
    call_re = re.compile(r"\[?[A-Z0-9]*\d[A-Z0-9]*(/[A-Z0-9]+)*\]?(\(.*\))?(\s.*)?", re.I)
    heads = [(i, m.group(1)) for i, l in enumerate(lines)
             if (m := head_re.fullmatch(l)) and m.group(1) in STATES]
    need = []
    for i, st in heads:
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if not nxt or head_re.fullmatch(nxt) or not call_re.fullmatch(nxt):
            need.append(st)
    return sorted(set(need)), len(heads)


def parse_schedule_text(text):
    """pdftotext -layout output of the ARRL W1AW Portable schedule -> [(YYYY-MM-DD, call, ST)]."""
    rows, unknown = set(), set()
    for m in re.finditer(r"^\s*(\d\d)/(\d\d)/(\d{4})\s+(.+?)\s{2,}(W1AW/\w+)", text, re.M):
        mo, d, y, name, call = m.groups()
        st = BYNAME.get(name.strip().lower())
        if st:
            rows.add((f"{y}-{mo}-{d}", call.upper(), st))
        else:
            unknown.add(name.strip())
    return sorted(rows), sorted(unknown)


def parse_schedule_tsv(text):
    rows = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        start, call, st = line.split("\t")[:3]
        rows.append((start.strip(), call.strip().upper(), st.strip().upper()))
    return rows


def pdf_to_text(data):
    r = subprocess.run(["pdftotext", "-layout", "-", "-"], input=data, capture_output=True, check=True)
    return r.stdout.decode("utf-8", "replace")


# ---------------------------------------------------------------- WAS
class WAS(Award):
    key = "was"
    title = "Worked All States"
    priority = 50
    default_since = "1900-01-01"

    def __init__(self, cfg):
        super().__init__(cfg)
        self.since = str(cfg.get("since", self.default_since)).replace("-", "")
        self.needed = set(STATES)
        self.pending = {}          # state -> how (worked, awaiting credit)
        self.worked_calls = set()
        self._sec = None

    # needed = ticked states minus worked-awaiting-credit
    def effective_needed(self):
        return self.needed - set(self.pending)

    def needed_states(self):
        return self.effective_needed() if self.active() else set()

    def active(self, now=None):
        return True

    def reload(self, ctx):
        self._sec = ctx.store.section(self.key)
        self.needed = set(self._sec.get("needed", sorted(STATES)))
        self.pending = dict(self._sec.get("pending", {}))
        worked = set()
        for rec in ctx.adif:
            if adif_date(rec) >= self.since and rec.get("call"):
                b = base_call(rec["call"])
                if b != "W1AW":
                    worked.add(b)
        self.worked_calls = worked

    def classify(self, st):
        if not self.active() or not st.state or st.base in self.worked_calls:
            return None
        if st.state in self.effective_needed():
            return Need(self.priority, self.key, st.state, f"needed state {ALL_REGIONS.get(st.state, st.state)}")
        return None

    def on_logged(self, call, grid, band):
        b = base_call(call)
        if b != "W1AW":
            self.worked_calls.add(b)

    def panel(self):
        return {"title": self.title, "kind": "states", "active": self.active(),
                "states": [{"code": c, "name": n, "needed": c in self.needed, "pending": self.pending.get(c, "")}
                           for c, n in sorted(STATES.items(), key=lambda kv: kv[1])],
                "needed_count": len(self.effective_needed())}

    def _save(self, ctx):
        self._sec["needed"] = sorted(self.needed)
        self._sec["pending"] = self.pending
        ctx.store.save()

    def action(self, name, payload, ctx):
        st = str(payload.get("state", "")).upper()
        if name == "toggle":
            if st not in STATES:
                raise ValueError(f"unknown state {st!r}")
            self.needed ^= {st}
        elif name == "set_needed":
            vals = {s.upper() for s in payload.get("states", [])}
            if not vals <= set(STATES):
                raise ValueError("unknown state code")
            self.needed = vals
        elif name == "credits":
            need, n = parse_credits(payload.get("text", ""))
            if n < 45:
                raise ValueError(f"only found {n} 'State (XX)' lines; paste the whole list from the award page")
            newly = sorted(self.needed - set(need))
            self.needed = set(need)
            self.pending = {k: v for k, v in self.pending.items() if k in self.needed}
            self._save(ctx)
            return {"ok": True, "message": f"{n} states read, {len(STATES) - len(need)} credited, "
                    f"{len(need)} still needed" + (f"; newly credited: {' '.join(newly)}" if newly else "")}
        elif name == "done":
            if st not in STATES:
                raise ValueError(f"unknown state {st!r}")
            self.pending[st] = f"marked by hand {datetime.now(UTC):%Y-%m-%d}"
        elif name == "undone":
            self.pending.pop(st, None)
        else:
            return super().action(name, payload, ctx)
        self._save(ctx)
        return {"ok": True}


# ---------------------------------------------------------------- America250
class America250(WAS):
    key = "america250"
    title = "America250 WAS (2026)"
    priority = 60
    default_since = "2026-01-01"
    START = datetime(2026, 1, 1, tzinfo=UTC)
    END = datetime(2027, 1, 1, tzinfo=UTC)

    def __init__(self, cfg):
        super().__init__(cfg)
        self.schedule = []          # [(start datetime, call, ST)]
        self.now = None

    def _now(self):
        return self.now or datetime.now(UTC)

    def active(self, now=None):
        return self.START <= (now or self._now()) < self.END

    def reload(self, ctx):
        self.now = ctx.now
        super().reload(ctx)
        text = self._sec.get("schedule_tsv")
        if not text:
            text = resources.files("award_hunter").joinpath("data/america250-2026.tsv").read_text()
        self.schedule = [(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=UTC), c, st)
                         for s, c, st in parse_schedule_tsv(text)]
        self._mark_w1aw_worked(ctx.adif)

    def _mark_w1aw_worked(self, adif):
        """A W1AW/n QSO inside that call's scheduled week marks the state worked-awaiting-credit."""
        changed = False
        for rec in adif:
            call = rec.get("call", "").upper()
            if not call.startswith("W1AW/") or not rec.get("qso_date"):
                continue
            try:
                t = datetime.strptime(rec["qso_date"][:8] + rec.get("time_on", "0000")[:4],
                                      "%Y%m%d%H%M").replace(tzinfo=UTC)
            except ValueError:
                continue
            for s, c, st in self.schedule:
                if c == call and s <= t < s + WEEK and st in self.needed and st not in self.pending:
                    self.pending[st] = f"{call} {t:%Y-%m-%d %H%Mz} {rec.get('band', '')} {rec.get('mode', '')}".strip()
                    changed = True
        if changed and self._sec is not None:
            self._sec["pending"] = self.pending

    def active_rows(self, now=None):
        """{call: [ST, ...]} W1AW/n stations on the air this week for still-needed states."""
        now = now or self._now()
        need, out = self.effective_needed(), {}
        for s, c, st in self.schedule:
            if st in need and s <= now < s + WEEK:
                out.setdefault(c, []).append(st)
        return out

    def classify(self, st):
        if not self.active():
            return None
        rows = self.active_rows()
        if st.call in rows:
            label = "/".join(rows[st.call])
            return Need(90, self.key, f"{st.call} {label}", f"W1AW this week for needed {label}", force_lotw=True)
        if st.base == "W1AW":
            return None
        return super().classify(st)

    def on_logged(self, call, grid, band):
        rows = self.active_rows()
        if call.upper() in rows:
            for st in rows[call.upper()]:
                self.pending[st] = f"{call.upper()} logged {self._now():%Y-%m-%d %H%Mz} {band or ''}".strip()
        super().on_logged(call, grid, band)

    def panel(self):
        p = super().panel()
        now = self._now()
        need = self.effective_needed()
        p["kind"] = "states"
        p["ended"] = now >= self.END
        p["w1aw_now"] = [{"call": c, "states": s} for c, s in sorted(self.active_rows().items())]
        upcoming = []
        for s, c, st in self.schedule:
            if st in need and now < s + WEEK:
                upcoming.append({"state": st, "call": c, "start": f"{s:%Y-%m-%d}",
                                 "end": f"{s + WEEK - timedelta(days=1):%Y-%m-%d}", "on_now": s <= now})
        p["upcoming"] = upcoming
        p["schedule_rows"] = len(self.schedule)
        return p

    def action(self, name, payload, ctx):
        if name in ("schedule_pdf", "schedule_text"):
            if name == "schedule_pdf":
                import base64
                text = pdf_to_text(base64.b64decode(payload.get("data", "")))
            else:
                text = payload.get("text", "")
            rows, unknown = parse_schedule_text(text)
            if len(rows) < 50:
                raise ValueError(f"only parsed {len(rows)} activations; format changed? Not saved.")
            self._sec["schedule_tsv"] = "".join(f"{s}\t{c}\t{st}\n" for s, c, st in rows)
            ctx.store.save()
            return {"ok": True, "message": f"imported {len(rows)} activations"
                    + (f"; skipped unknown: {', '.join(unknown)}" if unknown else "")}
        return super().action(name, payload, ctx)
