import json
import threading
import unittest
import urllib.error
import urllib.request

from test_awards import make_app
from award_hunter import config
from award_hunter.state import Store
from award_hunter.daemon import App
from award_hunter.web.server import make_server


class CleanStation(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(config.clean_station(" kz4iw ", "fm16tj"), ("KZ4IW", "FM16"))
        self.assertEqual(config.clean_station("VE3/K1ABC/P", "FN03"), ("VE3/K1ABC/P", "FN03"))

    def test_invalid(self):
        for call, grid in (("", "EM48"), ("HELLO", "EM48"), ("K1ABC", "ZZ99"), ("K1ABC", "EM4"), ("K1ABC", "")):
            with self.assertRaises(ValueError, msg=(call, grid)):
                config.clean_station(call, grid)


class Settings(unittest.TestCase):
    def setUp(self):
        self.app = make_app(None)            # config: N0CALL / EM48

    def test_override_and_reset(self):
        self.app.cfg["_station_default"] = {"call": "N0CALL", "grid": "EM48"}
        self.app.cfg["_station_source"] = {"call": "config", "grid": "config"}
        self.assertEqual(self.app.station_source(), "config")
        r = self.app.update_settings({"call": "kz4iw", "grid": "FM16tj"})
        self.assertTrue(r["ok"])
        self.assertEqual(self.app.cfg["station"], {"call": "KZ4IW", "grid": "FM16"})
        self.assertEqual(self.app.status()["station"]["source"], "web")
        # survives a restart
        app2 = App(self.app.cfg | {"station": {"call": "N0CALL", "grid": "EM48"}},
                   store=Store(self.app.store.path), log=lambda *a: None)
        self.assertEqual(app2.cfg["station"]["call"], "KZ4IW")
        self.app.update_settings({"reset": True})
        self.assertEqual(self.app.cfg["station"], {"call": "N0CALL", "grid": "EM48"})
        self.assertEqual(self.app.station_source(), "config")

    def test_bad_input_keeps_old(self):
        with self.assertRaises(ValueError):
            self.app.update_settings({"call": "K1ABC", "grid": "nope"})
        self.assertEqual(self.app.cfg["station"]["grid"], "EM48")

    def test_grid_change_drops_band_tips_but_keeps_rate_limit(self):
        t = self.app.tipper
        t.raw, t.raw_time, t.result = [], 1000.0, {"bands": {}}
        t.grid_changed(now=1100.0)
        self.assertIsNone(t.result)
        self.assertEqual(t.next_at, 1300.0)       # still 300 s after the last query

    def test_api(self):
        srv = make_server(self.app, bind="127.0.0.1", port=0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_address[1]}/api/settings"
        post = lambda body: urllib.request.urlopen(urllib.request.Request(
            url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}))
        try:
            self.assertTrue(json.load(post({"call": "W1AW", "grid": "FN31"}))["ok"])
            self.assertEqual(self.app.cfg["station"]["grid"], "FN31")
            with self.assertRaises(urllib.error.HTTPError) as cm:
                post({"call": "W1AW", "grid": "XX"})
            self.assertEqual(cm.exception.code, 400)
            self.assertIn("grid", json.load(cm.exception)["error"])
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
