"""award-hunter command line."""
import argparse
import sys
import time

from . import CREDIT, __version__, config


def cmd_run(a):
    from .daemon import App
    cfg = config.load(a.config)
    if getattr(a, "port", None):
        cfg["web"]["port"] = a.port
    if not cfg["station"]["call"]:
        print("warning: no station call (set [station] call or MyCall in WSJT-X)", file=sys.stderr)
    print(f"award-hunter {__version__} - {CREDIT}", flush=True)
    App(cfg).run()


def cmd_init(a):
    path = a.config or config.CONFIG_FILE
    print(("wrote " if config.write_template(path) else "already exists: ") + path)


def cmd_refresh(a):
    from .daemon import App
    app = App(config.load(a.config))
    app.refresh_data(force=True)


def cmd_simulate(a):
    """Pretend to be WSJT-X: send a few decodes to the daemon and print highlights it sends back."""
    import socket
    from . import wsjtx
    host, port = config.parse_hostport(a.target)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton("127.0.0.1"))
    s.bind(("127.0.0.1", 0))
    s.settimeout(0.5)
    wid = "SIM"
    msgs = a.messages or ["CQ K0ABC EN10", "CQ W1AW/0 ", "N0CALL KS0XX EM17", "CQ DX JA1ZZZ PM95"]
    s.sendto(wsjtx.build_heartbeat(wid), (host, port))
    s.sendto(wsjtx.build_status(wid, int(a.freq * 1e6)), (host, port))
    for m in msgs:
        s.sendto(wsjtx.build_decode(wid, m, snr=-7), (host, port))
        print("decode ->", m)
    end = time.time() + 2
    while time.time() < end:
        try:
            d, _ = s.recvfrom(4096)
        except socket.timeout:
            continue
        h = wsjtx.parse_highlight(d)
        if h:
            print("highlight <-", h["call"], h["bg"], h["fg"])


def main(argv=None):
    p = argparse.ArgumentParser(prog="award-hunter", description=f"award-hunter: {CREDIT}")
    p.add_argument("--version", action="version", version=f"award-hunter {__version__} - {CREDIT}")
    p.add_argument("-c", "--config", help=f"config file (default {config.CONFIG_FILE})")
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("run", help="run the daemon and web UI (default)")
    r.add_argument("--port", type=int)
    sub.add_parser("init", help="write a starter config file")
    sub.add_parser("refresh", help="download FCC ULS and LoTW data now")
    s = sub.add_parser("simulate", help="send fake WSJT-X decodes to a running daemon")
    s.add_argument("--target", default="239.255.0.1:2237")
    s.add_argument("--freq", type=float, default=14.074)
    s.add_argument("messages", nargs="*")
    a = p.parse_args(argv)
    {"run": cmd_run, None: cmd_run, "init": cmd_init, "refresh": cmd_refresh, "simulate": cmd_simulate}[a.cmd](a)


if __name__ == "__main__":
    main()
