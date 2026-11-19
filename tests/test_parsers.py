import struct
import unittest

from helpers import cty, write  # noqa: F401  (sets sys.path)
from award_hunter import adif, alltxt, wsjtx
from award_hunter.callsign import base_call, band_of_hz, split_call
from award_hunter.engine import parse_message


class WsjtxWire(unittest.TestCase):
    def test_decode_roundtrip(self):
        m = wsjtx.parse(wsjtx.build_decode("WSJT-X", "CQ K1ABC FN42", snr=-12, mode="~"))
        self.assertEqual((m["type"], m["id"], m["message"], m["snr"], m["mode"]),
                         (2, "WSJT-X", "CQ K1ABC FN42", -12, "~"))
        self.assertFalse(m["low_conf"])
        self.assertFalse(m["off_air"])

    def test_status_roundtrip(self):
        m = wsjtx.parse(wsjtx.build_status("WSJT-X", 14074000, de_call="KZ4IW", de_grid="FM16"))
        self.assertEqual((m["dial"], m["mode"], m["de_call"], m["de_grid"]), (14074000, "FT8", "KZ4IW", "FM16"))
        self.assertEqual(m["config"], "Default")

    def test_status_old_schema_truncated(self):
        full = wsjtx.build_status("W", 7074000)
        # cut after dx_grid (older WSJT-X sends fewer trailing fields)
        cut = full[:full.index(b"EM48") + 4 + 4]
        m = wsjtx.parse(cut)
        self.assertEqual(m["dial"], 7074000)

    def test_qso_logged(self):
        m = wsjtx.parse(wsjtx.build_qso_logged("W", "KS0XX", "EM17", 14075000))
        self.assertEqual((m["type"], m["dx_call"], m["dx_grid"], m["tx_freq"]), (5, "KS0XX", "EM17", 14075000))

    def test_garbage(self):
        self.assertIsNone(wsjtx.parse(b"hello"))
        self.assertIsNone(wsjtx.parse(struct.pack(">III", wsjtx.MAGIC, 2, 2) + b"\x00\x00\x00\x09ab"))

    def test_null_string(self):
        d = struct.pack(">III", wsjtx.MAGIC, 2, 3) + b"\xff\xff\xff\xff"
        self.assertEqual(wsjtx.parse(d)["id"], "")

    def test_highlight_bytes(self):
        d = wsjtx.build_highlight("WSJT-X", "K1ABC", (0x0D, 0x47, 0xA1), (255, 255, 255))
        hdr = struct.pack(">III", wsjtx.MAGIC, 2, 13)
        self.assertTrue(d.startswith(hdr))
        # bg QColor: spec 1, alpha ffff, r/g/b scaled x257, pad 0
        bg = struct.pack(">bHHHHH", 1, 0xFFFF, 0x0D * 257, 0x47 * 257, 0xA1 * 257, 0)
        self.assertIn(bg, d)
        self.assertEqual(d[-1], 0)
        h = wsjtx.parse_highlight(d)
        self.assertEqual((h["call"], h["bg"], h["fg"]), ("K1ABC", (13, 71, 161), (255, 255, 255)))

    def test_highlight_clear_is_invalid_color(self):
        d = wsjtx.build_highlight("WSJT-X", "K1ABC", None, None)
        self.assertEqual(d.count(struct.pack(">bHHHHH", 0, 0, 0, 0, 0, 0)), 2)
        h = wsjtx.parse_highlight(d)
        self.assertIsNone(h["bg"])

    def test_parse_color(self):
        self.assertEqual(wsjtx.parse_color("#90CAF9"), (0x90, 0xCA, 0xF9))
        with self.assertRaises(ValueError):
            wsjtx.parse_color("blue")


