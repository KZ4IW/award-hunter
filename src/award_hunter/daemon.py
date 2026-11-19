"""The award-hunter daemon: wires inputs, engine, outputs and the web UI together."""
import os
import random
import select
import socket
import struct
import threading
import time
from datetime import datetime, timezone

from . import CREDIT, CREDIT_URL, __version__, adif, alltxt, config, data, wsjtx
from .callsign import band_of_hz, band_of_mhz
from .engine import Engine
from .notify import Notifier
from .plugins import Context, registry
from .pskreporter import BandTipper
from .state import Store


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


class Lookups:
    def __init__(self):
        self.states, self.lotw, self.cty = {}, {}, None


class App:
    def __init__(self, cfg, store=None, now=None, log=log):
        self.cfg, self.log, self.now = cfg, log, now
        self.store = store or Store(os.path.join(config.STATE_DIR, "state.json"))
        self.cache = config.CACHE_DIR
        self.lk = Lookups()
        reg = registry()
        self.plugins = {k: reg[k](v) for k, v in cfg["awards"].items() if v.get("enabled") and k in reg}
        self.notifier = Notifier(cfg, log)
        self.engine = Engine(cfg, self.plugins, self.lk, self.notifier, log)
        self.tipper = BandTipper(cfg, self.engine, os.path.join(self.cache, "pskreporter.json"), self.notifier, log)
        self.instances = {}          # WSJT-X id -> (addr, schema, last seen)
        self.sock = None
        self.udp_packets = 0
        self.last_udp_decode = 0.0
        self.stop = threading.Event()
        self._adif_sig = None
        self._store_ver = -1
        self.lock = threading.RLock()
        self.apply_station(self.store.section("settings").get("station"))

    # ------------------------------------------------------------ paths & data
    @property
    def log_dir(self):
        return self.cfg["wsjtx"]["log_dir"]

    def adif_paths(self):
        return [os.path.expanduser(p) for p in self.cfg["adif"]["paths"]] or [os.path.join(self.log_dir, "wsjtx_log.adi")]

    def states_path(self):
        return os.path.expanduser(self.cfg["data"]["states_tsv"]) or os.path.join(self.cache, "callsign_states.tsv")

    def lotw_path(self):
        return os.path.expanduser(self.cfg["data"]["lotw_csv"]) or os.path.join(self.cache, "lotw-user-activity.csv")

    def load_lookups(self):
        t0 = time.monotonic()
        self.lk.states = data.load_states(self.states_path())
        self.lk.lotw = data.load_lotw(self.lotw_path())
        self.lk.cty = data.Cty.load(os.path.join(self.log_dir, "cty.dat"))
        if not self.lk.cty.entities:
            self.lk.cty = data.Cty.load("/usr/share/wsjtx/cty.dat")
        self.log(f"lookups: {len(self.lk.states)} US calls, {len(self.lk.lotw)} LoTW users, "
                 f"{len(self.lk.cty.entities)} DXCC entities ({time.monotonic() - t0:.1f}s)")

    def refresh_data(self, force=False):
        """Download FCC ULS + LoTW if older than refresh_days (or forced). Runs in a thread."""
        os.makedirs(self.cache, exist_ok=True)
        days = self.cfg["data"]["refresh_days"]
        changed = False
        if not self.cfg["data"]["lotw_csv"]:
            a = data.age_days(self.lotw_path())
            if force or a is None or a > days:
                try:
                    changed |= data.download(self.cfg["data"]["lotw_url"], self.lotw_path())
                    self.engine.event("LoTW user list refreshed")
                except Exception as e:
                    self.engine.event(f"LoTW download failed: {e!r}")
        if not self.cfg["data"]["states_tsv"]:
            a = data.age_days(self.states_path())
            if force or a is None or a > days:
                zp = os.path.join(self.cache, "l_amat.zip")
                try:
                    self.engine.event("downloading FCC ULS amateur database (~150 MB)...")
                    data.download(self.cfg["data"]["fcc_url"], zp, timeout=900)
                    n = data.build_states_tsv(zp, self.states_path())
                    os.unlink(zp)
                    changed = True
                    self.engine.event(f"FCC ULS: {n} active US licenses")
                except Exception as e:
                    self.engine.event(f"FCC ULS refresh failed: {e!r}")
        if changed:
            self.load_lookups()
            self.engine.reloaded()
            self.tipper.recompute()

    def context(self, records):
        return Context(cfg=self.cfg, store=self.store, adif=records, states=self.lk.states, lotw=self.lk.lotw,
                       cty=self.lk.cty, now=self.now() if callable(self.now) else self.now)

    def maybe_reload(self, force=False):
        sig = []
        for p in self.adif_paths():
            try:
                sig.append(os.path.getmtime(p))
            except OSError:
                sig.append(0)
        sig = tuple(sig)
        if not force and sig == self._adif_sig and self.store.version == self._store_ver:
            return False
        records = []
        for p in self.adif_paths():
            records += adif.read(p)
        with self.engine.lock:
            ctx = self.context(records)
            for p in self.plugins.values():
                try:
                    p.reload(ctx)
                except Exception as e:
                    self.log(f"plugin {p.key} reload error: {e!r}")
            self._adif_sig, self._store_ver = sig, self.store.version
            self.engine.reloaded()
        self.tipper.recompute()
        return True

    # ------------------------------------------------------------ settings
    def station_source(self):
        if self.store.section("settings").get("station"):
            return "web"
        return self.cfg.get("_station_source", {}).get("call") or ""

    def apply_station(self, st):
        """Set call/grid (None = back to config file / WSJT-X values). A new grid drops band tips."""
        default = self.cfg.get("_station_default", self.cfg["station"])
        new = dict(st) if st else dict(default)
        old_grid = self.cfg["station"]["grid"]
        self.cfg["station"].update(call=new["call"], grid=new["grid"])
        if new["grid"] != old_grid and hasattr(self, "tipper"):
            self.tipper.grid_changed()

    def update_settings(self, payload):
        with self.lock:
            sec = self.store.section("settings")
            if payload.get("reset"):
                sec.pop("station", None)
                msg = "using the call and grid from your config file / WSJT-X again"
            else:
                call, grid = config.clean_station(payload.get("call"), payload.get("grid"))
                sec["station"] = {"call": call, "grid": grid}
                msg = f"saved: {call} in {grid}"
            self.store.save()
            self.apply_station(sec.get("station"))
            self.engine.event(f"settings: {msg}")
            return {"ok": True, "message": msg}

    # ------------------------------------------------------------ UDP
    def open_socket(self):
        host, port = config.parse_hostport(self.cfg["wsjtx"]["listen"])
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        group = host if 224 <= int(host.split(".")[0]) <= 239 else None
        if group:
            s.bind(("", port))
            iface = self.cfg["wsjtx"]["interface"]
            joined = 0
            if iface:
                s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                             socket.inet_aton(group) + socket.inet_aton(iface))
                joined = 1
            else:                       # join on every interface, loopback included
                for idx, name in socket.if_nameindex():
                    try:
                        mreqn = struct.pack("4s4si", socket.inet_aton(group), socket.inet_aton("0.0.0.0"), idx)
                        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreqn)
                        joined += 1
                    except OSError:
                        pass
            if not joined:
                raise OSError(f"could not join multicast group {group}")
            self.log(f"WSJT-X: listening on multicast {group}:{port} ({joined} interfaces)")
        else:
            s.bind((host, port))
            self.log(f"WSJT-X: listening on {host}:{s.getsockname()[1]}")
        self.sock = s
        return s

    def sender(self, wid):
        inst = self.instances.get(wid)
        if not inst or not self.sock:
            return None
        addr, schema, _ = inst

        def send(call, colors):
            bg, fg = colors if colors else (None, None)
            try:
                self.sock.sendto(wsjtx.build_highlight(wid, call, bg, fg, schema=min(schema, 3)), addr)
            except OSError as e:
                self.log(f"highlight send failed: {e!r}")
        return send

    def handle_datagram(self, dgram, src):
        m = wsjtx.parse(dgram)
        if not m:
            return
        self.udp_packets += 1
        wid = m["id"]
        if m["type"] in (wsjtx.HEARTBEAT, wsjtx.STATUS, wsjtx.DECODE):
            prev = self.instances.get(wid)
            if not prev or prev[0] != src:
                self.engine.on_clear()               # new/restarted WSJT-X: its highlight table is empty
                self.log(f"WSJT-X instance {wid!r} at {src[0]}:{src[1]}")
            self.instances[wid] = (src, m["schema"], time.time())
        if m["type"] == wsjtx.STATUS:
            self.engine.on_status(band_of_hz(m.get("dial", 0)), m.get("mode", ""))
        elif m["type"] == wsjtx.DECODE:
            if m.get("off_air") or m.get("low_conf"):
                return
            self.last_udp_decode = time.time()
            self.engine.on_decode(m["message"], snr=m["snr"], mode=self.engine.mode or m["mode"], send=self.sender(wid))
        elif m["type"] == wsjtx.CLEAR:
            if m.get("window", 0) in (0, 2):
                self.engine.on_clear()
        elif m["type"] == wsjtx.QSO_LOGGED:
            self.engine.on_logged(m["dx_call"], m.get("dx_grid", ""), band_of_hz(m.get("tx_freq", 0)) or "",
                                  send=self.sender(wid))
        elif m["type"] == wsjtx.CLOSE:
            self.instances.pop(wid, None)

    def udp_loop(self):
        while not self.stop.is_set():
            try:
                r, _, _ = select.select([self.sock], [], [], 1.0)
                if r:
                    dgram, src = self.sock.recvfrom(65535)
                    self.handle_datagram(dgram, src)
            except Exception as e:                   # never let one bad packet stop listening
                self.log(f"UDP handler error: {e!r}")

    # ------------------------------------------------------------ ALL.TXT
    def alltxt_loop(self):
        tail = alltxt.Tail(os.path.join(self.log_dir, "ALL.TXT"))
        while not self.stop.wait(2.0):
            try:
                for line in tail.poll():
                    d = alltxt.parse_line(line)
                    if not d or d["tx"]:
                        continue
                    band = band_of_mhz(d["mhz"])
                    if time.time() - self.last_udp_decode < 120:
                        continue                     # UDP is feeding us; avoid double counting
                    self.engine.on_status(band, d["mode"])
                    self.engine.on_decode(d["message"], snr=d["snr"], mode=d["mode"], band=band)
            except Exception as e:
                self.log(f"ALL.TXT error: {e!r}")

    # ------------------------------------------------------------ housekeeping
    def housekeeping_loop(self):
        next_refresh = time.time() + random.randint(30, 300)
        while not self.stop.wait(15.0):
            try:
                self.maybe_reload()
                self.tipper.tick()
                if self.cfg["data"]["auto_refresh"] and time.time() > next_refresh:
                    next_refresh = time.time() + 6 * 3600
                    self.refresh_data()
            except Exception as e:
                self.log(f"housekeeping error: {e!r}")

    def status(self):
        now = time.time()
        inst = [{"id": k, "addr": f"{v[0][0]}:{v[0][1]}", "age": round(now - v[2])} for k, v in self.instances.items()]
        return {
            "station": {"call": self.cfg["station"]["call"], "grid": self.cfg["station"]["grid"],
                        "source": self.station_source(), "default": self.cfg.get("_station_default", {})},
            "wsjtx": {"connected": any(i["age"] < 60 for i in inst), "instances": inst, "band": self.engine.band,
                      "mode": self.engine.mode, "packets": self.udp_packets,
                      "last_decode_age": round(now - self.engine.last_decode) if self.engine.last_decode else None,
                      "listen": self.cfg["wsjtx"]["listen"]},
            "heard": self.engine.heard_now(),
            "bands": self.tipper.result, "bandtip_error": self.tipper.error,
            "bandtip_next": round(self.tipper.next_at - now) if self.tipper.next_at > now else 0,
            "awards": {k: p.panel() for k, p in self.plugins.items()},
            "events": list(self.engine.events)[-40:][::-1],
            "data": {"us_calls": len(self.lk.states), "states_age_days": _r(data.age_days(self.states_path())),
                     "lotw_users": len(self.lk.lotw), "lotw_age_days": _r(data.age_days(self.lotw_path())),
                     "dxcc_entities": len(self.lk.cty.entities) if self.lk.cty else 0},
            "colors": self.cfg["highlight"],
            "about": {"version": __version__, "credit": CREDIT, "url": CREDIT_URL},
            "utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ"),
        }

    def action(self, key, name, payload):
        p = self.plugins.get(key)
        if not p:
            raise KeyError(key)
        with self.engine.lock:
            res = p.action(name, payload, self.context([]))
        self.maybe_reload(force=True)
        return res or {"ok": True}

    def run(self, with_web=True):
        self.load_lookups()
        self.maybe_reload(force=True)
        self.open_socket()
        threads = [self.udp_loop, self.housekeeping_loop]
        if self.cfg["wsjtx"]["alltxt"]:
            threads.append(self.alltxt_loop)
        for fn in threads:
            threading.Thread(target=fn, daemon=True, name=fn.__name__).start()
        if with_web:
            from .web.server import serve
            serve(self)
        else:
            self.stop.wait()


def _r(x):
    return None if x is None else round(x, 1)
