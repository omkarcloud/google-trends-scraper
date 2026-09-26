"""Trending Now (trends.google.com/trending) over the page's own batchexecute
rpcs — no cookie, no token, direct egress (validated 2026-09-26).

  i0OFE [null, null, <geo>, <articles per trend 0-N>, <hl>, <hours 1-191>, 1]
        -> [null, [trend, …]], every trend of the window (US: 4 h ≈ 40,
           24 h ≈ 240, 7 d ≈ 1,700; a subregion like US-CA ≈ 40 / day);
           null for a geo Trending Now does not cover
        trend = [0 title, 1 [article, …] | null, 2 geo, 3 [start ts],
                 4 [end ts] | null (null = still active), 5 null,
                 6 search volume (lower bound of the 100 … 2M+ bucket), 7 null,
                 8 growth % (50 … 1000 = "1000%+"), 9 [trend breakdown queries],
                 10 [category ids], 11 [[news id, lang, geo], …],
                 12 normalized title]
        article = [title, link, source, [published ts], image]
  wAgrOe [<geo>, <hl>] -> [<geo name>]
  w4opAf [[[news id, lang, geo], …], <n ≤ 30>] -> [[article, …]]

The page filters categories, the active-only switch and the sort order
client-side (its own SSR request always asks for category 0), so this
module does the same over the full list.
"""
from datetime import timedelta

from google_trends import fetch
from google_trends.reference import TRENDING_CATEGORIES
from google_trends.refs import encode_news_tokens
from google_trends.shared import SITE, at, clean_text, iso_from_ts, locale_tag, slice_page, to_int
from urllib.parse import quote

SORTS = ("relevance", "search_volume", "growth", "recency", "title")
STATUSES = ("all", "active", "ended")
MAX_ARTICLES = 10          # inline articles per trend on the list route
MAX_NEWS = 30              # w4opAf answers at most 30
LOOKUP_TTL = timedelta(minutes=15)


def _article(row):
    if not isinstance(row, list) or not isinstance(at(row, 1), str):
        return None
    return {
        "title": clean_text(at(row, 0)),
        "link": at(row, 1),
        "source": clean_text(at(row, 2)),
        "published_at": iso_from_ts(at(row, 3, 0)),
        "image": at(row, 4) if isinstance(at(row, 4), str) else None,
    }


def _articles(rows):
    return [a for a in (_article(r) for r in (rows or [])) if a]


def _trend_link(title, geo, hours):
    date = "now 1-d" if hours <= 24 else "now 7-d"
    return f"{SITE}/trends/explore?q={quote(title)}&geo={quote(geo)}&date={quote(date)}"


def parse_trend(row, geo, hours):
    title = at(row, 0)
    if not isinstance(title, str) or not title.strip():
        return None
    title = title.strip()
    cats = [c for c in (at(row, 10) or []) if isinstance(c, int)]
    tokens = at(row, 11) or []
    ended = at(row, 4, 0)
    return {
        "id": encode_news_tokens(tokens),
        "title": title,
        "link": _trend_link(title, geo, hours),
        "search_volume": to_int(at(row, 6)),
        "growth_percent": to_int(at(row, 8)),
        "is_active": ended is None,
        "started_at": iso_from_ts(at(row, 3, 0)),
        "ended_at": iso_from_ts(ended) if ended is not None else None,
        "categories": [{"id": c, "name": TRENDING_CATEGORIES.get(c)} for c in cats],
        "related_queries": [q for q in (at(row, 9) or []) if isinstance(q, str) and q.strip().lower() != title.lower()],
        "articles": _articles(at(row, 1)),
        "articles_count": len(tokens),
    }


def _fetch_rows(geo, hours, articles, hl):
    rows, name = fetch.batch([("i0OFE", [None, None, geo, articles, hl, hours, 1]), ("wAgrOe", [geo, hl])],
                             language=hl, label="trending")
    items = at(rows, 1)
    if not isinstance(items, list):
        raise fetch.TrendsNotFound(f"Trending Now has no data for geo {geo} (see /google-trends/trending/geos)")
    return items, at(name, 0)


