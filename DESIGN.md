# award-hunter — design

*Designed by KZ4IW.*

award-hunter watches what WSJT-X decodes, works out which stations would give you
something you still need for an award (a US state, a W1AW/n America250 station, a grid,
a DXCC entity, …), and makes those stations stand out: highlighted in WSJT-X's Band
Activity window, listed in a browser UI, and optionally pushed to your desktop or phone.
It also asks PSK Reporter which band is bringing needed stations into your area right now.

It grew out of KZ4IW's personal scripts for the ARRL America250 WAS
event. Those were hard-coded to one callsign, grid, server and set of files. This project
is the general, installable version. America250 ends on 31 December 2026, so the award is
a plugin, not the core: the same engine serves plain WAS, grids, DXCC and (later) POTA.

Scope: Linux first (Ubuntu/Debian/Fedora), FT8/FT4 via WSJT-X 2.6+ / 3.x and anything that
speaks the WSJT-X UDP protocol (JTDX, MSHV in compatible mode). CW/SSB are out of scope for
the decode side; PSK Reporter band tips are filtered to FT8/FT4 by default.

---

## 1. Shape of the program

```
                      +------------------------- award-hunter daemon (one Python process) -------------------------+
WSJT-X ──UDP mcast──► | wsjtx listener ─► decode bus ─► needed engine (plugins) ─► highlighter ──UDP unicast──► WSJT-X
239.255.0.1:2237      |                                   │        ▲                  │                            |
GridTracker also ◄────┤ (joins the group, does not relay) │        │                  ├─► notifier: libnotify, ntfy |
listens on the group  |                                   ▼        │                  │                            |
ALL.TXT (tail) ─────► | alltxt tailer ─► decode bus      heard-stations store ◄───────┘                            |
wsjtx_log.adi ──────► | adif watcher ─► worked set ──────────────────┘                                            |
PSK Reporter ◄──HTTP─ | band-tip poller (≥5 min, cached) ─► per-band table                                         |
FCC ULS, LoTW CSV ◄── | weekly data refresher ─► callsign→state, LoTW last-upload                                 |
                      | web UI (http.server, 127.0.0.1:8073) ◄── browser (local, or tablet over RDP/Tailscale)    |
                      +---------------------------------------------------------------------------------------------+
```

- **One process, stdlib first.** Python ≥ 3.11 (for `tomllib`). Threads, not asyncio: a UDP
  loop, a file tailer, a poller, a refresher and `ThreadingHTTPServer`. No third-party
  runtime dependencies. Optional system tools: `notify-send` (libnotify-bin) for desktop
  notifications and `pdftotext` (poppler-utils) for importing the ARRL schedule PDF.
- **Never in the radio's way.** Every handler is wrapped so an exception in a plugin or the
  UI cannot stop the UDP loop. The daemon never sends Reply/Halt/Free-text to WSJT-X; the
  only messages it sends are Highlight Callsign (type 13).
- **Local browser UI.** Served by the daemon on `127.0.0.1:8073`. Works on the desktop, over
  RDP from a tablet, and (opt-in, see §8) from the tablet directly over the LAN/Tailscale.

## 2. Configuration

TOML at `$XDG_CONFIG_HOME/award-hunter/config.toml` (default `~/.config/award-hunter/`).
Everything has a default; an empty file works. `award-hunter init` writes a commented file,
pre-filled with call and grid from `~/.config/WSJT-X.ini` when present.

```toml
[station]
call = "N0CALL"          # default: MyCall from WSJT-X.ini
grid = "EM48"            # 4 characters are enough; default: MyGrid from WSJT-X.ini

[wsjtx]
listen = "239.255.0.1:2237"   # multicast group (recommended) or a unicast host:port
interface = ""                # IP of the interface to join the group on ("" = all/loopback)
log_dir = "~/.local/share/WSJT-X"   # ALL.TXT and wsjtx_log.adi

[highlight]
enabled = true
lotw_bg = "#0D47A1"      # LoTW user: dark blue, white text
lotw_fg = "#FFFFFF"
other_bg = "#90CAF9"     # not a recent LoTW user: light blue, black text
other_fg = "#000000"
lotw_days = 365          # "LoTW user" = uploaded within this many days
resend_seconds = 300

[notify]
desktop = true
ntfy_url = ""            # e.g. "https://ntfy.sh/my-secret-topic" or your own server
ntfy_token_file = ""     # file holding a bearer token (never put the token in this file)
cooldown_seconds = 900

[bandtips]
enabled = true
interval_seconds = 600   # never below 300 (PSK Reporter's request)
modes = ["FT8", "FT4"]

[web]
bind = "127.0.0.1"
port = 8073

[awards.america250]      # each [awards.*] table enables a plugin
enabled = true

[awards.was]
enabled = false
since = "1900-01-01"

[awards.grids]
enabled = false
watch = ["EM77", "DN83"] # empty = every grid not yet in your log

[awards.dxcc]
enabled = false
```

