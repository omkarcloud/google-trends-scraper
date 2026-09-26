"""Google's Year in Search (trends.google.com/trends/yis/<year>/<geo>/, which
redirects to trends.withgoogle.com/[<lang>/]year-in-search/<year>/[<cc>/]):
server-rendered HTML, no cookie, no rate limit seen (validated 2026-09-26).

Layouts
  2020+ : sections (<h3> expansion panels: Trending, Entertainment, Sports,
          …) each holding cards (<h4 class="list-title">: Searches, News,
          People, …) of ranked items
  older : the same cards without the section panels
  item  : <li value="rank"> with the term (h4.list-item__term-heading), a
          google.com/search link and a trends explore link
Countries and editions: the region picker (#regionSelect li[data-region-code]),
years 2001 -> last year (links on every page). A country / year without an
edition answers 404.
"""
import re
from datetime import date, timedelta

import lxml.html

from google_trends import fetch
from google_trends.shared import clean_text

SITE = fetch.YIS_SITE
FIRST_YEAR = 2001
COUNTRIES_TTL = timedelta(days=30)


def latest_year():
    return date.today().year - 1


def _url(year, country, language):
    lang = (language or "en").strip()
    prefix = "" if lang.lower() in ("en", "en-us") else f"/{lang}"
    geo = f"{country.lower()}/" if country else ""
    return f"{SITE}{prefix}/year-in-search/{year}/{geo}"


def _load(year, country, language):
    try:
        html, final = fetch.page(_url(year, country, language), "year in search")
    except fetch.TrendsNotFound:
        where = country or "worldwide"
        raise fetch.TrendsNotFound(f"Google has no {year} Year in Search edition for {where}"
                                   + (f" in language {language}" if language and language != "en" else "")
                                   + " (see /google-trends/year-in-search/countries)")
    return lxml.html.fromstring(html), final


def _text(el):
    return clean_text(el.text_content()) if el is not None else None


def _first(el, selector):
    found = el.cssselect(selector)
    return found[0] if found else None


def _pick(*elements):
    """First element that exists (lxml elements without children are falsy,
    so `a or b` would skip a real but empty element)."""
    return next((e for e in elements if e is not None), None)


def _item(li):
    term_el = _pick(_first(li, ".list-item__term-heading"), _first(li, "a.item-term"), li)
    search = _pick(_first(li, "a.item-term"), _first(li, "a[href*='google.com/search']"))
    trends = _pick(_first(li, "a.list-item__trends-link"), _first(li, "a[href*='trends.google.com']"))
    term = _text(term_el) or clean_text(li.get("aria-label", "").split(". ", 1)[-1])
    rank = li.get("value") or li.get("data-list-index")
    return {
        "rank": int(rank) if rank and rank.isdigit() else None,
        "term": term,
        "search_link": search.get("href") if search is not None else None,
        "explore_link": trends.get("href") if trends is not None else None,
    }


def _section_name(card):
    """The <h3> expansion-panel heading of the section a card sits in."""
    node = card.getparent()
    while node is not None:
        if "glue-expansion-panel__content" in (node.get("class") or ""):
            heading = node.getprevious()
            label = _first(heading, ".glue-expansion-panel__header-text") if heading is not None else None
            return _text(label)
        node = node.getparent()
    return None


def _lists(doc):
    lists, seen = [], set()
    for card in doc.cssselect(".card-container"):
        title = _first(card, "h4.list-title")
        items = card.cssselect("ol.list-container > li.list-item") or card.cssselect("li.list-item")
        key = card.get("id") or card.get("data-card-id")
        if title is None or not items or (key and key in seen):
            continue
        if key:
            seen.add(key)
        lists.append({"section": _section_name(card), "name": _text(title),
                      "items": [i for i in (_item(li) for li in items) if i["term"]]})
    return lists


def _regions(doc):
    out = []
    for li in doc.cssselect("#regionSelect li[data-region-code]"):
        code = (li.get("data-region-code") or "").upper()
        if code and all(r["code"] != code for r in out):
            name = clean_text(li.get("data-locale")) or _text(li)
            out.append({"code": code, "name": name,
                        "has_edition": "is-disabled" not in (li.get("class") or "")})
    return out


def year_in_search(year=None, country=None, language="en"):
    """Google's Year in Search: the year's top-searched lists for a country
    (or worldwide) — trending searches, news, people, entertainment, sports…"""
    year = year or latest_year()
    doc, final = _load(year, country, language)
    lists = _lists(doc)
    if not lists:
        fetch.dump_debug("yis_nolists", lxml.html.tostring(doc, encoding="unicode")[:200000])
        raise fetch.TrendsUpstreamError("the Year in Search page carried no ranked lists (layout changed?)")
    name = None
    if country:
        name = next((r["name"] for r in _regions(doc) if r["code"] == country), None)
    return {
        "year": year,
        "country": {"code": country, "name": name} if country else {"code": None, "name": "Worldwide"},
        "language": language,
        "link": final,
        "lists_count": len(lists),
        "lists": lists,
    }


def countries(year=None):
    """The countries with a Year in Search edition, and every year covered."""
    year = year or latest_year()

    def build():
        doc, _ = _load(year, None, "en")
        html = lxml.html.tostring(doc, encoding="unicode")
        years = sorted({int(y) for y in re.findall(r"year-in-search/(\d{4})/", html)} | {year}, reverse=True)
        return {"regions": _regions(doc), "years": years}

    data = fetch.dataset("yis_countries", {"year": year}, build, COUNTRIES_TTL)
    rows = sorted(data["regions"], key=lambda r: r["name"] or "")
    return {"year": year, "years": data["years"], "count": len(rows), "countries": rows}
