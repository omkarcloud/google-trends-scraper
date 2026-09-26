"""Use the scraper straight from Python — no server needed.

    python main.py

Every function returns the same JSON the API does; results are written to
output/*.json.
"""
import json
import os

from google_trends.explore import explore
from google_trends.trending import trending
from google_trends.year_in_search import year_in_search

os.makedirs("output", exist_ok=True)


def save(name, data):
    path = os.path.join("output", name)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"saved {path}")


if __name__ == "__main__":
    # interest over time + by region + related queries, in one call
    save("explore_chatgpt.json", explore(keywords=["chatgpt"]))

    # what the US is searching for right now, with 2 news articles per trend
    save("trending_us.json", trending(geo="US", hours=24, articles=2))

    # Google's Year in Search 2025 for the United States
    save("year_in_search_2025_us.json", year_in_search(year=2025, country="US"))
