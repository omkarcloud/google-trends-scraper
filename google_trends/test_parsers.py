"""Offline tests: input resolution, schemas, response classification and every
endpoint function run over recorded upstream bodies (google_trends/fixtures/,
captured 2026-09-26). The transport is replaced by a fake that serves the
fixture matching each request, so decode / parse / shape code runs for real.

    python -m pytest google_trends/test_parsers.py -q
"""
import gzip
import json
import os
import re
from urllib.parse import parse_qs, unquote, urlparse

import pytest

from google_trends import explore, fetch, reference, refs, schemas, trending, year_in_search
from schema_fields import load_query

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture(name):
    with gzip.open(os.path.join(FIXTURES, name + ".gz"), "rt", encoding="utf-8") as f:
        return f.read()


# ---- fake transport ------------------------------------------------------------------------------

def _explore_fixture(req):
    words = [c["keyword"] for c in req["comparisonItem"]]
    geos = {c["geo"] for c in req["comparisonItem"]}
    if words == ["coffee", "tea"]:
        return "explore_coffee_tea.json"
    if words == ["coffee", "coffee"]:
        return "explore_coffee_multirange.json"
    if words == ["coffee"] and geos == {"US-CA"}:
        return "explore_coffee_usca.json"
    if words == ["/m/0dl567"]:
        return "explore_taylor_7d.json"
    raise AssertionError(f"no explore fixture for {req}")


def _widget_fixture(kind, req_text):
    if kind == "multiline":
        return "multiline_taylor_7d.json" if "/m/0dl567" in req_text else "multiline_coffee_tea.json"
    if kind == "multirange":
        return "multirange_coffee.json"
    if kind == "comparedgeo":
        return "comparedgeo_coffee_city.json" if '"resolution":"CITY"' in req_text else "comparedgeo_coffee_tea_metro.json"
    if kind == "relatedsearches":
        return "related_tea.json" if '"value":"tea"' in req_text else "related_coffee.json"
    raise AssertionError(f"no widget fixture for {kind}")


def fake_request(method, url, *, kind, label, headers, timeout=None, body=None, browser=True):
    parsed = urlparse(url)
    qs = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
    if parsed.path == fetch.BATCH_PATH:
        rpcs = qs.get("rpcids", "")
        if "i0OFE" in rpcs:
            return fixture("trending_us_24h.txt")
        if "w4opAf" in rpcs:
            return fixture("trend_news.txt")
        if "DqDTgb" in rpcs:
            return fixture("trending_geos.txt")
    if parsed.path == "/trends/api/explore":
        return fixture(_explore_fixture(json.loads(qs["req"])))
    if parsed.path.startswith("/trends/api/widgetdata/"):
        return fixture(_widget_fixture(parsed.path.rsplit("/", 1)[1], qs["req"]))
    if parsed.path.startswith("/trends/api/autocomplete/"):
        return fixture("autocomplete_tesla.json")
    if parsed.path == "/trends/api/explore/pickers/geo":
        return fixture("picker_geo.json")
    if parsed.path == "/trends/api/explore/pickers/category":
        return fixture("picker_category.json")
    raise AssertionError(f"unexpected request {method} {unquote(url)}")


def fake_page(url, label):
    if "/2025/us/" in url:
        return fixture("yis_2025_us.html"), url
    if url.rstrip("/").endswith("/2019"):
        return fixture("yis_2019_global.html"), url
    raise fetch.TrendsNotFound("not found")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(fetch, "request", fake_request)
    monkeypatch.setattr(fetch, "page", fake_page)
    monkeypatch.setattr(fetch, "cached_call", lambda namespace, data, fn, ttl: fn(**data))
    fetch._memo.clear()
    yield
    fetch._memo.clear()


