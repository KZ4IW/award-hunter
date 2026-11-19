"""DXCC entities not yet in your log (entity resolved with WSJT-X's cty.dat)."""
from . import Award, Need


class DXCC(Award):
    key = "dxcc"
    title = "DXCC"
    priority = 70

    def __init__(self, cfg):
        super().__init__(cfg)
        self.worked = set()
        self.cty = None

    def reload(self, ctx):
        self.cty = ctx.cty
        w = set()
        for r in ctx.adif:
            e = ctx.cty.lookup(r["call"]) if r.get("call") and ctx.cty else None
            if e:
                w.add(e)
        self.worked = w

    def classify(self, st):
        if st.dxcc and st.dxcc not in self.worked:
            return Need(self.priority, self.key, st.dxcc, "new DXCC entity")
        return None

    def on_logged(self, call, grid, band):
        e = self.cty.lookup(call) if self.cty else None
        if e:
            self.worked.add(e)

    def panel(self):
        total = len(self.cty.entities) if self.cty else 0
        return {"title": self.title, "kind": "list", "items": [], "worked_count": len(self.worked), "total": total}
