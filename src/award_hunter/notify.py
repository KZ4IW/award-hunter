"""Desktop notifications (notify-send / libnotify) and optional ntfy push."""
import os
import shutil
import subprocess
import threading
import urllib.request

from . import __version__


class Notifier:
    def __init__(self, cfg, log=print):
        n = cfg["notify"]
        self.desktop = n["desktop"] and shutil.which("notify-send") is not None
        self.url = n["ntfy_url"].strip()
        self.token_file = n["ntfy_token_file"]
        self.log = log

    def _token(self):
        if not self.token_file:
            return ""
        try:
            with open(os.path.expanduser(self.token_file)) as fh:
                return fh.read().strip()
        except OSError:
            return ""

    def send(self, title, body, tags="radio", priority="default"):
        threading.Thread(target=self._send, args=(title, body, tags, priority), daemon=True).start()

    def _send(self, title, body, tags, priority):
        if self.desktop:
            try:
                subprocess.run(["notify-send", "-a", "award-hunter", "-u",
                                "critical" if priority == "high" else "normal", title, body],
                               timeout=10, check=False, capture_output=True)
            except Exception as e:
                self.log(f"notify-send failed: {e!r}")
        if self.url:
            headers = {"Title": title.encode("utf-8").decode("latin-1", "replace"), "Tags": tags,
                       "Priority": priority, "User-Agent": f"award-hunter/{__version__}"}
            tok = self._token()
            if tok:
                headers["Authorization"] = f"Bearer {tok}"
            try:
                req = urllib.request.Request(self.url, data=body.encode(), method="POST", headers=headers)
                urllib.request.urlopen(req, timeout=20).read()
            except Exception as e:
                self.log(f"ntfy publish failed: {e!r}")