# ---- input resolution ------------------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    ("coffee", ["coffee"]),
    (" coffee ,  Tea,coffee ", ["coffee", "Tea"]),
    ("/m/0dl567,taylor swift", ["/m/0dl567", "taylor swift"]),
    ("https://trends.google.com/trends/explore?q=coffee,tea&geo=US", ["coffee", "tea"]),
    ("trends.google.com/trends/explore?q=%2Fm%2F0dl567&date=now%207-d", ["/m/0dl567"]),
])
def test_resolve_keywords(value, expected):
    assert refs.resolve_keywords(value) == expected


@pytest.mark.parametrize("value", ["a,b,c,d,e,f", " , ", "https://www.google.com/search?q=x",
                                   "https://trends.google.com/trends/explore?geo=US"])
def test_resolve_keywords_rejects(value):
    with pytest.raises(ValueError):
        refs.resolve_keywords(value)


@pytest.mark.parametrize("value, expected", [
    ("us", "US"), ("UK", "GB"), ("us-ca", "US-CA"), ("US-CA-803", "US-CA-803"), ("GB-ENG", "GB-ENG"),
    ("worldwide", ""), ("", ""), ("https://trends.google.com/trending?geo=IN", "IN"),
])
def test_normalize_geo(value, expected):
    assert refs.normalize_geo(value) == expected


@pytest.mark.parametrize("value, kwargs", [("XX", {}), ("US-CA-803", {"allow_metro": False}),
                                            ("worldwide", {"allow_worldwide": False}), ("United States", {})])
def test_normalize_geo_rejects(value, kwargs):
    with pytest.raises(ValueError):
        refs.normalize_geo(value, **kwargs)


@pytest.mark.parametrize("value, expected", [
    (None, "today 12-m"), ("past_7_days", "now 7-d"), ("Past 90 Days", "today 3-m"), ("all", "all"),
    ("today 5-y", "today 5-y"), ("2024-01-01 2024-12-31", "2024-01-01 2024-12-31"),
    ("2024-01-01..2024-12-31", "2024-01-01 2024-12-31"), ("2026-09-20T00 2026-09-23T00", "2026-09-20T00 2026-09-23T00"),
])
def test_resolve_timeframe(value, expected):
    assert refs.resolve_timeframe(value) == expected


@pytest.mark.parametrize("value", ["yesterday", "2024-12-31 2024-01-01", "2003-01-01 2004-06-01",
                                   "2099-01-01 2099-02-01", "2026-09-01T00 2026-09-20T00"])
def test_resolve_timeframe_rejects(value):
    with pytest.raises(ValueError):
        refs.resolve_timeframe(value)


def test_trend_ids_round_trip():
    tokens = [[4845949982, "en", "US"], [4846085008, "es", "US"], [1234567, "ko", "US"], [7654321, "en", "US"]]
    encoded = refs.encode_news_tokens(tokens)
    assert encoded == "4845949982.en.US,4846085008.es.US,1234567.ko.US"
    assert refs.decode_news_tokens(encoded) == tokens[:3]
    assert refs.resolve_trend(encoded)["tokens"] == tokens[:3]
    assert refs.resolve_trend("south korea vs ecuador") == {"tokens": None, "title": "south korea vs ecuador"}


# ---- schemas ---------------------------------------------------------------------------------------

def test_explore_link_fills_params():
    data, err = load_query(schemas.InterestOverTimeSchema, {
        "keywords": "https://trends.google.com/trends/explore?q=coffee,tea&geo=US-CA&date=today%203-m&cat=71&gprop=youtube"})
    assert err is None
    assert data == {"keywords": ["coffee", "tea"], "geo": "US-CA", "timeframe": "today 3-m", "category": 71,
                    "property": "youtube", "language": "en"}
    data, _ = load_query(schemas.InterestOverTimeSchema, {"keywords": "https://trends.google.com/trends/explore?q=x&geo=US",
                                                         "geo": "GB"})
    assert data["geo"] == "GB"     # an explicit param wins over the link


