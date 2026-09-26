# 📈 Google Trends Scraper

Google Trends Scraper is a **free and open-source** scraper that gets you **unlimited** detailed Google Trends data for free.

## ✨ What Can I Get?

- 📈 **Interest over time since 2004** — 0-100 curves for up to 5 keywords, from minute to monthly resolution
- 🗺️ **Interest across 250 countries & 210 US metros** — by country, region, city or metro, plus top & rising queries
- 🔥 **Trending Now in 125 countries** — search volume, growth, related queries & news articles for every trend
- 🏆 **Every Year in Search since 2001** — Google's ranked top searches, news & people, per country

## 🎥 Example: A Full Google Trends Report

```json
{
  "keywords": [{ "keyword": "chatgpt", "name": "chatgpt", "is_topic": false }],
  "geo": { "code": null, "name": "Worldwide" },
  "timeframe": { "value": "past_12_months", "start": "2025-09-26", "end": "2026-09-26" },
  "link": "https://trends.google.com/trends/explore?q=chatgpt&date=today%2012-m",
  "interest_over_time": {
    "resolution": "week",
    "timeline": [
      { "date": "2026-09-13", "values": { "chatgpt": 66 }, "is_partial": false },
      { "date": "2026-09-20", "values": { "chatgpt": 67 }, "is_partial": true }
    ]
  },
  "interest_by_region": {
    "resolution": "country",
    "value_type": "interest",
    "count": 88,
    "regions": [
      { "code": "RW", "name": "Rwanda", "values": { "chatgpt": 100 } },
      { "code": "LK", "name": "Sri Lanka", "values": { "chatgpt": 82 } }
    ]
  },
  "related_queries": {
    "rising_compared_to": { "start": "2024-09-25", "end": "2025-09-25" },
    "results": [
      {
        "keyword": "chatgpt",
        "top": [{ "query": "chatgpt ai", "value": 100, "link": "https://trends.google.com/trends/explore?q=chatgpt+ai&date=today+12-m" }],
        "rising": [{ "query": "how to use chatgpt effectively", "growth_percent": 2550, "is_breakout": false, "link": "https://trends.google.com/trends/explore?q=how+to+use+chatgpt+effectively&date=today+12-m" }]
      }
    ]
  }
}
```

*Trimmed for readability.*

## 🚀 Unlimited Free Google Trends Data — Get It in 60 Seconds

1️⃣ Clone and install:
```bash
git clone https://github.com/omkarcloud/google-trends-scraper
cd google-trends-scraper
python -m pip install -r requirements.txt
```

2️⃣ Start the API:
```bash
python run.py
```

3️⃣ Get your first data:
```bash
curl "http://localhost:8000/explore?keywords=chatgpt"
```

```json
{
  "keywords": [{ "keyword": "chatgpt", "name": "chatgpt", "is_topic": false }],
  "geo": { "code": null, "name": "Worldwide" },
  "timeframe": { "value": "past_12_months", "start": "2025-09-26", "end": "2026-09-26" },
  "interest_over_time": {
    "resolution": "week",
    "timeline": [
      { "date": "2026-09-13", "values": { "chatgpt": 67 }, "is_partial": false },
      { "date": "2026-09-20", "values": { "chatgpt": 68 }, "is_partial": true }
    ]
  },
  "interest_by_region": {
    "regions": [{ "code": "RW", "name": "Rwanda", "values": { "chatgpt": 100 } }]
  },
  "related_queries": {
    "results": [{
      "keyword": "chatgpt",
      "top": [{ "query": "chatgpt ai", "value": 100 }],
      "rising": [{ "query": "chatgpt 5.2", "growth_percent": 1400, "is_breakout": false }]
    }]
  }
}
```

All 15 endpoints are now live at `http://localhost:8000`.

Running at high volume? Explore calls go direct by default, and Google shows one IP a captcha after about 40 of them in a couple of minutes. Set `GOOGLE_TRENDS_PROXY=http://user:pass@host:port` (a rotating residential proxy) and keep going.

## 📚 Endpoints

15 endpoints cover everything you need.

