"""JSON state the UI edits (needed lists, credits, schedule overrides), written atomically."""
import json
import os
import threading


class Store:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.data = {}
        self.version = 0
        try:
            with open(path) as fh:
                self.data = json.load(fh)
        except (OSError, ValueError):
            self.data = {}

    def section(self, key):
        with self.lock:
            return self.data.setdefault(key, {})

    def save(self):
        with self.lock:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(self.data, fh, indent=1, sort_keys=True)
            os.replace(tmp, self.path)
            self.version += 1
