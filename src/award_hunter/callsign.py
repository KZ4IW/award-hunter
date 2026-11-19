"""Callsign, band and US-state helpers."""
import re

# A token WSJT-X prints as a callsign: optional prefix/, base call, optional /suffix, maybe in <> (hashed).
CALL_RE = re.compile(r"^<?((?:[A-Z0-9]{1,4}/)?([A-Z0-9]{1,3}[0-9][A-Z0-9]{0,3}[A-Z])(?:/[A-Z0-9]{1,4})?)>?$")
US_RE = re.compile(r"^[AKNW][A-Z]?[0-9][A-Z]{1,3}$")
GRID4_RE = re.compile(r"^[A-R]{2}[0-9]{2}$")

STATES = dict(AL="Alabama", AK="Alaska", AZ="Arizona", AR="Arkansas", CA="California", CO="Colorado",
    CT="Connecticut", DE="Delaware", FL="Florida", GA="Georgia", HI="Hawaii", ID="Idaho", IL="Illinois",
    IN="Indiana", IA="Iowa", KS="Kansas", KY="Kentucky", LA="Louisiana", ME="Maine", MD="Maryland",
    MA="Massachusetts", MI="Michigan", MN="Minnesota", MS="Mississippi", MO="Missouri", MT="Montana",
    NE="Nebraska", NV="Nevada", NH="New Hampshire", NJ="New Jersey", NM="New Mexico", NY="New York",
    NC="North Carolina", ND="North Dakota", OH="Ohio", OK="Oklahoma", OR="Oregon", PA="Pennsylvania",
    RI="Rhode Island", SC="South Carolina", SD="South Dakota", TN="Tennessee", TX="Texas", UT="Utah",
    VT="Vermont", VA="Virginia", WA="Washington", WV="West Virginia", WI="Wisconsin", WY="Wyoming")
EXTRA_REGIONS = dict(DC="District of Columbia", PR="Puerto Rico", GU="Guam", VI="Virgin Islands",
                     AS="American Samoa", MP="Northern Mariana Islands")
ALL_REGIONS = {**STATES, **EXTRA_REGIONS}
BYNAME = {v.lower(): k for k, v in ALL_REGIONS.items()}

BANDS = [("160m", 1.8, 2.0), ("80m", 3.5, 4.0), ("60m", 5.25, 5.45), ("40m", 7.0, 7.3), ("30m", 10.1, 10.15),
         ("20m", 14.0, 14.35), ("17m", 18.068, 18.168), ("15m", 21.0, 21.45), ("12m", 24.89, 24.99),
         ("10m", 28.0, 29.7), ("6m", 50.0, 54.0), ("2m", 144.0, 148.0), ("70cm", 420.0, 450.0)]
BAND_ORDER = [b[0] for b in BANDS]


def band_of_mhz(mhz):
    for name, lo, hi in BANDS:
        if lo <= mhz <= hi:
            return name
    return None


def band_of_hz(hz):
    return band_of_mhz(hz / 1e6) if hz else None


def split_call(token):
    """'<W1AW/4>' -> ('W1AW/4', 'W1AW'); 'VE3/K1ABC/P' -> ('VE3/K1ABC/P', 'K1ABC'); None if not a call."""
    m = CALL_RE.match(token.strip().upper())
    return (m.group(1), m.group(2)) if m else None


def base_call(call):
    s = split_call(call or "")
    if s:
        return s[1]
    parts = [p for p in (call or "").strip("<>").upper().split("/") if p]
    return max(parts, key=len) if parts else ""


def is_us(base):
    return bool(US_RE.match(base))
