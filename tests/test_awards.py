import io
import os
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone

from helpers import cty, make_cfg, write
from award_hunter import adif, data
from award_hunter.callsign import STATES
from award_hunter.daemon import App
from award_hunter.plugins.states import parse_credits, parse_schedule_text
from award_hunter.state import Store

UTC = timezone.utc


def credits_paste(needed):
    out = ["America250 WAS Award - Credits", ""]
    for code, name in sorted(STATES.items(), key=lambda kv: kv[1]):
        out.append(f"{name} ({code})")
        if code not in needed:
            out.append(f"W{len(out) % 10}ABC (2026-03-0{len(out) % 9 + 1})")
    return "\n".join(out)


class Credits(unittest.TestCase):
    def test_parse(self):
        need, n = parse_credits(credits_paste({"DE", "KS", "KY"}))
        self.assertEqual(n, 50)
        self.assertEqual(need, ["DE", "KS", "KY"])

    def test_last_state_needed(self):
        need, _ = parse_credits(credits_paste({"WY"}))
        self.assertEqual(need, ["WY"])

    def test_bracketed_call(self):
        txt = "Kansas (KS)\n[W1AW/0]\nKentucky (KY)\nMaine (ME)\nK1ME"
        self.assertEqual(parse_credits(txt)[0], ["KY"])


class Schedule(unittest.TestCase):
    def test_pdf_text(self):
        txt = ("   W1AW Portable Operations\n"
               "   01/07/2026   Nebraska          W1AW/0\n"
               "   01/07/2026   New York          W1AW/2\n"
               "   12/09/2026   Guam              W1AW/KH2\n"
               "   02/04/2026   Atlantis          W1AW/9\n")
        rows, unknown = parse_schedule_text(txt)
        self.assertIn(("2026-01-07", "W1AW/0", "NE"), rows)
        self.assertIn(("2026-12-09", "W1AW/KH2", "GU"), rows)
        self.assertEqual(unknown, ["Atlantis"])


