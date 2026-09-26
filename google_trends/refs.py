"""Input resolvers for /google-trends/* params — ONE param per input
(tripadvisor QueryOrLinkField convention): each accepts the plain value OR a
Google Trends link carrying it, and auto-detects which form was passed.

    keywords   "coffee,tea" | "/m/02vqfm" (a topic id) | an explore link
               trends.google.com/trends/explore?q=coffee,tea&geo=US&date=…
    geo        "US" | "US-CA" | "US-CA-803" (a US metro / DMA) | "GB-ENG"
               | "worldwide" | a trends link with ?geo=
    trend      a trending search title | the `id` of a /trending result
    timeframe  past_7_days | today 3-m | 2024-01-01 2024-12-31 | …

Every resolver raises ValueError with a user-facing message.
"""
import re
from datetime import date, datetime
from urllib.parse import parse_qs, urlparse

from schema_fields import COUNTRY_CODES

MAX_KEYWORDS = 5
MAX_KEYWORD_LENGTH = 100
TRENDS_HOSTS = ("trends.google.com", "trends.withgoogle.com")
FIRST_DATE = date(2004, 1, 1)   # Google Trends data starts in 2004

# Public timeframe names -> the site's tokens.
TIMEFRAMES = {
    "past_hour": "now 1-H",
    "past_4_hours": "now 4-H",
    "past_day": "now 1-d",
    "past_7_days": "now 7-d",
    "past_30_days": "today 1-m",
    "past_90_days": "today 3-m",
    "past_12_months": "today 12-m",
    "past_5_years": "today 5-y",
    "all": "all",
}
DEFAULT_TIMEFRAME = "past_12_months"
TIMEFRAME_NAMES = {token: name for name, token in TIMEFRAMES.items()}

_TOPIC_RE = re.compile(r"^/(m|g|t)/[0-9a-zA-Z_\-]+$")
_SITE_TOKEN_RE = re.compile(r"^(now) (1-H|4-H|1-d|7-d)$|^(today) (1-m|3-m|12-m|5-y|\d{1,2}-m)$")
_DATE_RANGE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{4}-\d{2}-\d{2})$")
_HOUR_RANGE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}) (\d{4}-\d{2}-\d{2}T\d{2})$")
_REGION_RE = re.compile(r"^([A-Z]{2})-([A-Z0-9]{1,3})$")
_DMA_RE = re.compile(r"^US-([A-Z]{2})-(\d{3})$")
_NEWS_TOKEN_RE = re.compile(r"^\d{6,}\.[a-zA-Z\-]{2,10}\.[A-Z]{2}(-[A-Z0-9]{1,3})?$")
WORLDWIDE = {"", "WORLDWIDE", "WORLD", "GLOBAL", "WW", "ALL"}


def is_link(value):
    return isinstance(value, str) and value.strip().lower().startswith(("http://", "https://", "trends.google."))


def link_params(value):
    """A trends.google.com / trends.withgoogle.com link -> its query params
    ({name: last value}). Raises ValueError for another site's link."""
    url = value.strip()
    if not url.lower().startswith("http"):
        url = "https://" + url
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in TRENDS_HOSTS):
        raise ValueError("Must be a trends.google.com link.")
    return {k: v[-1] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}


# ---- keywords ----------------------------------------------------------------------------------

def _split_keywords(text):
    out = []
    for raw in str(text).split(","):
        item = " ".join(raw.split())
        if item and item.lower() not in {k.lower() for k in out}:
            out.append(item)
    return out


def resolve_keywords(value):
    """'coffee, tea' | topic ids | an explore link -> ['coffee', 'tea']."""
    if is_link(value):
        q = link_params(value).get("q", "")
        if not q:
            raise ValueError("The explore link carries no q= keywords.")
        items = _split_keywords(q)
    else:
        items = _split_keywords(value)
    if not items:
        raise ValueError("Pass at least one keyword (e.g. coffee,tea).")
    if len(items) > MAX_KEYWORDS:
        raise ValueError(f"At most {MAX_KEYWORDS} keywords per request (Google Trends compares up to 5).")
    for item in items:
        if len(item) > MAX_KEYWORD_LENGTH:
            raise ValueError(f"Keywords must be at most {MAX_KEYWORD_LENGTH} characters.")
    return items


def is_topic(keyword):
    """A Knowledge Graph topic id (/m/…, /g/…) rather than a search term."""
    return bool(_TOPIC_RE.match(keyword or ""))


# ---- geo ---------------------------------------------------------------------------------------

