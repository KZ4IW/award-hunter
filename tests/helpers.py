import copy
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from award_hunter import config  # noqa: E402
from award_hunter.data import Cty  # noqa: E402


def make_cfg(**awards):
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["station"].update(call="N0CALL", grid="EM48")
    cfg["notify"]["desktop"] = False
    cfg["data"]["auto_refresh"] = False
    cfg["wsjtx"]["alltxt"] = False
    tmp = tempfile.mkdtemp(prefix="ah-test-")
    cfg["wsjtx"]["log_dir"] = tmp
    if awards:
        cfg["awards"] = copy.deepcopy(config.DEFAULTS["awards"])
        for k in cfg["awards"]:
            cfg["awards"][k]["enabled"] = False
        for k, v in awards.items():
            cfg["awards"][k] = {"enabled": True, **v}
    return cfg, tmp


CTY = """United States:            05:  08:  NA:   37.53:    91.67:     5.0:  K:
    AA,AB,AC,K,N,W;
Japan:                    25:  45:  AS:   36.40:  -138.38:    -9.0:  JA:
    JA,JE,JF,7J;
Guam:                     27:  64:  OC:   13.37:  -144.70:   -10.0:  KH2:
    AH2,KH2,NH2,WH2,=W1AW/KH2;
Italy:                    15:  28:  EU:   42.82:   -12.58:    -1.0:  I:
    I,IK;
Sicily:                   15:  28:  EU:   37.50:   -14.00:    -1.0:  *IT9:
    IT9;
"""


def write(path, text):
    with open(path, "w") as fh:
        fh.write(text)


def cty():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "cty.dat")
    write(p, CTY)
    return Cty.load(p)
