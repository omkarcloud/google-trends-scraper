"""The 15 Google Trends endpoints. Every path is served with and without the
`/google-trends` prefix, so code generated against the hosted API on RapidAPI
(paths like /interest-over-time) runs unchanged against this server.

Params are validated by the marshmallow schemas in google_trends/schemas.py:
ONE param per input — `keywords` takes terms / topic ids OR a Google Trends
explore link (whose geo / date / cat / gprop fill the other params), `geo` a
code OR a trends link, `trend` a title OR a trend id."""
import json
from urllib.parse import urlencode

from bottle import request, response, route

from google_trends import explore, reference, schemas, trending, year_in_search
from schema_fields import load_query
from scraper_errors import BadRequest, NotFound

PREFIX = "/google-trends"


def json_response(data, status=200):
    response.status = status
    response.content_type = "application/json"
    return json.dumps(data, ensure_ascii=False)


def query_dict():
    """The query as unicode strings (bottle 0.12's .get() hands back latin-1
    decoded bytes, so a UTF-8 "Amélie" would arrive as "AmÃ©lie")."""
    return {key: request.query.getunicode(key) for key in request.query.keys()}


def paginate(result, raw):
    """Lift the `pagination` block into the flat shape the hosted API returns
    (count / per_page / current_page / total_pages / next / previous)."""
    block = result.pop("pagination", None) or {}
    page = int(block.get("page") or 1)
    total_pages = int(block.get("total_pages") or 0)
    base = f"{request.urlparts.scheme}://{request.urlparts.netloc}{request.urlparts.path}"

    def link(n):
        params = {k: v for k, v in raw.items() if v not in (None, "")}
        params["page"] = n
        return f"{base}?{urlencode(params)}"

    out = {"count": block.get("total_count"), "per_page": block.get("items_per_page"), "current_page": page,
           "total_pages": total_pages, "next": link(page + 1) if page < total_pages else None,
           "previous": link(page - 1) if page > 1 else None}
    out.update(result)
    return out


def call(schema, fn, label, paginated=False):
    """Validate, run, map errors: bad params -> 400, nothing there -> 404,
    transport / blocks -> 500."""
    raw = query_dict()
    data, error = load_query(schema, raw)
    if error:
        return json_response(error, 400)
    try:
        result = fn(**data)
    except ValueError as e:                # bad params the schema could not catch
        return json_response({"error": str(e)}, 400)
    except BadRequest as e:                # Google rejected the request
        return json_response({"error": f"google trends rejected the request: {e}"}, 400)
    except NotFound as e:
        return json_response({"error": str(e) or "not found"}, 404)
    except Exception as e:                 # retries exhausted / blocked
        return json_response({"error": f"google trends {label} failed: {e}"}, 500)
    return json_response(paginate(result, raw) if paginated else result)


ENDPOINTS = [
    ("/explore", schemas.ExploreSchema, explore.explore, False),
    ("/interest-over-time", schemas.InterestOverTimeSchema, explore.interest_over_time, False),
    ("/interest-by-region", schemas.InterestByRegionSchema, explore.interest_by_region, False),
    ("/related-queries", schemas.RelatedQueriesSchema, explore.related_queries, False),
    ("/compare-timeframes", schemas.CompareTimeframesSchema, explore.compare_timeframes, False),
    ("/compare-geos", schemas.CompareGeosSchema, explore.compare_geos, False),
    ("/autocomplete", schemas.AutocompleteSchema, explore.autocomplete, False),
    ("/trending", schemas.TrendingSchema, trending.trending, True),
    ("/trending/news", schemas.TrendNewsSchema, trending.trend_news, False),
    ("/year-in-search", schemas.YearInSearchSchema, year_in_search.year_in_search, False),
    ("/geos", schemas.GeosSchema, reference.geos, False),
    ("/categories", schemas.CategoriesSchema, reference.categories, False),
    ("/trending/geos", schemas.TrendingGeosSchema, reference.trending_geos, False),
    ("/trending/categories", schemas.EmptySchema, reference.trending_categories, False),
    ("/year-in-search/countries", schemas.YearInSearchCountriesSchema, year_in_search.countries, False),
]


def mount(path, schema, fn, paginated):
    """Serve one endpoint at /path and /google-trends/path."""
    def handler():
        return call(schema, fn, path.strip("/"), paginated)
    handler.__name__ = "google_trends_" + path.strip("/").replace("/", "_").replace("-", "_")
    route(path, method="GET")(handler)
    route(PREFIX + path, method="GET")(handler)


for _path, _schema, _fn, _paginated in ENDPOINTS:
    mount(_path, _schema, _fn, _paginated)


@route("/", method="GET")
@route("/health", method="GET")
def health():
    return json_response({"status": "ok", "endpoints": [p for p, *_ in ENDPOINTS]})
