"""UDP simulator: a fake WSJT-X talks to a running daemon over loopback; no radio needed."""
import json
import socket
import struct
import threading
import time
import unittest
import urllib.request
from datetime import datetime, timezone

from test_awards import make_app
from award_hunter import wsjtx
from award_hunter.web.server import make_server

NOW = datetime(2026, 1, 8, 12, 0, tzinfo=timezone.utc)
DARK = ((0x0D, 0x47, 0xA1), (255, 255, 255))
LIGHT = ((0x90, 0xCA, 0xF9), (0, 0, 0))


class FakeWsjtx:
    def __init__(self, target, mcast=False):
        self.target = target
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if mcast:
            self.s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton("127.0.0.1"))
            self.s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        self.s.bind(("127.0.0.1", 0))
        self.s.settimeout(0.2)

    def send(self, data):
        self.s.sendto(data, self.target)

    def highlights(self, wait=1.0):
        out, end = [], time.time() + wait
        while time.time() < end:
            try:
                d, _ = self.s.recvfrom(4096)
            except socket.timeout:
                continue
            h = wsjtx.parse_highlight(d)
            if h:
                out.append(h)
        return out


def start(app, listen):
    app.cfg["wsjtx"]["listen"] = listen
    app.open_socket()
    threading.Thread(target=app.udp_loop, daemon=True).start()
    return app.sock.getsockname()[1]


class UdpSimulator(unittest.TestCase):
    def setUp(self):
        self.app = make_app(lambda: NOW)
        port = start(self.app, "127.0.0.1:0")
        self.w = FakeWsjtx(("127.0.0.1", port))
        self.w.send(wsjtx.build_heartbeat("WSJT-X"))
        self.w.send(wsjtx.build_status("WSJT-X", 14074000))

    def tearDown(self):
        self.app.stop.set()
        time.sleep(0.05)
        self.app.sock.close()
        self.w.s.close()

    def test_highlight_colors(self):
        for m in ("CQ KS0XX EM17", "CQ KY4AA EM78", "CQ K9NOPE EN50", "CQ <W1AW/0>", "CQ W1AW/5"):
            self.w.send(wsjtx.build_decode("WSJT-X", m))
        hl = {h["call"]: (h["bg"], h["fg"]) for h in self.w.highlights()}
        self.assertEqual(hl.get("KS0XX"), DARK)          # LoTW user in needed KS
        self.assertEqual(hl.get("KY4AA"), LIGHT)         # LoTW upload too old
        self.assertEqual(hl.get("W1AW/0"), DARK)         # this week's W1AW for NE
        self.assertNotIn("K9NOPE", hl)                   # unknown call
        self.assertNotIn("W1AW/5", hl)                   # not on this week
        heard = {r["call"]: r for r in self.app.engine.heard_now()}
        self.assertEqual(heard["KS0XX"]["band"], "20m")
        self.assertEqual(set(heard), {"KS0XX", "KY4AA", "W1AW/0"})

    def test_no_resend_then_clear_after_logged(self):
        self.w.send(wsjtx.build_decode("WSJT-X", "CQ KS0XX EM17"))
        self.assertEqual(len(self.w.highlights(0.5)), 1)
        self.w.send(wsjtx.build_decode("WSJT-X", "N0CALL KS0XX -10"))
        self.assertEqual(self.w.highlights(0.5), [])     # within resend window
        self.w.send(wsjtx.build_qso_logged("WSJT-X", "KS0XX", "EM17"))
        hl = self.w.highlights(0.5)
        self.assertEqual([(h["call"], h["bg"]) for h in hl], [("KS0XX", None)])
        self.w.send(wsjtx.build_decode("WSJT-X", "CQ KS0XX EM17"))
        self.assertEqual(self.w.highlights(0.5), [])     # worked: never again

    def test_untick_clears_highlight(self):
        self.w.send(wsjtx.build_decode("WSJT-X", "CQ KS0XX EM17"))
        self.assertEqual(len(self.w.highlights(0.5)), 1)
        self.app.action("america250", "toggle", {"state": "KS"})
        self.w.send(wsjtx.build_decode("WSJT-X", "CQ K9NOPE EN50"))   # any decode flushes changes
        self.assertEqual([(h["call"], h["bg"]) for h in self.w.highlights(0.5)], [("KS0XX", None)])

    def test_off_air_and_garbage_ignored(self):
        self.w.send(b"not wsjtx at all")
        d = bytearray(wsjtx.build_decode("WSJT-X", "CQ KS0XX EM17"))
        d[-1] = 1                                        # off_air (replayed .wav)
        self.w.send(bytes(d))
        self.assertEqual(self.w.highlights(0.5), [])
        self.w.send(wsjtx.build_decode("WSJT-X", "CQ KS0XX EM17"))
        self.assertEqual(len(self.w.highlights(0.5)), 1)

    def test_web_api(self):
        srv = make_server(self.app, bind="127.0.0.1", port=0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            self.w.send(wsjtx.build_decode("WSJT-X", "CQ KS0XX EM17"))
            self.w.highlights(0.4)
            s = json.load(urllib.request.urlopen(base + "/api/status"))
            self.assertTrue(s["wsjtx"]["connected"])
            self.assertEqual(s["heard"][0]["call"], "KS0XX")
            self.assertIn("america250", s["awards"])
            html = urllib.request.urlopen(base + "/").read()
            self.assertIn(b"award-hunter", html)
            req = urllib.request.Request(base + "/api/awards/america250/toggle", data=b'{"state":"KS"}',
                                         headers={"Content-Type": "application/json"})
            self.assertTrue(json.load(urllib.request.urlopen(req))["ok"])
            form = urllib.request.Request(base + "/api/awards/america250/toggle", data=b"state=KS",
                                          headers={"Content-Type": "application/x-www-form-urlencoded"})
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(form)
            self.assertEqual(cm.exception.code, 415)
        finally:
            srv.shutdown()
            srv.server_close()


class Multicast(unittest.TestCase):
    """Two listeners on the same multicast group/port (award-hunter + e.g. GridTracker) both get decodes."""

    def test_two_listeners_share_group(self):
        port = 22370 + int(time.time()) % 500
        group = "239.255.0.1"
        try:
            other = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            other.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            other.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            other.bind(("", port))
            other.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                             struct.pack("4s4s", socket.inet_aton(group), socket.inet_aton("127.0.0.1")))
            other.settimeout(1.0)
            app = make_app(lambda: NOW)
            start(app, f"{group}:{port}")
        except OSError as e:
            self.skipTest(f"multicast not available here: {e}")
        w = FakeWsjtx((group, port), mcast=True)
        try:
            w.send(wsjtx.build_status("WSJT-X", 14074000))
            w.send(wsjtx.build_decode("WSJT-X", "CQ KS0XX EM17"))
            hl = w.highlights(1.0)
            try:
                got_other = [wsjtx.parse(other.recvfrom(4096)[0])["type"] for _ in range(2)]
            except socket.timeout:
                got_other = []
            if not hl and not got_other:
                self.skipTest("multicast loopback not delivered on this host")
            self.assertEqual([h["call"] for h in hl], ["KS0XX"])
            self.assertEqual(got_other, [wsjtx.STATUS, wsjtx.DECODE])
        finally:
            app.stop.set()
            time.sleep(0.05)
            app.sock.close()
            other.close()
            w.s.close()


if __name__ == "__main__":
    unittest.main()
