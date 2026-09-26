"""Reference data: the explore geo + category pickers, the trending-now geo
list and the trending categories — plus the code -> name lookups the other
modules use to label their responses.

    /trends/api/explore/pickers/geo       250 countries -> regions -> US metros (DMA)
    /trends/api/explore/pickers/category  1,427 categories, 6 levels deep
    batchexecute DqDTgb                   125 trending countries + their subregions
"""
from datetime import timedelta

from google_trends import fetch
from google_trends.refs import normalize_geo
from google_trends.shared import at, clean_text

PICKER_TTL = timedelta(days=7)
TRENDING_GEOS_TTL = timedelta(days=1)

# Trending-now categories (the /trending page's filter; ids from its menu,
# 12 is unused upstream).
TRENDING_CATEGORIES = {
    1: "Autos and Vehicles", 2: "Beauty and Fashion", 3: "Business and Finance", 4: "Entertainment",
    5: "Food and Drink", 6: "Games", 7: "Health", 8: "Hobbies and Leisure", 9: "Jobs and Education",
    10: "Law and Government", 11: "Other", 13: "Pets and Animals", 14: "Politics", 15: "Science",
    16: "Shopping", 17: "Sports", 18: "Technology", 19: "Travel and Transportation", 20: "Climate",
}


def _hl(language):
    return language or "en-US"


# ---- explore geo picker ------------------------------------------------------------------------

def _build_geos(language):
    tree = fetch.api("/trends/api/explore/pickers/geo", {"hl": _hl(language), "tz": 0}, "geo picker")
    rows = []
    for country in (tree or {}).get("children") or []:
        cc = country.get("id")
        if not cc:
            continue
        regions = country.get("children") or []
        rows.append({"code": cc, "name": country.get("name"), "type": "country", "parent_code": None,
                     "regions_count": len(regions)})
        for region in regions:
            rid = region.get("id")
            if not rid:
                continue
            rcode = rid if rid.startswith(cc + "-") else f"{cc}-{rid}"
            metros = region.get("children") or []
            rows.append({"code": rcode, "name": region.get("name"), "type": "region", "parent_code": cc,
                         "regions_count": len(metros)})
            for metro in metros:
                mid = metro.get("id")
                if mid:
                    rows.append({"code": f"{rcode}-{mid}", "name": metro.get("name"), "type": "metro",
                                 "parent_code": rcode, "regions_count": 0})
    if not rows:
        raise fetch.TrendsUpstreamError("the geo picker came back empty")
    return rows


def explore_geos(language="en-US"):
    return fetch.dataset("explore_geos", {"hl": _hl(language)}, lambda: _build_geos(language), PICKER_TTL)


def geo_name(code, language="en-US"):
    """'US-CA' -> 'California' (None when unknown or the lookup fails)."""
    if not code:
        return "Worldwide"
    try:
        for row in explore_geos(language):
            if row["code"] == code:
                return row["name"]
    except Exception as e:
        print(f"google-trends: geo picker lookup failed: {type(e).__name__}: {e}")
    return None


def metro_codes(language="en-US"):
    """{Nielsen DMA id: 'US-XX-NNN'} — metro rows of a region breakdown carry
    the bare DMA id; this turns it back into a code `geo` accepts."""
    try:
        return {r["code"].rsplit("-", 1)[1]: r["code"] for r in explore_geos(language) if r["type"] == "metro"}
    except Exception as e:
        print(f"google-trends: geo picker lookup failed: {type(e).__name__}: {e}")
        return {}


def geo_ref(code, language="en-US"):
    return {"code": code or None, "name": geo_name(code, language)}


def geos(country=None, query=None, language="en-US"):
    """Every geo explore accepts: countries, or one country's regions and US
    metros (`country`), or a name / code search (`query`) across all levels."""
    rows = explore_geos(language)
    parent = normalize_geo(country, allow_metro=False, allow_worldwide=False) if country else None
    if parent:
        rows = [r for r in rows if r["code"] != parent and r["code"].startswith(parent + "-")]
        if not rows and not any(r["code"] == parent for r in explore_geos(language)):
            raise fetch.TrendsNotFound(f"Google Trends has no geo {parent}")
    elif not query:
        rows = [r for r in rows if r["type"] == "country"]
    if query:
        q = query.lower()
        rows = [r for r in rows if q in (r["name"] or "").lower() or q == r["code"].lower()]
    return {"country": parent, "query": query, "count": len(rows), "geos": rows}