| Endpoint | Path | Returns |
|---|---|---|
| Explore All Data | `/explore` | Interest over time, by region and related queries in one call |
| Interest Over Time | `/interest-over-time` | 0-100 timeline for up to 5 keywords, minute to monthly |
| Interest by Region | `/interest-by-region` | Interest by country, region, city or US metro |
| Related Queries | `/related-queries` | Top and rising searches with growth and breakout flags |
| Compare Timeframes / Locations | `/compare-timeframes`, `/compare-geos` | One keyword across 2-5 periods or places, one scale |
| Autocomplete Topics | `/autocomplete` | Any term turned into Google topic ids |
| Trending Now | `/trending` | Live trends with search volume, growth, related queries and news |
| Trending News | `/trending/news` | Up to 30 news articles behind any trend |
| Year in Search | `/year-in-search` | Google's ranked top searches, news and people since 2001 |
| Locations / Categories | `/geos`, `/categories` | Every location and 1,400+ categories explore accepts |
| Trending Locations / Categories | `/trending/geos`, `/trending/categories` | Countries, regions and categories Trending Now covers |
| Year in Search Countries | `/year-in-search/countries` | Every country and year with an edition |


## 🔍 Exploring Parameters

The same API is published on RapidAPI, and its playground is the easiest place to try parameters and see raw responses. Once a request looks right, run it locally for **unlimited free** data.

1. [Subscribe to the free plan](https://rapidapi.com/OmkarCloud/api/best-google-trends-scraper-free-1000-calls/pricing) — 1,000 calls/month, no credit card.
2. [Try the endpoints in the playground](https://rapidapi.com/OmkarCloud/api/best-google-trends-scraper-free-1000-calls/playground) — every param is pre-filled, so you see real data in one click.
3. Copy the generated code and replace `https://best-google-trends-scraper-free-1000-calls.p.rapidapi.com` with `http://localhost:8000`. It will now run against your local API.

```python
import requests

# generated by the playground, host swapped for the local API
response = requests.get(
    "http://localhost:8000/interest-over-time",
    params={"keywords": "chatgpt,gemini,claude"},
)
print(response.json())
```

## 💬 Have Questions? We Have Answers.

You're a developer — we know how hard completing a project can be. So we offer full support: just message us and we'll reply ✅ with a solution within 1 working day.

[![Message Us on WhatsApp about Google Trends Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/whatsapp-us.png)](https://api.whatsapp.com/send?phone=918178804274&text=I%20need%20help%20using%20the%20Google%20Trends%20Scraper%20API.)

[![Ask Us by Email about Google Trends Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/ask-on-email.png)](mailto:happy.to.help@omkar.cloud?subject=Help%20with%20Google%20Trends%20Scraper%20API&body=I%20need%20help%20using%20the%20Google%20Trends%20Scraper%20API.)

## ⚡ Popular Scrapers by Omkar Cloud

- [**Google Maps Scraper (3,100+ GitHub Stars)**](https://github.com/omkarcloud/google-maps-scraper) — type "dentists in New York", get every business as a ready-to-call lead list: phones, emails, websites & reviews. Up to 100K free leads/month.
- [**Google Search Scraper**](https://www.omkar.cloud/tools/google-search-scraper) — Google results, AI Overviews, images, news & shopping as clean JSON
- [**G2 Scraper**](https://www.omkar.cloud/tools/g2-scraper) — G2 product details, ratings & AI-found contacts
- [**Website Email Contact Scraper**](https://www.omkar.cloud/tools/website-email-contact-scraper) — emails, phones & socials from any website
- [**AliExpress Scraper**](https://www.omkar.cloud/tools/aliexpress-scraper) — live product details, SKU variants, stock & shipping
- [**Booking Scraper**](https://www.omkar.cloud/tools/booking-scraper) — Booking.com hotels: prices, ratings, rooms & amenities

## ⭐ Love It? [Star It ⭐!](https://github.com/omkarcloud/google-trends-scraper)

Star the repo ⭐ and become my star hero!

It's just 1 click, but it means the world to me.

[![Star us on GitHub](https://raw.githubusercontent.com/omkarcloud/google-maps-scraper/master/screenshots/star-us.png)](https://github.com/omkarcloud/google-trends-scraper)
