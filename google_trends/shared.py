"""Helpers shared by the /google-trends/* endpoint modules: value coercion
for Google's positional arrays, Trends dates, links and in-memory paging.
Self-contained (no imports from other scraper packages) so the module also
runs on its own, outside the scrapers service."""
import html as htmllib
import re
from datetime import datetime, timezone
from urllib.parse import quote, urlencode

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def at(seq, *path, default=None):
    """Safe nested index into Google's positional arrays: at(d, 0, 3, 1)."""
    cur = seq
    for key in path:
        try:
            cur = cur[key]
        except (IndexError, KeyError, TypeError):
            return default
        if cur is None:
            return default
    return cur


def clean_text(value):
    """Strip tags, unescape entities and collapse whitespace; '' -> None."""
    if value is None:
        return None
    text = _WS_RE.sub(" ", htmllib.unescape(_TAG_RE.sub("", str(value)))).strip()
    return text or None


def to_int(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    digits = re.sub(r"[^\d\-]", "", str(value))
    try:
        return int(digits) if digits not in ("", "-") else None
    except ValueError:
        return None


def iso_from_ts(ts):
    """Unix seconds -> ISO-8601 UTC string."""
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def locale_tag(language, country):
    """'en' + 'US' -> 'en-US'; a locale given as 'pt-BR' is kept."""
    language = (language or "en").replace("_", "-")
    if "-" in language:
        return language
    return f"{language}-{(country or 'US').upper()}"


def pagination(page, per_page, total):
    total_pages = max((total + per_page - 1) // per_page, 1 if total else 0) if per_page else 0
    return {"page": page, "items_per_page": per_page, "total_pages": total_pages, "total_count": total}


SITE = "https://trends.google.com"

# Explore resolutions finer than a day carry a time of day; coarser ones are dates.
SUB_DAY_RESOLUTIONS = {"MINUTE", "EIGHT_MINUTE", "SIXTEEN_MINUTE", "HOUR", "FOUR_HOUR"}


def iso_date_from_ts(ts):
    """Unix seconds -> 'YYYY-MM-DD' (UTC)."""
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def point_date(ts, resolution):
    """A timeline timestamp -> ISO date for day/week/month points, ISO UTC
    datetime for sub-day points."""
    if (resolution or "").upper() in SUB_DAY_RESOLUTIONS:
        return iso_from_ts(ts)
    return iso_date_from_ts(ts)


def slice_page(items, page, per_page):
    """One page of an in-memory list -> (items, pagination block route_glue lifts)."""
    total = len(items)
    total_pages = (total + per_page - 1) // per_page if per_page else 0
    if total_pages and page > total_pages:
        raise ValueError(f"page {page} is past the last page ({total_pages}).")
    start = (page - 1) * per_page
    return items[start:start + per_page], pagination(page, per_page, total)


def explore_link(keywords, geo=None, date=None, category=None, prop=None):
    """The trends.google.com explore page for these keywords."""
    params = {"q": ",".join(keywords)}
    if date:
        params["date"] = date
    if geo:
        params["geo"] = geo
    if category:
        params["cat"] = category
    if prop:
        params["gprop"] = prop
    return f"{SITE}/trends/explore?{urlencode(params, quote_via=quote, safe=',/')}"


def absolute(link, base=SITE):
    if not isinstance(link, str) or not link:
        return None
    return base + link if link.startswith("/") else link