# ---- explore category picker ---------------------------------------------------------------------

def _build_categories(language):
    tree = fetch.api("/trends/api/explore/pickers/category", {"hl": _hl(language), "tz": 0}, "category picker")
    rows = []

    def walk(node, parent_id, path):
        for child in node.get("children") or []:
            cid, name = child.get("id"), child.get("name")
            if cid is None:
                continue
            here = path + [name]
            rows.append({"id": cid, "name": name, "parent_id": parent_id, "level": len(here), "path": here,
                         "subcategories_count": len(child.get("children") or [])})
            walk(child, cid, here)

    walk(tree or {}, (tree or {}).get("id", 0), [])
    if not rows:
        raise fetch.TrendsUpstreamError("the category picker came back empty")
    return rows


def explore_categories(language="en-US"):
    return fetch.dataset("explore_categories", {"hl": _hl(language)}, lambda: _build_categories(language),
                         PICKER_TTL)


def category_ref(cid, language="en-US"):
    if not cid:
        return {"id": 0, "name": "All categories"}
    name = None
    try:
        name = next((r["name"] for r in explore_categories(language) if r["id"] == cid), None)
    except Exception as e:
        print(f"google-trends: category picker lookup failed: {type(e).__name__}: {e}")
    return {"id": cid, "name": name}


def categories(parent=None, query=None, language="en-US"):
    """The explore category taxonomy, flat (id, name, parent_id, level, path):
    all of it, one category's direct children (`parent`), or a name search."""
    rows = explore_categories(language)
    if parent is not None:
        if parent and not any(r["id"] == parent for r in rows):
            raise fetch.TrendsNotFound(f"Google Trends has no category {parent}")
        rows = [r for r in rows if r["parent_id"] == parent]
    if query:
        q = query.lower()
        rows = [r for r in rows if q in (r["name"] or "").lower()]
    return {"parent_id": parent, "query": query, "count": len(rows), "categories": rows}


# ---- trending now reference ------------------------------------------------------------------------

def _build_trending_geos(language):
    data = fetch.batch([("DqDTgb", [_hl(language), 1, 0])], language=_hl(language), label="trending geos")[0]
    rows = []
    for entry in at(data, 0) or []:
        country = at(entry, 0) or []
        cc = at(country, 0)
        if not isinstance(cc, str):
            continue
        subs = at(entry, 1) or []
        rows.append({"code": cc, "name": clean_text(at(country, 1)), "type": "country", "parent_code": None,
                     "regions_count": len(subs)})
        for sub in subs:
            code = at(sub, 0)
            if isinstance(code, str):
                rows.append({"code": code, "name": clean_text(at(sub, 1)), "type": "region", "parent_code": cc,
                             "regions_count": 0})
    if not rows:
        raise fetch.TrendsUpstreamError("the trending geo list came back empty")
    return rows


def trending_geo_rows(language="en-US"):
    return fetch.dataset("trending_geos", {"hl": _hl(language)}, lambda: _build_trending_geos(language),
                         TRENDING_GEOS_TTL)


def trending_geos(country=None, query=None, language="en-US"):
    """The countries (and their subregions) Trending Now covers."""
    rows = trending_geo_rows(language)
    parent = normalize_geo(country, allow_metro=False, allow_worldwide=False) if country else None
    if parent:
        rows = [r for r in rows if r["parent_code"] == parent]
        if not rows and not any(r["code"] == parent for r in trending_geo_rows(language)):
            raise fetch.TrendsNotFound(f"Trending Now does not cover {parent}")
    elif not query:
        rows = [r for r in rows if r["type"] == "country"]
    if query:
        q = query.lower()
        rows = [r for r in rows if q in (r["name"] or "").lower() or q == r["code"].lower()]
    return {"country": parent, "query": query, "count": len(rows), "geos": rows}


def trending_categories():
    """The Trending Now category filter."""
    rows = [{"id": cid, "name": name} for cid, name in sorted(TRENDING_CATEGORIES.items(), key=lambda kv: kv[1])]
    return {"count": len(rows), "categories": rows}
