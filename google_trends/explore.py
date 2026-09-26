"""Google Trends Explore over /trends/api (validated 2026-09-24..26).

    GET /trends/api/explore?hl&tz=0&req={"comparisonItem":[{keyword, geo, time}…≤5],
                                         "category":N, "property":""|images|news|youtube|froogle}
        -> widgets: TIMESERIES, GEO_MAP, RELATED_QUERIES (+ _0.._4 per keyword on a
           comparison), RELATED_TOPICS; each carries a `request` and a `token`
           bound to it (an edited request answers 401 — except the GEO_MAP
           resolution / low-volume switches, which the site itself edits)
    GET /trends/api/widgetdata/<kind>?hl&tz=0&req=<widget request>&token=<token>
        multiline       {"default": {"timelineData": [{time, value[], hasData[],
                        isPartial}], "averages": []}}
        multirange      same, one `columnData` entry per compared timeframe
        comparedgeo     {"default": {"geoMapData": [{geoCode, geoName, value[],
                        hasData[], maxValueIndex, coordinates?}]}}
        relatedsearches {"default": {"rankedList": [TOP, RISING]}}; RISING values
                        are growth % ("Breakout" = over +5000%)
    GET /trends/api/autocomplete/<query>?hl -> {"default": {"topics": [{mid, title, type}]}}

tz=0 everywhere, so every timestamp is UTC. RELATED_TOPICS is not served:
Google answers an empty rankedList for every anonymous request (tagged
USER_TYPE_SCRAPER server-side; empty in a real headed Chrome too).
"""
import functools
import json
from urllib.parse import quote

from google_trends import cache_config, fetch
from google_trends.reference import category_ref, geo_ref, metro_codes
from google_trends.refs import TIMEFRAME_NAMES, country_of, geo_level, is_topic
from google_trends.shared import absolute, explore_link, locale_tag, point_date

PROPERTIES = {"web": "", "images": "images", "news": "news", "youtube": "youtube", "shopping": "froogle"}
RESOLUTIONS = {"country": "COUNTRY", "region": "REGION", "city": "CITY", "metro": "DMA"}
_RESOLUTION_NAMES = {v: k for k, v in RESOLUTIONS.items()}


def _hl(language, geo):
    return locale_tag(language, country_of(geo) or "US")


# ---- upstream calls ------------------------------------------------------------------------------

def _explore(items, category, prop, hl):
    """items: [(keyword, geo, time), …] -> {widget id: widget}."""
    req = {"comparisonItem": [{"keyword": k, "geo": g, "time": t} for k, g, t in items],
           "category": category or 0, "property": PROPERTIES.get(prop, "")}
    data = fetch.api("/trends/api/explore", {"hl": hl, "tz": 0, "req": json.dumps(req, separators=(",", ":"),
                                                                                 ensure_ascii=False)},
                     "explore")
    widgets = data.get("widgets") if isinstance(data, dict) else None
    if not widgets:
        raise fetch.TrendsNotFound("Google Trends returned no data for this request")
    return {w.get("id"): w for w in widgets if isinstance(w, dict) and w.get("token")}


def _widget_data(widget, kind, hl, label):
    params = {"hl": hl, "tz": 0, "req": json.dumps(widget["request"], separators=(",", ":"), ensure_ascii=False),
              "token": widget["token"]}
    return fetch.api(f"/trends/api/widgetdata/{kind}", params, label)


# ---- response pieces ------------------------------------------------------------------------------

def _time_bound(text):
    """'2025-09-26' -> '2025-09-26'; '2026-09-17T13\\:06\\:25' -> ISO UTC datetime."""
    text = (text or "").replace("\\", "").strip()
    if "T" in text:
        return text + ("Z" if not text.endswith("Z") else "")
    return text or None


def _timeframe(token, widget_request):
    raw = (widget_request or {}).get("time")
    if raw is None:
        raw = ((widget_request or {}).get("comparisonItem") or [{}])[0].get("time")
    parts = (raw or "").split(" ")
    return {"value": TIMEFRAME_NAMES.get(token, token), "start": _time_bound(parts[0]) if parts and parts[0] else None,
            "end": _time_bound(parts[1]) if len(parts) > 1 else None}