class UlsAndLotw(unittest.TestCase):
    def test_uls_zip(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("HD.dat", "HD|1|0001||K1ABC|A|HA|\r\nHD|2|0002||W9OLD|E|HA|\r\nHD|3|0003||N0NE|A|HA|\r\n")
            en = lambda usi, call, st: "|".join(["EN", usi, "", "", call, "L", "", "Name"] + [""] * 9 + [st, "12345"]) + "\r\n"
            z.writestr("EN.dat", en("1", "K1ABC", "CT") + en("2", "W9OLD", "IL") + en("3", "N0NE", "KS"))
        buf.seek(0)
        with zipfile.ZipFile(buf) as z:
            self.assertEqual(sorted(data.parse_uls(z)), [("K1ABC", "CT"), ("N0NE", "KS")])

    def test_lotw_csv(self):
        p = os.path.join(tempfile.mkdtemp(), "l.csv")
        write(p, "K1ABC,2026-10-01,12:00:00\nW9OLD,2015-01-01,00:00:00\nbad line\n")
        d = data.load_lotw(p)
        self.assertEqual(d, {"K1ABC": "2026-10-01", "W9OLD": "2015-01-01"})


def make_app(now, adif_text="", **awards):
    cfg, tmp = make_cfg(**(awards or {"america250": {}}))
    write(os.path.join(tmp, "wsjtx_log.adi"), adif_text)
    store = Store(os.path.join(tmp, "state.json"))
    app = App(cfg, store=store, now=now, log=lambda *a: None)
    app.lk.states = {"KS0XX": "KS", "K1ABC": "CT", "KY4AA": "KY", "N0NE": "NE"}
    app.lk.lotw = {"KS0XX": "2026-11-01", "KY4AA": "2019-01-01"}
    app.lk.cty = cty()
    app.maybe_reload(force=True)
    return app


class America250Logic(unittest.TestCase):
    NOW = datetime(2026, 1, 8, 12, 0, tzinfo=UTC)       # week of W1AW/0 NE and W1AW/2 NY

    def classify(self, app, call):
        return app.engine.classify(app.engine.station(call))

    def test_default_all_needed_and_w1aw_week(self):
        app = make_app(self.NOW)
        self.assertEqual(self.classify(app, "KS0XX").label, "KS")
        n = self.classify(app, "W1AW/0")
        self.assertEqual((n.priority, n.label), (90, "W1AW/0 NE"))
        self.assertTrue(n.force_lotw)
        self.assertIsNone(self.classify(app, "W1AW/5"))          # not on this week

    def test_credits_and_worked(self):
        app = make_app(self.NOW, "<call:5>KY4AA<qso_date:8>20260105<eor>")
        app.action("america250", "credits", {"text": credits_paste({"KS", "KY", "NE"})})
        self.assertEqual(self.classify(app, "KS0XX").label, "KS")
        self.assertIsNone(self.classify(app, "K1ABC"))            # CT credited
        self.assertIsNone(self.classify(app, "KY4AA"))            # already worked in 2026
        self.assertIsNone(self.classify(app, "W1AW/2"))           # NY credited

    def test_w1aw_qso_marks_state_pending(self):
        app = make_app(self.NOW, "<call:6>W1AW/0<qso_date:8>20260107<time_on:4>1300<band:3>20m<eor>")
        p = app.plugins["america250"]
        self.assertIn("NE", p.pending)
        self.assertIsNone(self.classify(app, "W1AW/0"))
        self.assertIsNone(self.classify(app, "N0NE"))             # NE no longer needed

    def test_click_cycles_have_needed_pending(self):
        app = make_app(self.NOW, "")
        p = app.plugins["america250"]
        app.action("america250", "credits", {"text": credits_paste({"KS"})})
        app.action("america250", "cycle", {"state": "KS"})          # needed -> dashed
        self.assertIn("KS", p.pending)
        self.assertIsNone(self.classify(app, "KS0XX"))
        app.action("america250", "cycle", {"state": "KS"})          # dashed -> have it
        self.assertNotIn("KS", p.pending)
        self.assertNotIn("KS", p.needed)
        app.action("america250", "cycle", {"state": "KS"})          # have it -> needed
        self.assertEqual(self.classify(app, "KS0XX").label, "KS")

    def test_old_qso_does_not_count(self):
        app = make_app(self.NOW, "<call:5>KS0XX<qso_date:8>20251231<eor>")
        self.assertIsNotNone(self.classify(app, "KS0XX"))

    def test_event_over(self):
        app = make_app(datetime(2027, 1, 2, tzinfo=UTC))
        self.assertIsNone(self.classify(app, "KS0XX"))
        self.assertTrue(app.plugins["america250"].panel()["ended"])

    def test_toggle_persists(self):
        app = make_app(self.NOW)
        app.action("america250", "toggle", {"state": "KS"})
        self.assertIsNone(self.classify(app, "KS0XX"))
        store2 = Store(app.store.path)
        self.assertNotIn("KS", store2.section("america250")["needed"])
        with self.assertRaises(ValueError):
            app.action("america250", "toggle", {"state": "XX"})


class OtherAwards(unittest.TestCase):
    def test_grids_watch(self):
        app = make_app(None, "<call:4>K1AB<gridsquare:6>EM77ab<eor>", grids={"watch": ["EM77", "DN83"]})
        st = app.engine.station("K9ZZ", grid="DN83")
        self.assertEqual(app.engine.classify(st).label, "DN83")
        self.assertIsNone(app.engine.classify(app.engine.station("K9ZZ", grid="EM77")))   # worked
        self.assertIsNone(app.engine.classify(app.engine.station("K9ZZ", grid="FN42")))   # not watched

    def test_dxcc(self):
        app = make_app(None, "<call:6>JA1ABC<eor>", dxcc={})
        self.assertIsNone(app.engine.classify(app.engine.station("JE1XYZ")))
        self.assertEqual(app.engine.classify(app.engine.station("IK2ABC")).label, "Italy")


if __name__ == "__main__":
    unittest.main()
