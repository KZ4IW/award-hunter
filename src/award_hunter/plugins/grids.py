"""Grid squares: a watch list, or every 4-character grid not yet in your log."""
from . import Award, Need


class Grids(Award):
    key = "grids"
    title = "Grid squares"
    priority = 30

    def __init__(self, cfg):
        super().__init__(cfg)
        self.watch = {g.upper()[:4] for g in cfg.get("watch", [])}
        self.worked = set()

    def reload(self, ctx):
        self.worked = {r["gridsquare"][:4].upper() for r in ctx.adif if len(r.get("gridsquare", "")) >= 4}

    def classify(self, st):
        g = st.grid
        if not g or g in self.worked:
            return None
        if self.watch and g not in self.watch:
            return None
        return Need(self.priority, self.key, g, "watched grid" if self.watch else "new grid")

    def on_logged(self, call, grid, band):
        if grid:
            self.worked.add(grid[:4].upper())

    def panel(self):
        return {"title": self.title, "kind": "list", "items": sorted(self.watch) or ["(any unworked grid)"],
                "worked_count": len(self.worked)}