def _keywords(keywords, timeseries, geo_names=()):
    """Keyword + its display name (a topic id's entity name). On geo /
    timeframe comparisons the site labels each line "<name> - <geo name>";
    that suffix is dropped."""
    bullets = (timeseries or {}).get("bullets") or []
    out = []
    for i, k in enumerate(keywords):
        label = bullets[i].get("text") if i < len(bullets) and isinstance(bullets[i], dict) else None
        for name in geo_names:
            if label and name and label.endswith(" - " + name):
                label = label[: -len(" - " + name)]
        out.append({"keyword": k, "name": label or k, "is_topic": is_topic(k)})
    return out


def _context(keywords, geo, token, widgets, category, prop, hl):
    ts = widgets.get("TIMESERIES") or {}
    return {
        "keywords": _keywords(keywords, ts),
        "geo": geo_ref(geo, hl),
        "timeframe": _timeframe(token, ts.get("request")),
        "category": category_ref(category, hl),
        "property": prop,
        "link": explore_link(keywords, geo, token, category, PROPERTIES.get(prop) or None),
    }


def _values(labels, values, has_data):
    out = {}
    for i, label in enumerate(labels):
        ok = has_data[i] if i < len(has_data) else True
        out[label] = values[i] if ok and i < len(values) else None
    return out


def _timeline(data, labels, resolution):
    default = (data or {}).get("default") or {}
    points = []
    for p in default.get("timelineData") or []:
        points.append({
            "date": point_date(p.get("time"), resolution),
            "values": _values(labels, p.get("value") or [], p.get("hasData") or []),
            "is_partial": bool(p.get("isPartial")),
        })
    averages = default.get("averages") or []
    return {label: (averages[i] if i < len(averages) else None) for i, label in enumerate(labels)}, points


def _resolution(widget):
    return ((widget or {}).get("request") or {}).get("resolution")


def _regions(data, labels, include_low_volume, resolution=None, hl="en-US"):
    rows = []
    multi = len(labels) > 1
    metros = metro_codes(hl) if resolution == "DMA" else {}
    for g in ((data or {}).get("default") or {}).get("geoMapData") or []:
        values = _values(labels, g.get("value") or [], g.get("hasData") or [])
        if not include_low_volume and all(v is None for v in values.values()):
            continue
        coords = g.get("coordinates") if isinstance(g.get("coordinates"), dict) else None
        idx = g.get("maxValueIndex")
        rows.append({
            "code": metros.get(g.get("geoCode"), g.get("geoCode")) or None,
            "name": g.get("geoName"),
            "values": values,
            "top_keyword": labels[idx] if multi and isinstance(idx, int) and 0 <= idx < len(labels) else None,
            "coordinates": {"latitude": coords.get("lat"), "longitude": coords.get("lng")} if coords else None,
        })
    return rows


def _ranked(section, rising):
    out = []
    for r in (section or {}).get("rankedKeyword") or []:
        query = r.get("query")
        if not query:
            continue
        row = {"query": query}
        if rising:
            row["growth_percent"] = r.get("value")
            row["is_breakout"] = r.get("formattedValue") == "Breakout"
        else:
            row["value"] = r.get("value")
        row["link"] = absolute(r.get("link"))
        out.append(row)
    return out


def _compare_period(widget):
    raw = (((widget or {}).get("request") or {}).get("trendinessSettings") or {}).get("compareTime")
    parts = (raw or "").split(" ")
    if len(parts) != 2:
        return None
    return {"start": _time_bound(parts[0]), "end": _time_bound(parts[1])}


def _related_widgets(widgets, count):
    if count == 1 and "RELATED_QUERIES" in widgets:
        return [widgets["RELATED_QUERIES"]]
    return [widgets.get(f"RELATED_QUERIES_{i}") for i in range(count)]


def _related(widgets, keywords, hl):
    wanted = _related_widgets(widgets, len(keywords))
    datas = fetch.run_parallel([
        (lambda w=w: _widget_data(w, "relatedsearches", hl, "related queries")) if w else (lambda: None)
        for w in wanted])
    out = []
    for k, data in zip(keywords, datas):
        ranked = ((data or {}).get("default") or {}).get("rankedList") or []
        out.append({"keyword": k,
                    "top": _ranked(ranked[0] if ranked else None, False),
                    "rising": _ranked(ranked[1] if len(ranked) > 1 else None, True)})
    period = next((_compare_period(w) for w in wanted if w and _compare_period(w)), None)
    return out, period


