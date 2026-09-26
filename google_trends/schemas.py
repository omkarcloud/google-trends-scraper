"""Marshmallow request schemas for every /google-trends/* route.

Generic fields live in the shared schema_fields.py; this module adds the
Google Trends resolvers (google_trends/refs.py) and per-route schemas. Every
schema's load() output is the kwargs dict its endpoint function takes.

ONE param per input (tripadvisor QueryOrLinkField convention, never a
sibling `url` / `id` pair):
    keywords   terms or topic ids, comma-separated — OR a trends.google.com
               explore link, whose geo / date / cat / gprop also fill
               `geo` / `timeframe` / `category` / `property` when those are absent
    geo        US | US-CA | US-CA-803 | worldwide — OR a trends link with ?geo=
    trend      a trending search title — OR a trend `id` from /trending
"""
import re

from marshmallow import ValidationError, fields, pre_load, validate

from google_trends import explore, refs, trending
from google_trends.reference import TRENDING_CATEGORIES
from google_trends.year_in_search import FIRST_YEAR, latest_year
from schema_fields import (BaseSchema, ChoiceField, CountryCodeField, Flag, LanguageCodeField, LimitField,
                           PageField, QueryField, RefField, StrippedString)


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


# ---- fields ----------------------------------------------------------------------------------------------------

class KeywordsField(RefField):
    """Up to 5 comma-separated terms / topic ids, or an explore link."""
    resolver = staticmethod(refs.resolve_keywords)


class KeywordField(RefField):
    """Exactly one term / topic id (or an explore link with one keyword)."""

    @staticmethod
    def resolver(value):
        items = refs.resolve_keywords(value)
        if len(items) != 1:
            raise ValueError("Pass exactly one keyword here.")
        return items[0]


