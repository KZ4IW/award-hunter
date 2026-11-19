"""TOML configuration with defaults, plus XDG paths."""
import copy
import os
import re
import tomllib

APP = "award-hunter"


def _xdg(var, fallback):
    return os.path.join(os.environ.get(var) or os.path.expanduser(fallback), APP)


CONFIG_DIR = _xdg("XDG_CONFIG_HOME", "~/.config")
STATE_DIR = _xdg("XDG_STATE_HOME", "~/.local/state")
CACHE_DIR = _xdg("XDG_CACHE_HOME", "~/.cache")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.toml")
WSJTX_INI = os.path.expanduser("~/.config/WSJT-X.ini")

DEFAULTS = {
    "station": {"call": "", "grid": ""},
    "wsjtx": {"listen": "239.255.0.1:2237", "interface": "", "log_dir": "~/.local/share/WSJT-X",
              "alltxt": True},
    "adif": {"paths": []},
    "highlight": {"enabled": True, "lotw_bg": "#0D47A1", "lotw_fg": "#FFFFFF", "other_bg": "#90CAF9",
                  "other_fg": "#000000", "lotw_days": 365, "resend_seconds": 300},
    "notify": {"desktop": True, "ntfy_url": "", "ntfy_token_file": "", "cooldown_seconds": 900,
               "on_needed": True, "on_bandtip": True},
    "bandtips": {"enabled": True, "interval_seconds": 600, "modes": ["FT8", "FT4"]},
    "data": {"refresh_days": 7, "states_tsv": "", "lotw_csv": "",
             "fcc_url": "https://data.fcc.gov/download/pub/uls/complete/l_amat.zip",
             "lotw_url": "https://lotw.arrl.org/lotw-user-activity.csv", "auto_refresh": True},
    "web": {"bind": "127.0.0.1", "port": 8073, "token_file": ""},
    "awards": {"america250": {"enabled": True}, "was": {"enabled": False, "since": "1900-01-01"},
               "grids": {"enabled": False, "watch": []}, "dxcc": {"enabled": False}},
}

TEMPLATE = """# award-hunter configuration. Every setting is optional; see DESIGN.md section 2.

[station]
call = "{call}"
grid = "{grid}"

[wsjtx]
# In WSJT-X: Settings > Reporting > UDP Server = 239.255.0.1, port 2237,
# Outgoing interfaces = loopback. GridTracker can listen on the same group.
listen = "239.255.0.1:2237"

[highlight]
lotw_bg = "#0D47A1"   # recent LoTW user
lotw_fg = "#FFFFFF"
other_bg = "#90CAF9"  # not a recent LoTW user
other_fg = "#000000"

[notify]
desktop = true
ntfy_url = ""          # e.g. "https://ntfy.sh/your-secret-topic"
ntfy_token_file = ""   # optional file with a bearer token

[bandtips]
enabled = true
interval_seconds = 600

[web]
bind = "127.0.0.1"
port = 8073

[awards.america250]
enabled = true

[awards.was]
enabled = false

[awards.grids]
enabled = false
watch = []

[awards.dxcc]
enabled = false
"""


def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def wsjtx_ini(path=WSJTX_INI):
    """MyCall / MyGrid from WSJT-X's settings file, if present."""
    out = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return out
    for key, name in (("MyCall", "call"), ("MyGrid", "grid")):
        m = re.search(rf"^{key}=(\S+)", text, re.M)
        if m:
            out[name] = m.group(1).upper()
    return out


def load(path=None):
    cfg = copy.deepcopy(DEFAULTS)
    path = path or CONFIG_FILE
    if os.path.exists(path):
        with open(path, "rb") as fh:
            _merge(cfg, tomllib.load(fh))
    ini = wsjtx_ini()
    for k in ("call", "grid"):
        if not cfg["station"][k]:
            cfg["station"][k] = ini.get(k, "")
    cfg["station"]["call"] = cfg["station"]["call"].upper()
    cfg["station"]["grid"] = cfg["station"]["grid"].upper()[:4]
    cfg["wsjtx"]["log_dir"] = os.path.expanduser(cfg["wsjtx"]["log_dir"])
    cfg["bandtips"]["interval_seconds"] = max(300, int(cfg["bandtips"]["interval_seconds"]))
    cfg["_path"] = path
    return cfg


def write_template(path=None):
    path = path or CONFIG_FILE
    if os.path.exists(path):
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ini = wsjtx_ini()
    with open(path, "w") as fh:
        fh.write(TEMPLATE.format(call=ini.get("call", ""), grid=ini.get("grid", "")[:4]))
    return True


def parse_hostport(s, default_port=2237):
    host, _, port = s.rpartition(":")
    if not host:
        return s, default_port
    return host, int(port)