def _geo_request(widget, geo, resolution, include_low_volume):
    """Apply the resolution / low-volume switches to a GEO_MAP widget (the
    same edits the site's own map controls make)."""
    level = geo_level(geo)
    if resolution:
        if level == "worldwide" and resolution not in ("country", "city"):
            raise fetch.TrendsBadRequest("worldwide data breaks down by country or city only")
        if level == "metro" and resolution != "city":
            raise fetch.TrendsBadRequest("a metro area breaks down by city only")
        if resolution == "metro" and country_of(geo) != "US":
            raise fetch.TrendsBadRequest("resolution=metro (Nielsen DMA) exists only for the US")
        if resolution == "country" and level != "worldwide":
            raise fetch.TrendsBadRequest("resolution=country needs geo=worldwide")
        widget["request"]["resolution"] = RESOLUTIONS[resolution]
    widget["request"]["includeLowSearchVolumeGeos"] = bool(include_low_volume)
    return widget


# ---- endpoints -------------------------------------------------------------------------------------

def cached_by_timeframe(fn):
    """Serve an explore endpoint through the response cache with a TTL picked
    from its timeframe(s) (cache_config.explore_ttl); the key is the
    validated params, like route_glue's own caching."""
    @functools.wraps(fn)
    def wrapper(**kwargs):
        tokens = kwargs.get("timeframes") or [kwargs.get("timeframe") or ""]
        return fetch.cached_call(f"google-trends.{fn.__name__}", kwargs, fn, cache_config.explore_ttl(*tokens))
    return wrapper


@cached_by_timeframe
def interest_over_time(keywords, geo="", timeframe="today 12-m", category=0, property="web", language="en"):
    """Relative search interest (0-100) over time for up to 5 keywords."""
    hl = _hl(language, geo)
    widgets = _explore([(k, geo, timeframe) for k in keywords], category, property, hl)
    ts = widgets.get("TIMESERIES")
    if not ts:
        raise fetch.TrendsNotFound("no interest-over-time data for these keywords")
    data = _widget_data(ts, "multiline", hl, "interest over time")
    resolution = _resolution(ts)
    averages, points = _timeline(data, keywords, resolution)
    out = _context(keywords, geo, timeframe, widgets, category, property, hl)
    out.update({"resolution": (resolution or "").lower() or None, "averages": averages, "timeline": points})
    return out


@cached_by_timeframe
def interest_by_region(keywords, geo="", timeframe="today 12-m", resolution=None, include_low_volume=False,
                       category=0, property="web", language="en"):
    """Relative search interest by country / region / city / US metro area.
    One keyword -> 0-100 interest; several -> each keyword's share (%)."""
    hl = _hl(language, geo)
    widgets = _explore([(k, geo, timeframe) for k in keywords], category, property, hl)
    widget = widgets.get("GEO_MAP")
    if not widget:
        raise fetch.TrendsNotFound("no regional data for these keywords")
    widget = _geo_request(widget, geo, resolution, include_low_volume)
    data = _widget_data(widget, "comparedgeo", hl, "interest by region")
    rows = _regions(data, keywords, include_low_volume, _resolution(widget), hl)
    out = _context(keywords, geo, timeframe, widgets, category, property, hl)
    out.update({
        "resolution": _RESOLUTION_NAMES.get(_resolution(widget), (_resolution(widget) or "").lower() or None),
        "value_type": "share_percent" if len(keywords) > 1 else "interest",
        "count": len(rows),
        "regions": rows,
    })
    return out


@cached_by_timeframe
def related_queries(keywords, geo="", timeframe="today 12-m", category=0, property="web", language="en"):
    """Top and rising related searches per keyword."""
    hl = _hl(language, geo)
    widgets = _explore([(k, geo, timeframe) for k in keywords], category, property, hl)
    results, period = _related(widgets, keywords, hl)
    out = _context(keywords, geo, timeframe, widgets, category, property, hl)
    out.update({"rising_compared_to": period, "results": results})
    return out