**Config vs state.** The TOML file is what the user writes. Things the UI changes (ticked
states, an imported credits paste, the W1AW schedule, states marked worked by hand) live in
`$XDG_STATE_HOME/award-hunter/state.json`, written atomically. This keeps the daemon from
ever rewriting a hand-edited, commented TOML file. Data downloads live in
`$XDG_CACHE_HOME/award-hunter/`.

## 3. Needed-list engine

A plugin is a class with a small interface:

```python
class Award:
    key = "was"            # config table name
    title = "Worked All States"
    priority = 50          # higher wins when several awards want the same station

    def reload(self, ctx) -> None          # state/config/data changed
    def classify(self, st: Station) -> Need | None
    def ui_panel(self) -> dict             # JSON the browser UI renders (lists, counts)
    def handle_action(self, name, payload) -> dict   # UI edits, e.g. toggle a state
```

`Station` carries what the decode tells us plus lookups: `call` (as printed), `base`
(portable/prefix stripped), `state` (FCC), `lotw` (bool), `grid` (from a CQ or reply, if
any), `dxcc` (from cty.dat), `band`, `mode`, `snr`, `message`, `is_cq`.

`Need` is `(award, label, reason, priority)`, e.g. `("america250", "KS", "needed state", 60)`.
The engine asks every enabled plugin, keeps the highest-priority need, and drops anything
already worked for that award (from the ADIF watcher, per-award `since` date, and the live
QSO Logged message so a station stops being highlighted the moment you log it).

Built-in plugins:

| plugin | needed when | data |
|---|---|---|
| `was` | FCC state of a US call is unticked on your list | FCC ULS |
| `america250` | as `was`, plus the W1AW/n station scheduled this week (Wed 0000z–Tue 2359z) for a still-needed state; award-page credits paste imports the list; W1AW QSOs in the log mark the state worked-awaiting-credit | bundled 2026 schedule, PDF import |
| `grids` | a CQ carries a 4-char grid on your watch list, or not yet in your log | ADIF `GRIDSQUARE` |
| `dxcc` | prefix resolves (cty.dat) to an entity not in your log | WSJT-X's `cty.dat` |
| `pota` *(planned)* | a park you haven't hunted is spotted on pota.app and that activator is decoded | pota.app spots API |

Adding a plugin = one module in `award_hunter/plugins/` and an entry point
(`award_hunter.plugins`) so third-party packages can ship more.

**America250 after 2026.** The plugin stays installable for log review, but its `classify`
returns nothing after 2026-12-31 23:59z and the UI shows "event ended". The WAS plugin
carries on with the same state picker.

## 4. Inputs

**WSJT-X UDP, multicast.** WSJT-X's "UDP Server" is set to `239.255.0.1`, port `2237`, with
the loopback interface ticked under "Outgoing interfaces". award-hunter and GridTracker2 both
bind `0.0.0.0:2237` with `SO_REUSEADDR`/`SO_REUSEPORT` and join the group, so each gets every
datagram — no relay chain, no port juggling, either program can be stopped independently.
Replies (highlights) go unicast to the source address of WSJT-X's datagrams, which is where
WSJT-X listens for them. A unicast `listen = "127.0.0.1:2239"` mode is supported for setups
that already chain programs.

Parsed messages: Heartbeat (0), Status (1: dial frequency → band, mode, de call/grid),
Decode (2), Clear (3: reset our highlight bookkeeping), QSO Logged (5), Close (6). The parser
follows WSJT-X's `NetworkMessage.hpp` (big-endian Qt `QDataStream`; utf8 = u32 length +
bytes, `0xFFFFFFFF` = null; QDateTime = i64 julian day + u32 ms + u8 timespec [+ i32 offset]).

**ALL.TXT tail.** For people who cannot or will not change WSJT-X's UDP setting, and for
JTDX. Lines look like `261119_004815    14.074 Rx FT8    -12  0.2 1234 CQ K1ABC FN42`. Gives
everything except highlighting (no address to reply to).

**ADIF.** `wsjtx_log.adi` (or any ADIF via `[adif] paths = [...]`), re-read when its mtime
changes. Gives worked calls (per award window), worked grids, worked DXCC.

## 5. Outputs

**WSJT-X Highlight Callsign (type 13).** Payload: id, callsign, background QColor, foreground
QColor, `highlight_last` bool. QColor on the wire: `i8 spec, u16 alpha, u16 r, u16 g, u16 b,
u16 pad` with spec 1 = RGB and 8-bit values scaled ×257. An *invalid* colour (spec 0) clears
the highlight for that call. Defaults are blue because WSJT-X already uses magenta/pink for
its own "new" colouring: dark blue (white text) = recent LoTW user, light blue (black text) =
not. Colours are configurable. Bookkeeping: re-send at most every `resend_seconds`; clear
when worked, when the needed list changes, and forget everything on WSJT-X Clear/restart
(new source address or instance id).