def test_schema_errors():
    assert load_query(schemas.CompareGeosSchema, {"keyword": "coffee", "geos": "US,worldwide"})[1]
    assert load_query(schemas.CompareGeosSchema, {"keyword": "coffee,tea", "geos": "US,GB"})[1]
    assert load_query(schemas.TrendingSchema, {"hours": "500"})[1]
    assert load_query(schemas.TrendingSchema, {"typo": "1"})[1]
    data, _ = load_query(schemas.TrendingSchema, {"category": "Travel and Transportation"})
    assert data["category"] == 19 and data["geo"] == "US"
    assert load_query(schemas.TrendingSchema, {"category": "sports"})[0]["category"] == 17


# ---- classification ---------------------------------------------------------------------------------

@pytest.mark.parametrize("status, url, text, exc", [
    (429, "https://trends.google.com/x", "Too Many Requests", fetch.TrendsBlocked),
    (200, "https://www.google.com/sorry/index?continue=x", "", fetch.TrendsBlocked),
    (429, "https://trends.google.com/x", "Our systems have detected unusual traffic", fetch.TrendsBlocked),
    (400, "https://trends.google.com/x", "", fetch.TrendsBadRequest),
    (404, "https://trends.google.com/x", "", fetch.TrendsNotFound),
    (401, "https://trends.google.com/x", "", fetch.TrendsUpstreamError),
    (500, "https://trends.google.com/x", "", fetch.TrendsUpstreamError),
])
def test_classify(status, url, text, exc):
    with pytest.raises(exc):
        fetch._classify(status, url, text, "t")


def test_decode_batch_errors_and_nulls():
    text = ")]}'\n\n" + json.dumps([["wrb.fr", "i0OFE", None, None, None, [3], "1"],
                                   ["wrb.fr", "wAgrOe", '["California"]', None, None, None, "2"]])
    out = fetch.decode_batch(text)
    assert out["1"] == ("i0OFE", None, 3) and out["2"] == ("wAgrOe", ["California"], None)
    with pytest.raises(fetch.TrendsUpstreamError):
        fetch.decode_batch(")]}'\n\n[]")


# ---- trending now ------------------------------------------------------------------------------------

def test_trending():
    out = trending.trending(geo="US", hours=24, articles=2, limit=10)
    assert out["geo"] == {"code": "US", "name": "United States"}
    assert out["pagination"]["total_count"] > 100 and len(out["trends"]) == 10
    t = out["trends"][0]
    assert list(t) == ["id", "title", "link", "search_volume", "growth_percent", "is_active", "started_at",
                       "ended_at", "categories", "related_queries", "articles", "articles_count"]
    assert isinstance(t["search_volume"], int) and t["started_at"].endswith("Z")
    assert refs.decode_news_tokens(t["id"]) and t["title"].lower() not in [q.lower() for q in t["related_queries"]]
    assert 1 <= len(t["articles"]) <= 2 and t["articles"][0]["link"].startswith("http")
    assert all(c["name"] for c in t["categories"])


def test_trending_filters_and_sort():
    everything = trending.trending(geo="US", limit=100)["pagination"]["total_count"]
    sports = trending.trending(geo="US", category=17, sort="search_volume", limit=100)
    assert 0 < sports["pagination"]["total_count"] < everything
    volumes = [t["search_volume"] for t in sports["trends"]]
    assert volumes == sorted(volumes, reverse=True)
    active = trending.trending(geo="US", status="active", limit=100)["trends"]
    assert active and all(t["is_active"] and t["ended_at"] is None for t in active)
    with pytest.raises(ValueError):
        trending.trending(geo="US", page=999)


@pytest.mark.parametrize("row", [[], [None], ["  "], [123], ["x", None, None, None, None], ["x"] + [None] * 20])
def test_parse_trend_partial_rows(row):
    parsed = trending.parse_trend(row, "US", 24)
    assert parsed is None or (parsed["title"] == "x" and parsed["categories"] == [] and parsed["articles"] == [])