@cached_by_timeframe
def explore(keywords, geo="", timeframe="today 12-m", resolution=None, category=0, property="web",
            language="en"):
    """Everything the explore page shows in one call: interest over time,
    interest by region and related queries per keyword."""
    hl = _hl(language, geo)
    widgets = _explore([(k, geo, timeframe) for k in keywords], category, property, hl)
    ts, gm = widgets.get("TIMESERIES"), widgets.get("GEO_MAP")
    if gm:
        gm = _geo_request(gm, geo, resolution, False)
    calls = [
        (lambda: _widget_data(ts, "multiline", hl, "interest over time")) if ts else (lambda: None),
        (lambda: _widget_data(gm, "comparedgeo", hl, "interest by region")) if gm else (lambda: None),
        lambda: _related(widgets, keywords, hl),
    ]
    ts_data, geo_data, (related, period) = fetch.run_parallel(calls, workers=3)
    ts_resolution = _resolution(ts)
    averages, points = _timeline(ts_data, keywords, ts_resolution)
    regions = _regions(geo_data, keywords, False, _resolution(gm), hl)
    out = _context(keywords, geo, timeframe, widgets, category, property, hl)
    out.update({
        "interest_over_time": {"resolution": (ts_resolution or "").lower() or None, "averages": averages,
                               "timeline": points},
        "interest_by_region": {
            "resolution": _RESOLUTION_NAMES.get(_resolution(gm), (_resolution(gm) or "").lower() or None),
            "value_type": "share_percent" if len(keywords) > 1 else "interest",
            "count": len(regions), "regions": regions},
        "related_queries": {"rising_compared_to": period, "results": related},
    })
    return out


@cached_by_timeframe
def compare_timeframes(keyword, timeframes, geo="", category=0, property="web", language="en"):
    """One keyword's interest over 2-5 periods on one scale (e.g. this year
    vs last year), aligned point by point."""
    hl = _hl(language, geo)
    widgets = _explore([(keyword, geo, t) for t in timeframes], category, property, hl)
    ts = widgets.get("TIMESERIES")
    if not ts:
        raise fetch.TrendsNotFound("no interest-over-time data for this keyword")
    data = _widget_data(ts, "multirange", hl, "compare timeframes")
    resolution = _resolution(ts)
    default = (data or {}).get("default") or {}
    averages = default.get("averages") or []
    items = (ts.get("request") or {}).get("comparisonItem") or []
    periods = []
    for i, token in enumerate(timeframes):
        bounds = _timeframe(token, items[i] if i < len(items) else {})
        bounds["average"] = averages[i] if i < len(averages) else None
        periods.append(bounds)
    rows = []
    for pos, row in enumerate(default.get("timelineData") or [], start=1):
        points = []
        for col in row.get("columnData") or []:
            has = col.get("hasData")
            ok = has if isinstance(has, bool) else True
            points.append({"date": point_date(col.get("time"), resolution),
                           "value": col.get("value") if ok else None,
                           "is_partial": bool(col.get("isPartial"))})
        rows.append({"position": pos, "points": points})
    geo_info = geo_ref(geo, hl)
    return {
        "keyword": _keywords([keyword], ts, [geo_info["name"]])[0],
        "geo": geo_info,
        "category": category_ref(category, hl),
        "property": property,
        "resolution": (resolution or "").lower() or None,
        "timeframes": periods,
        "timeline": rows,
    }


@cached_by_timeframe
def compare_geos(keyword, geos, timeframe="today 12-m", category=0, property="web", language="en"):
    """One keyword's interest in 2-5 places on one scale."""
    hl = _hl(language, geos[0])
    widgets = _explore([(keyword, g, timeframe) for g in geos], category, property, hl)
    ts = widgets.get("TIMESERIES")
    if not ts:
        raise fetch.TrendsNotFound("no interest-over-time data for this keyword")
    data = _widget_data(ts, "multiline", hl, "compare geos")
    labels = list(geos)
    resolution = _resolution(ts)
    averages, points = _timeline(data, labels, resolution)
    geo_infos = [geo_ref(g, hl) for g in geos]
    return {
        "keyword": _keywords([keyword], ts, [g["name"] for g in geo_infos])[0],
        "geos": geo_infos,
        "timeframe": _timeframe(timeframe, ts.get("request")),
        "category": category_ref(category, hl),
        "property": property,
        "resolution": (resolution or "").lower() or None,
        "averages": averages,
        "timeline": points,
    }


def autocomplete(query, language="en"):
    """Search terms -> Google Trends topics (Knowledge Graph ids usable as keywords)."""
    hl = _hl(language, None)
    data = fetch.api(f"/trends/api/autocomplete/{quote(query, safe='')}", {"hl": hl, "tz": 0}, "autocomplete")
    topics = []
    for t in ((data or {}).get("default") or {}).get("topics") or []:
        mid = t.get("mid")
        if not mid:
            continue
        topics.append({"id": mid, "title": t.get("title"), "type": t.get("type"),
                       "link": explore_link([mid])})
    return {"query": query, "count": len(topics), "topics": topics}