class Callsigns(unittest.TestCase):
    def test_split(self):
        self.assertEqual(split_call("<W1AW/4>"), ("W1AW/4", "W1AW"))
        self.assertEqual(split_call("VE3/K1ABC/P"), ("VE3/K1ABC/P", "K1ABC"))
        for not_call in ("RR73", "FN42", "-12", "R-07", "73", "CQ", "POTA", "DX"):
            self.assertIsNone(split_call(not_call), not_call)
        self.assertEqual(base_call("kh6/k1abc"), "K1ABC")

    def test_band(self):
        self.assertEqual(band_of_hz(14074000), "20m")
        self.assertEqual(band_of_hz(50313000), "6m")
        self.assertIsNone(band_of_hz(0))

    def test_parse_message(self):
        self.assertEqual(parse_message("CQ K1ABC FN42")[:3], ("K1ABC", "FN42", True))
        self.assertEqual(parse_message("CQ POTA K1ABC FN42")[:3], ("K1ABC", "FN42", True))
        self.assertEqual(parse_message("N0CALL KS0XX EM17")[:3], ("KS0XX", "EM17", False))
        self.assertEqual(parse_message("N0CALL KS0XX RR73")[:3], ("KS0XX", "", False))
        self.assertEqual(parse_message("CQ <W1AW/0>")[:3], ("<W1AW/0>", "", True))
        self.assertEqual(parse_message("TNX 73 GL")[0], None)


class Adif(unittest.TestCase):
    SAMPLE = ("WSJT-X ADIF Export<eoh>\n<call:5>KS0XX <gridsquare:4>EM17 <mode:3>FT8 <qso_date:8>20260310 "
              "<time_on:6>141500 <band:3>20m <eor>\n<call:6>W1AW/0 <qso_date:8>20260108 <time_on:4>0100 "
              "<band:3>40m <comment:9>a<b>c d e <eor>\n")

    def test_parse(self):
        r = adif.parse(self.SAMPLE)
        self.assertEqual(len(r), 2)
        self.assertEqual(r[0]["call"], "KS0XX")
        self.assertEqual(r[0]["gridsquare"], "EM17")
        self.assertEqual(r[1]["comment"], "a<b>c d e")
        self.assertEqual(r[1]["band"], "40m")

    def test_no_header_and_type_indicator(self):
        r = adif.parse("<CALL:4:S>K1AB<QSO_DATE:8:D>20260101<EOR>")
        self.assertEqual(r, [{"call": "K1AB", "qso_date": "20260101"}])


class AllTxt(unittest.TestCase):
    def test_line(self):
        d = alltxt.parse_line("261119_012015     3.573 Rx FT8      0  0.1 2027 CQ N9AAA EN52")
        self.assertEqual((d["mhz"], d["mode"], d["snr"], d["df"], d["message"], d["tx"]),
                         (3.573, "FT8", 0, 2027, "CQ N9AAA EN52", False))
        d = alltxt.parse_line("261119_012030    14.074 Tx FT4      0  0.0 1500 KS0XX N0CALL -10")
        self.assertTrue(d["tx"])
        self.assertIsNone(alltxt.parse_line("garbage"))

    def test_tail(self):
        import os
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), "ALL.TXT")
        write(p, "old line\n")
        t = alltxt.Tail(p)
        self.assertEqual(t.poll(), [])
        with open(p, "a") as f:
            f.write("new one\npartial")
        self.assertEqual(t.poll(), ["new one"])
        with open(p, "a") as f:
            f.write(" done\n")
        self.assertEqual(t.poll(), ["partial done"])


class CtyDat(unittest.TestCase):
    def test_lookup(self):
        c = cty()
        self.assertEqual(c.lookup("K1ABC"), "United States")
        self.assertEqual(c.lookup("JA1XYZ"), "Japan")
        self.assertEqual(c.lookup("W1AW/KH2"), "Guam")          # exact entry
        self.assertEqual(c.lookup("KH2/K1ABC"), "Guam")         # prefix part
        self.assertEqual(c.lookup("IT9ABC"), "Italy")           # WAE-only entity falls back
        self.assertNotIn("Sicily", c.entities)


if __name__ == "__main__":
    unittest.main()
