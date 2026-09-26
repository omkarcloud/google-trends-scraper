"""Live endpoint smoke tests: one or more calls per /google-trends/* route
against a running service, with example values proven to return data
(2026-09-26). The listing tooling reads these calls as each route's working
example.

Skipped unless GOOGLE_TRENDS_BASE points at a running service:

    ONLY_SCRAPER=google-trends python run.py
    GOOGLE_TRENDS_BASE=http://127.0.0.1:6002 python -m pytest google_trends/test_endpoints.py -q
"""
import os

import pytest

BASE = os.environ.get("GOOGLE_TRENDS_BASE", "").rstrip("/")

pytestmark = pytest.mark.skipif(not BASE, reason="set GOOGLE_TRENDS_BASE to run live endpoint tests")


def call(path, expect=200, **params):
    from curl_cffi import requests
    resp = requests.get(BASE + path, params=params, timeout=300)
    assert resp.status_code == expect, f"{path} {params} -> {resp.status_code} {resp.text[:300]}"
    body = resp.json()
    assert body, f"{path} returned an empty body"
    return body


# ---- trending now ------------------------------------------------------------------------------

def test_trending():
    body = call("/google-trends/trending", geo="US", hours=24, articles=2)
    trend = body["trends"][0]
    assert body["geo"]["name"] == "United States" and body["count"] > 20 and body["next"]
    assert trend["title"] and trend["search_volume"] and trend["started_at"] and trend["id"]
    assert len(trend["articles"]) <= 2 and trend["link"].startswith("https://trends.google.com/")
    sports = call("/google-trends/trending", geo="US", hours=168, category="sports", status="active",
                  sort="search_volume", limit=10)["trends"]
    assert sports and all(any(c["id"] == 17 for c in t["categories"]) and t["is_active"] for t in sports)
    assert call("/google-trends/trending", geo="US-CA", hours=48)["geo"]["name"] == "California"
    assert call("/google-trends/trending", geo="JP", language="ja")["trends"]


def test_trend_news():
    trend = call("/google-trends/trending", geo="US", limit=1)["trends"][0]
    by_id = call("/google-trends/trending/news", trend=trend["id"], limit=20)
    assert by_id["articles"] and by_id["articles"][0]["link"] and by_id["articles"][0]["published_at"]
    by_title = call("/google-trends/trending/news", trend=trend["title"], geo="US", limit=5)
    assert by_title["trend"]["title"].lower() == trend["title"].lower() and by_title["articles"]
    assert call("/google-trends/trending/news", trend="weather")["articles"]
    call("/google-trends/trending/news", expect=404, trend="zz no such trend zz", geo="US")


def test_trending_reference():
    geos = call("/google-trends/trending/geos")["geos"]
    assert len(geos) > 100 and all(g["type"] == "country" for g in geos)
    assert len(call("/google-trends/trending/geos", country="GB")["geos"]) == 4
    assert call("/google-trends/trending/categories")["count"] == 19


# ---- explore -----------------------------------------------------------------------------------

def test_interest_over_time():
    body = call("/google-trends/interest-over-time", keywords="coffee,tea", geo="US", timeframe="past_90_days")
    assert body["resolution"] == "day" and len(body["timeline"]) > 80
    assert set(body["timeline"][0]["values"]) == {"coffee", "tea"} and body["averages"]["coffee"]
    topic = call("/google-trends/interest-over-time", keywords="/m/0dl567", timeframe="past_7_days")
    assert topic["keywords"][0]["name"] == "Taylor Swift" and topic["resolution"] == "hour"
    link = call("/google-trends/interest-over-time",
                keywords="https://trends.google.com/trends/explore?q=bitcoin&geo=GB&date=today%205-y")
    assert link["geo"]["code"] == "GB" and link["timeframe"]["value"] == "past_5_years"
    assert call("/google-trends/interest-over-time", keywords="coffee", timeframe="2024-01-01 2024-12-31",
                property="youtube", category=71)["timeline"]


def test_interest_by_region():
    states = call("/google-trends/interest-by-region", keywords="coffee", geo="US")
    assert states["resolution"] == "region" and states["regions"][0]["code"].startswith("US-")
    metros = call("/google-trends/interest-by-region", keywords="coffee,tea", geo="US", resolution="metro")
    assert metros["value_type"] == "share_percent" and metros["regions"][0]["code"].count("-") == 2
    cities = call("/google-trends/interest-by-region", keywords="coffee", geo="US-CA", resolution="city")
    assert cities["regions"][0]["coordinates"]
    assert call("/google-trends/interest-by-region", keywords="bitcoin")["resolution"] == "country"


def test_related_queries():
    body = call("/google-trends/related-queries", keywords="bitcoin,ethereum", geo="US")
    assert len(body["results"]) == 2 and body["results"][0]["top"] and body["results"][0]["rising"]
    assert "is_breakout" in body["results"][0]["rising"][0]


def test_explore():
    body = call("/google-trends/explore", keywords="python", geo="GB")
    assert body["interest_over_time"]["timeline"] and body["interest_by_region"]["regions"]
    assert body["related_queries"]["results"][0]["top"]


def test_comparisons():
    tf = call("/google-trends/compare-timeframes", keyword="coffee",
              timeframes="2024-01-01 2024-12-31,2025-01-01 2025-12-31", geo="US")
    assert len(tf["timeframes"]) == 2 and len(tf["timeline"][0]["points"]) == 2
    geos = call("/google-trends/compare-geos", keyword="coffee", geos="US,GB,CA")
    assert set(geos["averages"]) == {"US", "GB", "CA"} and geos["timeline"]


def test_autocomplete():
    topics = call("/google-trends/autocomplete", query="tesla")["topics"]
    assert topics and topics[0]["id"].startswith("/") and topics[0]["type"]


def test_reference():
    assert len(call("/google-trends/geos")["geos"]) > 200
    metros = call("/google-trends/geos", country="US-CA")["geos"]
    assert metros and metros[0]["type"] == "metro"
    assert call("/google-trends/geos", query="bavaria")["geos"][0]["code"] == "DE-BY"
    assert call("/google-trends/categories", parent=0)["count"] > 20
    assert call("/google-trends/categories", query="coffee")["categories"][0]["path"]


# ---- year in search ----------------------------------------------------------------------------

def test_year_in_search():
    body = call("/google-trends/year-in-search", year=2025, country="US")
    assert body["lists_count"] > 20 and body["lists"][0]["items"][0]["rank"] == 1
    assert call("/google-trends/year-in-search", year=2019)["lists"]
    assert call("/google-trends/year-in-search", year=2025, country="DE", language="de")["lists"]
    countries = call("/google-trends/year-in-search/countries")
    assert countries["count"] > 50 and 2001 in countries["years"]
    call("/google-trends/year-in-search", expect=404, year=2025, country="ZW")


def test_validation():
    call("/google-trends/interest-over-time", expect=400, keywords="a,b,c,d,e,f")
    call("/google-trends/interest-over-time", expect=400, keywords="coffee", timeframe="yesterday")
    call("/google-trends/trending", expect=400, geo="worldwide")
    call("/google-trends/compare-geos", expect=400, keyword="coffee", geos="US,worldwide")