def _sort_key(sort):
    if sort == "search_volume":
        return lambda t: (-(t["search_volume"] or 0), -(t["growth_percent"] or 0))
    if sort == "growth":
        return lambda t: (-(t["growth_percent"] or 0), -(t["search_volume"] or 0))
    if sort == "recency":
        return lambda t: t["started_at"] or ""
    if sort == "title":
        return lambda t: t["title"].lower()
    return None


def trending(geo="US", hours=24, category=None, status="all", sort="relevance", articles=0, language="en",
             page=1, limit=50):
    """What is trending now in a country or region (the Trending Now list)."""
    hl = locale_tag(language, geo[:2])
    rows, geo_name = _fetch_rows(geo, hours, articles, hl)
    trends = [t for t in (parse_trend(r, geo, hours) for r in rows) if t]
    if category:
        trends = [t for t in trends if any(c["id"] == category for c in t["categories"])]
    if status == "active":
        trends = [t for t in trends if t["is_active"]]
    elif status == "ended":
        trends = [t for t in trends if not t["is_active"]]
    key = _sort_key(sort)
    if key:
        trends.sort(key=key, reverse=(sort == "recency"))
    items, pagination = slice_page(trends, page, limit)
    return {
        "geo": {"code": geo, "name": clean_text(geo_name)},
        "hours": hours,
        "category": {"id": category, "name": TRENDING_CATEGORIES.get(category)} if category else None,
        "status": status,
        "sort": sort,
        "trends": items,
        "pagination": pagination,
    }


# ---- one trend's articles -----------------------------------------------------------------------

def _week_index(geo, hl):
    """{lower title / normalized title / breakdown query: trend} over the
    past 7 days of `geo` (shared by news lookups for 15 minutes); a trend is
    {title, tokens, volume}."""
    def build():
        rows, _ = _fetch_rows(geo, 168, 0, hl)
        index = {}
        for row in rows:
            tokens = at(row, 11) or []
            title = at(row, 0)
            if not tokens or not isinstance(title, str):
                continue
            entry = {"title": title.strip(), "tokens": tokens, "volume": to_int(at(row, 6)) or 0}
            for key in [title, at(row, 12)]:
                if isinstance(key, str):
                    index.setdefault(key.strip().lower(), entry)
            for q in at(row, 9) or []:
                if isinstance(q, str):
                    index.setdefault("~" + q.strip().lower(), entry)
        return index
    return fetch.dataset("trend_index", {"geo": geo, "hl": hl}, build, LOOKUP_TTL)


def _partial_match(index, key):
    """No exact title: the highest-volume trend whose title contains the text,
    else the highest-volume trend with a related query containing it (so
    `weather` finds "weather forecast today")."""
    for titles_only in (True, False):
        hits = [v for k, v in index.items()
                if key in (k if titles_only else k.lstrip("~")) and (not k.startswith("~")) == titles_only]
        if hits:
            return max(hits, key=lambda v: v.get("volume") or 0)
    return None


def trend_news(trend, geo="US", limit=10, language="en"):
    """News articles behind one trending search (up to 30): pass its id, its
    title, or any text in it (the highest-volume matching trend is used)."""
    hl = locale_tag(language, geo[:2])
    tokens, title = trend.get("tokens"), trend.get("title")
    if not tokens:
        index = _week_index(geo, hl)
        key = title.lower()
        hit = index.get(key) or index.get("~" + key) or _partial_match(index, key)
        if not hit:
            raise fetch.TrendsNotFound(f"no search trending in {geo} over the past 7 days matches '{title}' "
                                       f"(pass a trend id or title from /google-trends/trending)")
        tokens, title = hit["tokens"], hit["title"]
    data = fetch.batch([("w4opAf", [tokens, limit])], language=hl, label="trend news")[0]
    articles = _articles(at(data, 0))
    token_geo = at(tokens, 0, 2) or geo
    return {
        "trend": {"id": encode_news_tokens(tokens), "title": title},
        "geo": token_geo,
        "count": len(articles),
        "articles": articles,
    }
