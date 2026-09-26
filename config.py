"""Configuration for the Google Trends Scraper. Everything can be set with an
environment variable; the defaults work out of the box.

    PORT                 port the API listens on (default 8000)
    GOOGLE_TRENDS_PROXY  proxy URL for the Explore calls (interest over time /
                         by region, related queries, comparisons, autocomplete,
                         locations, categories), e.g. http://user:pass@host:port
                         (default: none — direct). Google shows one IP a captcha
                         after roughly 40 Explore calls in a couple of minutes;
                         a rotating residential proxy (a new exit per
                         connection) avoids that at volume. Trending Now and
                         Year in Search are never rate limited and always go
                         direct unless the direct call is refused.

Everything else below is a plain constant with a working default — edit it
here if you need to.
"""
import os

PORT = int(os.environ.get("PORT", "8000"))

# Retry policy for transport errors and blocks (every request).
MAX_RETRIES = 3
RETRY_BACKOFF = 2          # seconds, multiplied by the attempt number

GOOGLE_TRENDS_PROXY = os.environ.get("GOOGLE_TRENDS_PROXY") or None

GOOGLE_TRENDS_EXPLORE_CALLS_PER_EXIT = 30   # new connection (= new exit on a rotating proxy) after this many calls
GOOGLE_TRENDS_DIRECT_COOLDOWN = 900         # seconds a refused thread keeps using the proxy
GOOGLE_TRENDS_MAX_ATTEMPTS = 4              # attempts per call (a new connection after each block)
GOOGLE_TRENDS_BROWSER_FALLBACK = False      # the hosted service's headful-browser fallback is not part of this repo
GOOGLE_TRENDS_BROWSER_IDLE_SECONDS = 300


def google_trends_open_proxy():
    """Trending Now + Year in Search: direct."""
    return None


def google_trends_explore_proxy():
    """Explore calls: GOOGLE_TRENDS_PROXY, else direct."""
    return os.environ.get("GOOGLE_TRENDS_PROXY") or None


def google_trends_fallback_proxy():
    """Where a refused direct call moves to: GOOGLE_TRENDS_PROXY, else nowhere."""
    return os.environ.get("GOOGLE_TRENDS_PROXY") or None