def normalize_geo(value, allow_metro=True, allow_worldwide=True):
    """'us' -> 'US', 'us-ca' -> 'US-CA', 'US-CA-803' (metro), 'worldwide' -> ''."""
    raw = (value or "").strip()
    if is_link(raw):
        raw = link_params(raw).get("geo", "")
    code = raw.upper().replace("_", "-")
    if code == "UK":
        code = "GB"
    if code in WORLDWIDE:
        if not allow_worldwide:
            raise ValueError("A country is required here (e.g. US, GB, IN).")
        return ""
    if len(code) == 2:
        if code not in COUNTRY_CODES:
            raise ValueError("Unknown country code (use ISO codes like US, GB, IN).")
        return code
    if _DMA_RE.match(code):
        if not allow_metro:
            raise ValueError("Metro areas (US-CA-803) are not supported here; use a country or region (US, US-CA).")
        return code
    m = _REGION_RE.match(code)
    if m and m.group(1) in COUNTRY_CODES:
        return code
    raise ValueError("geo must be a country (US), a region (US-CA), a US metro (US-CA-803) or 'worldwide'; "
                     "see /google-trends/geos.")


def geo_level(code):
    if not code:
        return "worldwide"
    if _DMA_RE.match(code):
        return "metro"
    return "region" if "-" in code else "country"


def country_of(code):
    return (code or "")[:2] or None


# ---- timeframe ---------------------------------------------------------------------------------

def _parse_day(text):
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"Invalid date {text!r} (use YYYY-MM-DD).")


def resolve_timeframe(value):
    """A public name, a site token or a date / hour range -> the site token."""
    raw = " ".join((value or DEFAULT_TIMEFRAME).split())
    if is_link(raw):
        raw = link_params(raw).get("date", "") or DEFAULT_TIMEFRAME
    low = raw.lower().replace("-", "_").replace(" ", "_")
    if low in TIMEFRAMES:
        return TIMEFRAMES[low]
    if raw.lower() == "all" or _SITE_TOKEN_RE.match(raw):
        return raw
    m = _DATE_RANGE_RE.match(raw) or _DATE_RANGE_RE.match(raw.replace("..", " ").replace(" to ", " "))
    if m:
        start, end = _parse_day(m.group(1)), _parse_day(m.group(2))
        if start >= end:
            raise ValueError("The timeframe's start date must be before its end date.")
        if start < FIRST_DATE:
            raise ValueError("Google Trends data starts on 2004-01-01.")
        if end > date.today():
            raise ValueError("The timeframe cannot end in the future.")
        return f"{start.isoformat()} {end.isoformat()}"
    m = _HOUR_RANGE_RE.match(raw)
    if m:
        try:
            start, end = (datetime.strptime(m.group(i), "%Y-%m-%dT%H") for i in (1, 2))
        except ValueError:
            raise ValueError("Hour ranges look like 2026-09-20T00 2026-09-23T00.")
        if start >= end:
            raise ValueError("The timeframe's start must be before its end.")
        if (end - start).days > 7:
            raise ValueError("Hourly ranges can span at most 7 days.")
        return raw
    raise ValueError("timeframe must be one of " + ", ".join(TIMEFRAMES)
                     + ", or a range 'YYYY-MM-DD YYYY-MM-DD' / 'YYYY-MM-DDTHH YYYY-MM-DDTHH'.")


def resolve_timeframes(value):
    """Comma-separated timeframes (2-5) for a timeframe comparison."""
    items = []
    for raw in str(value or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        token = resolve_timeframe(raw)
        if token not in items:
            items.append(token)
    if len(items) < 2:
        raise ValueError("Pass 2 to 5 comma-separated timeframes (e.g. 2024-01-01 2024-12-31,2025-01-01 2025-12-31).")
    if len(items) > MAX_KEYWORDS:
        raise ValueError("At most 5 timeframes per comparison.")
    return items


# ---- trend -------------------------------------------------------------------------------------

def encode_news_tokens(tokens, limit=3):
    """[[id, lang, geo], …] -> '4845949982.en.US,4846085008.es.US' (a trend's `id`).
    Three news ids already return the full 30 articles w4opAf serves (tested
    2026-09-26: 3 ids == all 49 ids), so the id stays short."""
    out = []
    for t in tokens or []:
        if isinstance(t, list) and len(t) >= 3 and t[0] is not None:
            out.append(f"{t[0]}.{t[1]}.{t[2]}")
        if len(out) >= limit:
            break
    return ",".join(out) or None


def decode_news_tokens(value):
    """A trend `id` -> [[id, lang, geo], …] (None when it is not an id)."""
    parts = [p.strip() for p in str(value or "").split(",") if p.strip()]
    if not parts or not all(_NEWS_TOKEN_RE.match(p) for p in parts):
        return None
    out = []
    for p in parts:
        nid, lang, geo = p.split(".", 2)
        out.append([int(nid), lang, geo])
    return out


def resolve_trend(value):
    """A trending search title OR a trend `id` -> {'tokens': [...]} | {'title': str}."""
    raw = " ".join((value or "").split())
    if not raw:
        raise ValueError("Pass a trending search title or a trend id from /google-trends/trending.")
    tokens = decode_news_tokens(raw)
    if tokens:
        return {"tokens": tokens, "title": None}
    if len(raw) > 200:
        raise ValueError("Trend titles are at most 200 characters.")
    return {"tokens": None, "title": raw}