def test_trend_news_by_id_and_title():
    first = trending.trending(geo="US", limit=1)["trends"][0]
    by_id = trending.trend_news(trend=refs.resolve_trend(first["id"]), geo="US", limit=5)
    assert by_id["count"] == len(by_id["articles"]) > 0
    assert set(by_id["articles"][0]) == {"title", "link", "source", "published_at", "image"}
    by_title = trending.trend_news(trend=refs.resolve_trend(first["title"].upper()), geo="US")
    assert by_title["trend"]["title"] == first["title"]
    words = first["title"].split()
    partial = trending.trend_news(trend=refs.resolve_trend(max(words, key=len)), geo="US")
    assert partial["trend"]["title"] and partial["articles"]          # any text in a title finds a trend
    with pytest.raises(fetch.TrendsNotFound):
        trending.trend_news(trend=refs.resolve_trend("no such trend at all"), geo="US")


def test_trending_reference():
    countries = reference.trending_geos()
    assert countries["count"] > 100 and all(g["type"] == "country" for g in countries["geos"])
    gb = reference.trending_geos(country="GB")["geos"]
    assert [g["code"] for g in gb] == ["GB-ENG", "GB-NIR", "GB-SCT", "GB-WLS"]
    assert reference.trending_categories()["count"] == 19


# ---- explore ---------------------------------------------------------------------------------------

def test_interest_over_time():
    out = explore.interest_over_time(keywords=["coffee", "tea"], geo="US", timeframe="today 3-m")
    assert out["geo"] == {"code": "US", "name": "United States"}
    assert out["timeframe"]["value"] == "past_90_days" and re.match(r"\d{4}-\d{2}-\d{2}$", out["timeframe"]["start"])
    assert out["resolution"] == "day" and out["category"] == {"id": 0, "name": "All categories"}
    point = out["timeline"][0]
    assert re.match(r"\d{4}-\d{2}-\d{2}$", point["date"]) and set(point["values"]) == {"coffee", "tea"}
    assert out["timeline"][-1]["is_partial"] is True and all(isinstance(v, int) for v in out["averages"].values())


def test_interest_over_time_topic_hourly():
    out = explore.interest_over_time(keywords=["/m/0dl567"], geo="", timeframe="now 7-d")
    assert out["keywords"] == [{"keyword": "/m/0dl567", "name": "Taylor Swift", "is_topic": True}]
    assert out["geo"] == {"code": None, "name": "Worldwide"} and out["resolution"] == "hour"
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:00:00Z$", out["timeline"][0]["date"])
    assert out["timeframe"]["start"].endswith("Z")


def test_interest_by_region_metro_share():
    out = explore.interest_by_region(keywords=["coffee", "tea"], geo="US", timeframe="today 3-m", resolution="metro")
    assert out["resolution"] == "metro" and out["value_type"] == "share_percent" and out["count"] > 100
    row = out["regions"][0]
    assert re.match(r"US-[A-Z]{2}-\d{3}$", row["code"]) and row["top_keyword"] in ("coffee", "tea")
    assert sum(row["values"].values()) in (99, 100, 101)


def test_interest_by_region_city():
    out = explore.interest_by_region(keywords=["coffee"], geo="US-CA", timeframe="today 12-m", resolution="city")
    row = out["regions"][0]
    assert row["code"] is None and row["coordinates"]["latitude"] and row["top_keyword"] is None
    assert out["value_type"] == "interest"


def test_interest_by_region_invalid_resolution():
    with pytest.raises(fetch.TrendsBadRequest):
        explore.interest_by_region(keywords=["coffee"], geo="US-CA", resolution="country")
    with pytest.raises(fetch.TrendsBadRequest):
        explore._geo_request({"request": {}}, "GB", "metro", False)       # DMA exists only in the US
    with pytest.raises(fetch.TrendsBadRequest):
        explore._geo_request({"request": {}}, "", "region", False)        # worldwide -> country / city only


def test_related_queries():
    out = explore.related_queries(keywords=["coffee", "tea"], geo="US", timeframe="today 3-m")
    assert [r["keyword"] for r in out["results"]] == ["coffee", "tea"]
    coffee = out["results"][0]
    assert coffee["top"][0]["value"] == 100 and coffee["top"][0]["link"].startswith("https://trends.google.com/")
    assert all(set(r) == {"query", "growth_percent", "is_breakout", "link"} for r in coffee["rising"])
    assert out["rising_compared_to"] is None or out["rising_compared_to"]["start"]