**Desktop notifications** via `notify-send` (libnotify) — no D-Bus Python dependency.

**ntfy** (optional): POST to `ntfy_url` with `Title`, `Tags`, `Priority`; bearer token read
from a file. Works with ntfy.sh or a self-hosted server.

Alert events: first decode of a needed station (per call cooldown), this week's needed W1AW/n
heard, band tip (another band clearly better than yours).

## 6. Band tips (PSK Reporter)

One query per interval: `https://retrieve.pskreporter.info/query?callsign=<GRID4>
&modify=grid&flowStartSeconds=-900&rronly=1&noactive=1&nolocator=1&rptlimit=5000`.
Keep reports whose receiver locator starts with your grid, mode in `[bandtips] modes`,
sender in a needed state/entity, not already worked. `senderRegion` gives the state
(falls back to the FCC lookup), `senderLotwUpload` the LoTW flag. Score per band:
W1AW/n = 5, LoTW user = 2, others = 1. The UI shows the per-band table; a tip is pushed only
when WSJT-X is active, the best band isn't yours, it has at least one LoTW station, and
the same tip wasn't sent in the last 30 min.

**Rate limits.** PSK Reporter asks clients not to query more than once every 5 minutes.
The interval is clamped to ≥ 300 s, results are cached on disk and shared with the UI,
queries pause while WSJT-X is idle (no decode in 10 min), and HTTP 503/429 or errors back
off exponentially (to 1 h). A descriptive User-Agent `award-hunter/<version>` is sent.

## 7. Data sources

- **Callsign → state: FCC ULS.** The weekly amateur database
  `https://data.fcc.gov/download/pub/uls/complete/l_amat.zip` (~150 MB). Streamed through
  `zipfile` without extracting: `HD.dat` gives license status (keep `A`ctive), `EN.dat` gives
  call sign (field 5) and mailing state (field 18). Output: `callsign_states.tsv` (≈800k rows,
  ~10 MB) in the cache directory, loaded into a dict at start (~1 s). No dependence on
  third-party mirrors. An existing TSV (`CALL<TAB>ST`) can be imported instead.
  Caveat shown in the UI: the FCC address is the mailing address, not where the station is
  operating; portable suffixes are flagged.
- **LoTW users:** `https://lotw.arrl.org/lotw-user-activity.csv` (`CALL,date,time`), weekly.
- **DXCC:** WSJT-X's own `cty.dat` (Big CTY), read from its data directory.
- Refresh runs in the daemon (weekly, at a random minute, with `If-Modified-Since`), or by
  `award-hunter refresh`; failures keep the previous files.

## 8. Web UI

Vanilla HTML/CSS/JS served from the package (no build step, no CDN — works offline).
Polls `GET /api/status` every 3 s. Panels:

- **Heard now:** needed stations decoded in the last 15 min — call, award/state, LoTW,
  band, SNR, last message, age. Same colours as the WSJT-X highlight.
- **Bands:** PSK Reporter per-band table (score, stations, heard-by), your band marked.
- **Needed list:** the state grid — click to tick/untick; "Paste award credits" box; worked-
  awaiting-credit list; W1AW this week; schedule PDF upload (America250).
- **Status:** WSJT-X connected?, band/mode, data file ages, last PSK query, log.

API: `GET /api/status`, `POST /api/awards/<key>/<action>` (JSON). POSTs require
`Content-Type: application/json` (blocks cross-site form posts) and, when `bind` is not a
loopback address, a token from `[web] token_file` sent as `Authorization: Bearer`. The UI
is read/write for your own needed list only; it cannot transmit.

## 9. Packaging

- `pyproject.toml` (setuptools), console script `award-hunter`, package data (web assets,
  bundled schedule). Install: `pipx install .` (later `pipx install award-hunter` from PyPI).
- `packaging/award-hunter.service` — systemd **user** unit (`systemctl --user enable --now
  award-hunter`), `Restart=on-failure`, no root needed.
- Later: a `.deb` (debhelper + dh-python) that depends on `python3 (>= 3.11)` and recommends
  `libnotify-bin, poppler-utils`; Flatpak is a poor fit (needs host UDP + WSJT-X files).
- License: GPL-3.0-or-later (compatible with WSJT-X's GPL-3.0).

## 10. Testing

`python3 -m unittest` — stdlib only. Unit tests for each parser (WSJT-X wire format both
ways, ADIF, ALL.TXT, award-credits paste, schedule PDF text, PSK Reporter XML, FCC EN/HD
rows) and a UDP simulator test: a fake WSJT-X sends Status + Decode datagrams to a running
daemon on an ephemeral port and asserts it receives the right Highlight Callsign messages
(colours, clear after QSO Logged). A multicast variant runs when the host supports it.

## 11. Not in the MVP

POTA plugin, DXCC/grid "new on this band" granularity, HamAlert/telnet cluster input, `.deb`,
per-band/per-mode award tracking, LoTW confirmation download (would need the user's LoTW
password — deliberately left out).
