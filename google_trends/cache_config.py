"""Cache TTL per /google-trends/* endpoint (cache.py, keyed on the validated
params — marshmallow fills the defaults and resolves links, so
`timeframe=past_12_months` / `today 12-m` / no timeframe share a row, and
a pasted explore link shares the row of its keywords).

Trending Now moves by the minute; explore curves are recomputed by Google
through the day (the last point is partial), so they cache for a few hours;
the pickers and Year in Search barely change."""
from datetime import timedelta

TRENDING_CACHE = timedelta(minutes=10)
TREND_NEWS_CACHE = timedelta(minutes=30)
EXPLORE_CACHE = timedelta(hours=3)
EXPLORE_SHORT_CACHE = timedelta(minutes=20)    # sub-day timeframes (past hour / 4 h / day / 7 days, hour ranges)
AUTOCOMPLETE_CACHE = timedelta(days=1)
REFERENCE_CACHE = timedelta(days=7)
YEAR_IN_SEARCH_CACHE = timedelta(days=7)


def explore_ttl(*timeframes):
    """Explore routes cache by the timeframe asked for: minute / hour
    resolutions move fast, day / week / month curves do not."""
    fast = any(t and (t.startswith("now ") or "T" in t) for t in timeframes)
    return EXPLORE_SHORT_CACHE if fast else EXPLORE_CACHE
