import unittest
from datetime import datetime, timezone

from test_awards import make_app
from award_hunter import pskreporter

XML = b"""<?xml version="1.0"?>
<receptionReports>
 <receptionReport receiverCallsign="N4RX" receiverLocator="EM48ab" senderCallsign="KS0XX" senderLocator="EM17"
   frequency="14075123" sNR="-5" mode="FT8" senderRegion="Kansas" senderDXCC="United States" senderLotwUpload="2026-11-01"/>
 <receptionReport receiverCallsign="N4RY" receiverLocator="EM48cd" senderCallsign="KS0XX" senderLocator="EM17"
   frequency="14075500" sNR="2" mode="FT8" senderRegion="Kansas" senderDXCC="United States" senderLotwUpload="2026-11-01"/>
 <receptionReport receiverCallsign="N4RX" receiverLocator="EM48ab" senderCallsign="K7NEW" senderLocator="DN13"
   frequency="7074000" sNR="-15" mode="FT4" senderRegion="Idaho" senderDXCC="United States"/>
 <receptionReport receiverCallsign="N4RX" receiverLocator="EM48ab" senderCallsign="K7CW" frequency="7030000"
   sNR="5" mode="CW" senderRegion="Idaho" senderDXCC="United States"/>
 <receptionReport receiverCallsign="FAR" receiverLocator="FN42" senderCallsign="K7FAR" frequency="21074000"
   sNR="5" mode="FT8" senderRegion="Idaho" senderDXCC="United States"/>
 <receptionReport receiverCallsign="N4RX" receiverLocator="EM48ab" senderCallsign="W1AW/0" frequency="21074000"
   sNR="-20" mode="FT8"/>
</receptionReports>"""

NOW = datetime(2026, 1, 8, 12, 0, tzinfo=timezone.utc)


class PskReporter(unittest.TestCase):
    def test_query_url(self):
        u = pskreporter.query_url("EM48")
        self.assertIn("callsign=EM48", u)
        self.assertIn("modify=grid", u)
        self.assertIn("flowStartSeconds=-900", u)
        self.assertIn("rronly=1", u)

    def test_analyze(self):
        app = make_app(NOW)
        bands = pskreporter.analyze(pskreporter.parse_reports(XML), "EM48", app.engine, {"FT8", "FT4"})
        self.assertEqual(list(bands), ["40m", "20m", "15m"])
        b20 = bands["20m"]["stations"][0]
        self.assertEqual((b20["call"], b20["label"], b20["lotw"], b20["snr"], b20["rx"]),
                         ("KS0XX", "KS", True, 2, ["N4RX", "N4RY"]))
        self.assertEqual(bands["20m"]["score"], 2)
        self.assertEqual(bands["40m"]["stations"][0]["label"], "ID")   # state from senderRegion
        self.assertEqual(bands["15m"]["score"], 5)                     # W1AW/0 this week; K7FAR outside grid

    def test_rate_limit_and_backoff(self):
        app = make_app(NOW)
        calls = []

        def fetch(grid):
            calls.append(grid)
            raise OSError("503")
        t = pskreporter.BandTipper(app.cfg, app.engine, "/nonexistent/x.json", log=lambda *a: None, fetch=fetch)
        self.assertGreaterEqual(t.interval, 300)
        app.engine.last_decode = 1000.0
        t.tick(now=1001.0)
        self.assertEqual(len(calls), 1)
        t.tick(now=1100.0)                       # inside backoff: no query
        self.assertEqual(len(calls), 1)
        t.tick(now=1001.0 + 600)                 # first backoff = interval
        self.assertEqual(len(calls), 1)          # idle: no decode in the last 10 min
        app.engine.last_decode = 1590.0
        t.tick(now=1001.0 + 601)
        self.assertEqual(len(calls), 2)
        self.assertEqual(t.backoff, 1200)        # doubled

    def test_interval_clamped(self):
        app = make_app(NOW)
        app.cfg["bandtips"]["interval_seconds"] = 30
        t = pskreporter.BandTipper(app.cfg, app.engine, "/nonexistent/x.json", log=lambda *a: None, fetch=lambda g: [])
        self.assertEqual(t.interval, 300)


if __name__ == "__main__":
    unittest.main()