def test_explore_all_in_one():
    out = explore.explore(keywords=["coffee", "tea"], geo="US", timeframe="today 3-m", resolution="metro")
    assert out["interest_over_time"]["timeline"] and out["interest_by_region"]["regions"]
    assert len(out["related_queries"]["results"]) == 2


def test_compare_timeframes():
    out = explore.compare_timeframes(keyword="coffee", timeframes=["2024-01-01 2024-12-31", "2025-01-01 2025-12-31"],
                                     geo="US")
    assert out["keyword"]["name"] == "coffee"     # " - United States" suffix dropped
    assert [t["start"] for t in out["timeframes"]] == ["2024-01-01", "2025-01-01"]
    assert all(isinstance(t["average"], int) for t in out["timeframes"])
    assert out["timeline"][0]["position"] == 1 and len(out["timeline"][0]["points"]) == 2


def test_autocomplete():
    out = explore.autocomplete(query="tesla")
    assert out["count"] == 5 and out["topics"][0]["id"].startswith("/") and "q=/m/" in out["topics"][0]["link"]


def test_partial_widget_data_never_crashes():
    assert explore._timeline({}, ["a"], "WEEK") == ({"a": None}, [])
    assert explore._timeline({"default": {"timelineData": [{"time": "x"}]}}, ["a"], None)[1][0]["values"] == {"a": None}
    assert explore._regions({"default": {"geoMapData": [{}]}}, ["a"], True)[0]["name"] is None
    assert explore._ranked({"rankedKeyword": [{}, {"query": "q"}]}, True)[0]["query"] == "q"
    assert explore._timeframe("today 3-m", None) == {"value": "past_90_days", "start": None, "end": None}


# ---- reference ---------------------------------------------------------------------------------------

def test_geos():
    assert reference.geos()["count"] == 250
    metros = reference.geos(country="US-CA")["geos"]
    assert metros and all(g["type"] == "metro" and g["parent_code"] == "US-CA" for g in metros)
    assert reference.geos(query="bavaria")["geos"][0]["code"] == "DE-BY"
    assert reference.geo_name("US-CA-803") == "Los Angeles CA"
    with pytest.raises(fetch.TrendsNotFound):
        reference.geos(country="CS")     # an ISO code Google Trends does not list


def test_categories():
    top = reference.categories(parent=0)
    assert top["count"] > 20 and all(c["level"] == 1 for c in top["categories"])
    coffee = reference.categories(query="coffee & tea")["categories"][0]
    assert coffee["path"][-1] == "Coffee & Tea" and coffee["parent_id"]
    assert reference.category_ref(71)["name"]


# ---- year in search ----------------------------------------------------------------------------------

def test_year_in_search_2025():
    out = year_in_search.year_in_search(year=2025, country="US")
    assert out["country"] == {"code": "US", "name": "United States of America"}
    assert out["lists_count"] == len(out["lists"]) > 20
    first = out["lists"][0]
    assert first["section"] == "Trending" and first["name"] == "Searches"
    assert first["items"][0] == {"rank": 1, "term": "Charlie Kirk",
                                 "search_link": "https://www.google.com/search?q=Charlie%20Kirk",
                                 "explore_link": "https://trends.google.com/trends/explore?q=Charlie%20Kirk"}
    assert len({(l["section"], l["name"]) for l in out["lists"]}) == len(out["lists"])   # share modals deduped


def test_year_in_search_old_layout_and_countries():
    out = year_in_search.year_in_search(year=2019)
    assert out["country"]["name"] == "Worldwide" and out["lists"] and out["lists"][0]["section"] is None
    assert all(i["rank"] for l in out["lists"] for i in l["items"])
    with pytest.raises(fetch.TrendsNotFound):
        year_in_search.year_in_search(year=2025, country="ZW")