class GeoField(RefField):
    """Explore geo: country, region, US metro or worldwide (default)."""

    def __init__(self, allow_metro=True, allow_worldwide=True, default="", **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("load_default", default)
        kwargs.setdefault("validate", validate.Length(max=300))
        super().__init__(resolver=lambda v: refs.normalize_geo(v, allow_metro, allow_worldwide), **kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        if isinstance(value, str) and not value.strip():
            return self.load_default
        return super()._deserialize(value, attr, data, **kwargs)


class TrendingGeoField(GeoField):
    """Trending Now geo: a country or one of its subregions, default US."""

    def __init__(self, **kwargs):
        super().__init__(allow_metro=False, allow_worldwide=False, default="US", **kwargs)


class GeoListField(fields.Field):
    """2-5 comma-separated geos to compare."""

    def __init__(self, **kwargs):
        kwargs.setdefault("required", True)
        super().__init__(**kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        items = []
        for raw in str(value or "").split(","):
            if not raw.strip():
                continue
            try:
                code = refs.normalize_geo(raw, allow_worldwide=False)
            except ValueError as e:
                raise ValidationError(str(e))
            if code not in items:
                items.append(code)
        if not 2 <= len(items) <= refs.MAX_KEYWORDS:
            raise ValidationError("Pass 2 to 5 comma-separated geos (e.g. US,GB,CA or US-CA,US-NY); worldwide cannot be compared with other geos.")
        return items


class TimeframeField(RefField):
    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("load_default", refs.TIMEFRAMES[refs.DEFAULT_TIMEFRAME])
        super().__init__(resolver=refs.resolve_timeframe, **kwargs)


class TimeframesField(RefField):
    resolver = staticmethod(refs.resolve_timeframes)


class TrendRefField(RefField):
    resolver = staticmethod(refs.resolve_trend)


class CategoryField(fields.Integer):
    """Explore category id (see /google-trends/categories), default 0 = all."""

    def __init__(self, **kwargs):
        kwargs.setdefault("load_default", 0)
        kwargs.setdefault("validate", validate.Range(min=0, max=100000))
        super().__init__(strict=False, **kwargs)


class LanguageField(LanguageCodeField):
    def __init__(self, default="en", **kwargs):
        kwargs.setdefault("load_default", default)
        super().__init__(**kwargs)


# ---- explore ---------------------------------------------------------------------------------------------------

class _Options(BaseSchema):
    """Filters every explore route shares."""
    category = CategoryField()
    property = ChoiceField(list(explore.PROPERTIES), load_default="web")
    language = LanguageField()

    @pre_load
    def from_link(self, data, **kwargs):
        """A pasted explore link fills geo / timeframe / category / property."""
        source = data.get("keywords") or data.get("keyword") or ""
        if not refs.is_link(source):
            return data
        try:
            params = refs.link_params(source)
        except ValueError:
            return data     # the keywords field reports it
        data = dict(data)
        if "geo" in params and not data.get("geo") and "geo" in self.fields:
            data["geo"] = params["geo"] or "worldwide"
        if params.get("date") and not data.get("timeframe") and "timeframe" in self.fields:
            data["timeframe"] = params["date"]
        if params.get("cat") and not data.get("category"):
            data["category"] = params["cat"]
        if params.get("gprop") and not data.get("property"):
            gprop = params["gprop"]
            data["property"] = next((k for k, v in explore.PROPERTIES.items() if v == gprop), gprop)
        return data


class _Explore(_Options):
    keywords = KeywordsField()
    geo = GeoField()
    timeframe = TimeframeField()


class InterestOverTimeSchema(_Explore):
    pass


class InterestByRegionSchema(_Explore):
    resolution = ChoiceField(list(explore.RESOLUTIONS), load_default=None)
    include_low_volume = Flag(load_default=False)


class RelatedQueriesSchema(_Explore):
    pass


class ExploreSchema(_Explore):
    resolution = ChoiceField(list(explore.RESOLUTIONS), load_default=None)


class CompareTimeframesSchema(_Options):
    keyword = KeywordField()
    timeframes = TimeframesField()
    geo = GeoField()


class CompareGeosSchema(_Options):
    keyword = KeywordField()
    geos = GeoListField()
    timeframe = TimeframeField()


class AutocompleteSchema(BaseSchema):
    query = QueryField(max_length=100)
    language = LanguageField()


class GeosSchema(BaseSchema):
    country = StrippedString(load_default=None, validate=validate.Length(max=300))
    query = StrippedString(load_default=None, validate=validate.Length(max=100))
    language = LanguageField()


class CategoriesSchema(BaseSchema):
    parent = fields.Integer(strict=False, load_default=None, validate=validate.Range(min=0, max=100000))
    query = StrippedString(load_default=None, validate=validate.Length(max=100))
    language = LanguageField()


# ---- trending now ----------------------------------------------------------------------------------------------

_TRENDING_CATEGORY_CHOICES = {**{str(k): k for k in TRENDING_CATEGORIES},
                              **{v.lower(): k for k, v in TRENDING_CATEGORIES.items()},
                              **{_slug(v): k for k, v in TRENDING_CATEGORIES.items()}}


class TrendingSchema(BaseSchema):
    geo = TrendingGeoField()
    hours = fields.Integer(strict=False, load_default=24, validate=validate.Range(min=1, max=168))
    category = ChoiceField(_TRENDING_CATEGORY_CHOICES, load_default=None)
    status = ChoiceField(list(trending.STATUSES), load_default="all")
    sort = ChoiceField(list(trending.SORTS), load_default="relevance")
    articles = fields.Integer(strict=False, load_default=0, validate=validate.Range(min=0, max=trending.MAX_ARTICLES))
    language = LanguageField()
    page = PageField(max_page=1000)
    limit = LimitField(default=50, max_size=100)


class TrendNewsSchema(BaseSchema):
    trend = TrendRefField()
    geo = TrendingGeoField()
    limit = LimitField(default=10, max_size=trending.MAX_NEWS)
    language = LanguageField()


class TrendingGeosSchema(GeosSchema):
    pass


class EmptySchema(BaseSchema):
    pass


# ---- year in search --------------------------------------------------------------------------------------------

class YisCountryField(CountryCodeField):
    """ISO country, or empty / 'worldwide' for the global edition."""

    def _deserialize(self, value, attr, data, **kwargs):
        if isinstance(value, str) and value.strip().upper() in refs.WORLDWIDE:
            return None
        if isinstance(value, str) and value.strip().upper() == "UK":
            value = "GB"
        return super()._deserialize(value, attr, data, **kwargs)


def _year_field():
    return fields.Integer(strict=False, load_default=None,
                          validate=validate.Range(min=FIRST_YEAR, max=latest_year() + 1))


class YearInSearchSchema(BaseSchema):
    year = _year_field()
    country = YisCountryField()
    language = LanguageField()


class YearInSearchCountriesSchema(BaseSchema):
    year = _year_field()
