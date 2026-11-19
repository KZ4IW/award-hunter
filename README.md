# award-hunter

*Designed by KZ4IW* · project page: <https://kz4iw.link/award-hunter/>

Spot the stations you still need for an award while you run FT8/FT4 in WSJT-X.

award-hunter listens to WSJT-X's UDP feed and checks each decoded callsign against your
needed lists: US states (WAS), the 2026 ARRL **America250 WAS** event with its weekly
W1AW/n schedule, grid squares, and DXCC entities. Stations you need are:

- **highlighted in WSJT-X's Band Activity window.** Dark blue with white text means the
  station uploaded to LoTW in the last year, so a contact will probably confirm. Light blue
  with black text means it hasn't. Blue is the default because WSJT-X already uses magenta
  for its own coloring. You can change the colors.
- **listed in a browser page** (`http://127.0.0.1:8073`), along with a per-band table from
  PSK Reporter showing which band is bringing needed stations into your grid right now;
- **pushed to you** if you want: a desktop notification, and/or a phone alert through
  [ntfy](https://ntfy.sh) (ntfy.sh or your own server).

It never transmits and never sends WSJT-X anything except highlight messages.

Linux, Python 3.11+, no third-party Python packages. GPL-3.0-or-later.

## Install

```bash
sudo apt install pipx libnotify-bin poppler-utils   # notify-send and pdftotext are optional
git clone https://github.com/KZ4IW/award-hunter.git && cd award-hunter
pipx install .
award-hunter init        # writes ~/.config/award-hunter/config.toml (call/grid taken from WSJT-X)
```

Run it as a background service for your user (no root needed):

```bash
mkdir -p ~/.config/systemd/user
cp packaging/award-hunter.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now award-hunter
journalctl --user -u award-hunter -f
```

Or run it by hand: `award-hunter run`.

### First run downloads

On first start (and weekly after that) award-hunter downloads:

- the FCC ULS amateur license database (`l_amat.zip`, about 150 MB) from data.fcc.gov. It
  reduces this to a callsign→state table of about 10 MB and deletes the zip;
- the ARRL LoTW user activity list (`lotw-user-activity.csv`).

Both are stored in `~/.cache/award-hunter/`. Run `award-hunter refresh` to fetch them now.
Until the FCC table exists, award-hunter can't tell which state a call is in.

## Point WSJT-X at it

WSJT-X → **File › Settings › Reporting**:

| setting | value |
|---|---|
| UDP Server | `239.255.0.1` |
| UDP Server port number | `2237` |
| Outgoing interfaces | tick **loopback** (`lo`) |
| Accept UDP requests | **ticked** (needed for highlighting) |

`239.255.0.1` is a multicast address. Every program that joins the group gets every
datagram, so **GridTracker2 and award-hunter can both listen on port 2237 at the same time.**
You don't need a relay chain or any port juggling. In GridTracker, set its WSJT-X UDP
setting to the same multicast address and port.

Already forwarding WSJT-X to a unicast port? Set `listen = "127.0.0.1:2239"` (or whatever
port you use) under `[wsjtx]` in the config. If WSJT-X can't send UDP to award-hunter at
all, it falls back to reading `ALL.TXT`: you still get the browser list and alerts, but no
highlighting.

## Using the browser page

Open <http://127.0.0.1:8073>. On a tablet over RDP, just open it in the remote desktop's
browser.

- **Needed stations heard:** needed stations decoded in the last 15 minutes, colored the
  same way as in WSJT-X.
- **Bands:** needed stations that PSK Reporter receivers in your 4-character grid heard on
  each band in the last 15 minutes, with your current band marked. Queries run at most every
  5 minutes and only while WSJT-X is decoding.
- **America250 / WAS:** click a state to step it through have it → needed (blue) → worked,
  awaiting credit (dashed) → have it. Or open **Paste award credits**: select the whole state
  list on the award's credits page, copy it, paste it here, and award-hunter works out what's
  still needed. A W1AW/n contact during that state's week turns it dashed on its own; mark
  other worked-not-credited states dashed with a click. The W1AW/n stations on the air this
  week for states you need are listed. The 2026 schedule is built in; **W1AW schedule** lets
  you import a newer ARRL PDF.

When you log a QSO, that station's highlight is cleared straight away. It's also cleared on
the next start, because award-hunter reads `wsjtx_log.adi`.

## Configuration

`~/.config/award-hunter/config.toml`. Every key is optional; see `DESIGN.md` §2 for all
of them. Common ones:

```toml
[highlight]
lotw_bg = "#0D47A1"   # LoTW user
other_bg = "#90CAF9"  # not a recent LoTW user

[notify]
desktop = true
ntfy_url = "https://ntfy.sh/pick-a-long-random-topic"
ntfy_token_file = ""  # bearer token file, for a protected self-hosted server

[awards.was]
enabled = true        # plain WAS (keeps going after America250 ends on 31 Dec 2026)

[awards.grids]
enabled = true
watch = ["DN83", "EM77"]   # empty list = any grid not yet in your log

[awards.dxcc]
enabled = true
```

Your callsign and grid come from WSJT-X (My Call / My Grid) unless you set them under
`[station]`. You can also change them in the **Settings** panel on the page (or click the
callsign in the header). A value saved there overrides both, and "Use WSJT-X / config file"
undoes it.

Changes made in the browser page (ticked states, credits, schedule) are saved to
`~/.local/state/award-hunter/state.json`. award-hunter never rewrites your config file.

### Reaching the page from another device

By default the page only listens on `127.0.0.1`. To open it straight from a tablet on your
LAN or Tailscale network, create a token file and bind to that interface:

```bash
mkdir -p ~/.config/award-hunter
head -c 24 /dev/urandom | base64 > ~/.config/award-hunter/web-token
chmod 600 ~/.config/award-hunter/web-token
```

```toml
[web]
bind = "100.x.y.z"     # your Tailscale IP, or 0.0.0.0
token_file = "~/.config/award-hunter/web-token"
```

award-hunter won't start with a non-loopback address unless a token is set. The browser asks
for the token once and remembers it.

## Trying it without a radio

```bash
award-hunter simulate "CQ K0ABC EN10" "CQ W1AW/0" "CQ DX JA1ZZZ PM95"
```

This pretends to be WSJT-X, sends those decodes to the running daemon, and prints the
highlight messages it gets back.

## Development

```bash
python3 -m unittest discover -s tests     # stdlib only; includes a UDP simulator test
PYTHONPATH=src python3 -m award_hunter run
```

Award plugins live in `src/award_hunter/plugins/`. Third-party packages can add more through
the `award_hunter.plugins` entry point. `DESIGN.md` covers the architecture.

## Data and caveats

- Callsign→state comes from the licensee's mailing address in the FCC database, not from
  where the station is operating. A portable or mobile station may count for a different
  state.
- "LoTW user" means the station uploaded to LoTW within `lotw_days` (365 by default). It
  doesn't guarantee your QSO will be confirmed.
- PSK Reporter is a free community service. award-hunter queries it at most every 5
  minutes, backs off on errors, and stops querying while you're not decoding. Please keep it
  that way.

## License

Copyright (C) 2026 KZ4IW. GPL-3.0-or-later. See `LICENSE`. Inspired by, and protocol-compatible with, WSJT-X
(GPL-3.0).
