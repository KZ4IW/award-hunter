"""Needed-list plugins. An award plugin decides whether a heard station is still needed."""
from dataclasses import dataclass, field


@dataclass
class Station:
    call: str                 # as printed by WSJT-X (no <>), e.g. W1AW/4
    base: str                 # base call, e.g. W1AW
    state: str = ""           # FCC state for US calls
    lotw: bool = False        # uploaded to LoTW recently
    grid: str = ""            # 4-char grid if the message carried one
    dxcc: str = ""            # cty.dat entity name
    band: str = ""
    mode: str = ""
    snr: int = 0
    message: str = ""
    is_cq: bool = False


@dataclass(order=True)
class Need:
    priority: int
    award: str = field(compare=False)
    label: str = field(compare=False)       # e.g. "KS", "W1AW/0 NE", "EM77", "Japan"
    reason: str = field(compare=False)      # human text
    force_lotw: bool = field(default=False, compare=False)


@dataclass
class Context:
    cfg: dict
    store: object             # state.Store
    adif: list                # ADIF records
    states: dict              # call -> state
    lotw: dict                # call -> last upload date
    cty: object               # data.Cty
    now: object = None        # datetime (UTC), tests may pin it


class Award:
    key = "award"
    title = "Award"
    priority = 10

    def __init__(self, cfg):
        self.cfg = cfg        # this award's [awards.<key>] table

    def reload(self, ctx):
        pass

    def classify(self, st):
        return None

    def on_logged(self, call, grid, band):
        pass

    def panel(self):
        return {}

    def action(self, name, payload, ctx):
        raise ValueError(f"unknown action {name!r}")

    def needed_states(self):
        """US states this award still needs (used to resolve PSK Reporter senders)."""
        return set()


def registry():
    from .states import WAS, America250
    from .grids import Grids
    from .dxcc import DXCC
    reg = {c.key: c for c in (America250, WAS, Grids, DXCC)}
    try:                                       # third-party plugins
        from importlib.metadata import entry_points
        for ep in entry_points(group="award_hunter.plugins"):
            cls = ep.load()
            reg[cls.key] = cls
    except Exception:
        pass
    return reg
