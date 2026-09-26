"""Google Trends transport: plain curl_cffi with Chrome TLS, a headful
patchright in-page fetch as the last resort. Validated 2026-09-24..26.

Upstreams
---------
  OPEN (no cookie, no token, never rate limited in testing; direct egress)
    POST trends.google.com/_/TrendsUi/data/batchexecute?rpcids=<id,…>
         f.req=[[["<rpc>","<json args>",null,"<tag>"],…]]  + x-same-domain: 1
           i0OFE  [null,null,<geo>,<articles per trend>,<hl>,<hours>,1]
                  -> [null, [trend, …]]  (trending now; null for an unknown geo)
           w4opAf [[[news id, lang, geo], …], <n ≤ 30>]  -> [[article, …]]
           DqDTgb [<hl>, 1, 0]  -> trending countries + their subregions
           wAgrOe [<geo>, <hl>]  -> [<geo name>]
    GET  trends.withgoogle.com/[<lang>/]year-in-search/<year>/[<cc>/]
         (trends.google.com/trends/yis/... redirects there) — server HTML.

  EXPLORE (need an NID cookie + an IP that is not captcha'd)
    GET  /trends/api/explore?hl&tz&req={comparisonItem, category, property}
           -> widgets, each with a `request` and a `token` bound to it
    GET  /trends/api/widgetdata/multiline|multirange|comparedgeo|relatedsearches
           ?hl&tz&req=<widget request>&token=<widget token>
    GET  /trends/api/autocomplete/<query>?hl
    GET  /trends/api/explore/pickers/geo|category?hl
    Every body starts with the `)]}'` anti-XSSI prefix. The FIRST call of a
    cookie-less session answers 429 and sets NID; the retry then passes. A
    429 on a session that already holds NID is Google's /sorry captcha for
    that IP — the session moves to a fresh residential exit.

Escalation per call (config.GOOGLE_TRENDS_*): the thread's curl session ->
a fresh exit after every block -> after GOOGLE_TRENDS_MAX_ATTEMPTS blocked
attempts, one headful patchright Chrome on a fresh exit replays the call as
an in-page fetch from trends.google.com.

Failure taxonomy (scraper_errors, mapped to HTTP by route_glue):
  TrendsUpstreamError  transport failure / 5xx / unparseable body  -> 502
  TrendsBlocked        403 / 429 / captcha                         -> 502
  TrendsBadRequest     Google rejected the parameters (HTTP 400)   -> 400
  TrendsNotFound       unknown geo / year / no data                -> 404
"""
import json
import os
import sys
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError  # noqa: E402

SITE = "https://trends.google.com"
YIS_SITE = "https://trends.withgoogle.com"
BATCH_PATH = "/_/TrendsUi/data/batchexecute"
IMPERSONATE = "chrome"
TIMEOUT = 30
PAGE_TIMEOUT = 45
XSSI_PREFIX = ")]}'"
FANOUT_WORKERS = 4

PAGE_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "upgrade-insecure-requests": "1",
}
API_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-US,en;q=0.9",
    "referer": f"{SITE}/trends/explore",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}
BATCH_HEADERS = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
    "origin": SITE,
    "referer": f"{SITE}/trending",
    "x-same-domain": "1",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}


class TrendsUpstreamError(UpstreamError):
    """Transport failure, 5xx or an unparseable body — retryable."""


class TrendsBlocked(TrendsUpstreamError, Blocked):
    """403 / 429 / captcha — retryable on a fresh exit."""


class TrendsBadRequest(BadRequest):
    """Google rejected the parameters — never retried."""


class TrendsNotFound(NotFound):
    """Unknown geo / year, or no data for the request — never retried."""


def dump_debug(name, text):
    """Write a raw body to $GOOGLE_TRENDS_DEBUG_DIR/<name>.txt."""
    dbg = os.environ.get("GOOGLE_TRENDS_DEBUG_DIR", "")
    if dbg and text:
        try:
            os.makedirs(dbg, exist_ok=True)
            with open(os.path.join(dbg, name + ".txt"), "w") as f:
                f.write(text if isinstance(text, str) else repr(text))
        except OSError:
            pass


# ---- curl sessions ---------------------------------------------------------------------------
# One curl session per worker thread and kind (a curl handle must not be
# shared across threads):
#   "open"     direct (or GOOGLE_TRENDS_OPEN_PROXY_COUNTRY); after a block the
#              thread moves to the fallback exit for GOOGLE_TRENDS_DIRECT_COOLDOWN
#   "explore"  one sticky residential exit, rotated after
#              GOOGLE_TRENDS_EXPLORE_CALLS_PER_EXIT calls or on any block; the
#              session keeps the NID cookie the explore API needs
_local = threading.local()


def _sessions():
    store = getattr(_local, "sessions", None)
    if store is None:
        store = _local.sessions = {}
    return store


def _new_session(proxy):
    from curl_cffi import requests as curl_requests
    sess = curl_requests.Session(impersonate=IMPERSONATE)
    if proxy:
        sess.proxies = {"http": proxy, "https": proxy}
    return sess


def _session(kind):
    store = _sessions()
    entry = store.get(kind)
    if kind == "open":
        via_fallback = getattr(_local, "open_fallback_until", 0) > time.time()
        if entry is not None and entry["fallback"] != via_fallback:
            _drop_session(kind)
            entry = None
        if entry is None:
            proxy = config.google_trends_open_proxy() or (
                config.google_trends_fallback_proxy() if via_fallback else None)
            entry = store[kind] = {"session": _new_session(proxy), "used": 0, "fallback": via_fallback}
    else:
        budget = max(1, int(config.GOOGLE_TRENDS_EXPLORE_CALLS_PER_EXIT or 1))
        if entry is not None and entry["used"] >= budget:
            _drop_session(kind)
            entry = None
        if entry is None:
            entry = store[kind] = {"session": _new_session(config.google_trends_explore_proxy()), "used": 0,
                                   "fallback": False}
    entry["used"] += 1
    return entry["session"]


def _drop_session(kind):
    entry = _sessions().pop(kind, None)
    if entry is not None:
        try:
            entry["session"].close()
        except Exception:
            pass


def _on_blocked(kind):
    """The current exit was refused: open sessions move to the fallback exit
    for a while, explore sessions to a fresh sticky exit."""
    if kind == "open" and config.google_trends_fallback_proxy() is not None:
        _local.open_fallback_until = time.time() + config.GOOGLE_TRENDS_DIRECT_COOLDOWN
    _drop_session(kind)


# ---- classification --------------------------------------------------------------------------

def _is_sorry(status, url, text):
    return "/sorry/" in (url or "") or (status in (200, 429) and "unusual traffic" in (text or "")[:8000])


def _classify(status, url, text, label):
    """Raise the failure a response represents, or return its text."""
    if status in (403, 429) or _is_sorry(status, url, text):
        dump_debug(f"blocked_{label}", text)
        raise TrendsBlocked(f"HTTP {status} on {label}")
    if status == 400:
        raise TrendsBadRequest(f"{label}: Google Trends rejected the request")
    if status == 404:
        raise TrendsNotFound(f"{label}: not found")
    if status != 200:
        # 401 = the widget token did not match its request (re-explore);
        # 5xx = transient upstream failure
        raise TrendsUpstreamError(f"HTTP {status} on {label}")
    return text


def _curl(method, url, kind, label, headers, timeout, body=None):
    sess = _session(kind)
    try:
        resp = sess.request(method, url, headers=headers, data=body, timeout=timeout, allow_redirects=True)
    except Exception as e:
        raise TrendsUpstreamError(f"{label}: request failed: {type(e).__name__}: {e}")
    status, text, final = resp.status_code, resp.text or "", str(resp.url or url)
    if kind == "explore" and status == 429 and not _is_sorry(status, final, text) and not getattr(sess, "_gt_primed", False):
        # A cookie-less session: this 429 SET the NID cookie; the retry passes.
        sess._gt_primed = True
        try:
            resp = sess.request(method, url, headers=headers, data=body, timeout=timeout, allow_redirects=True)
        except Exception as e:
            raise TrendsUpstreamError(f"{label}: request failed: {type(e).__name__}: {e}")
        status, text, final = resp.status_code, resp.text or "", str(resp.url or url)
    sess._gt_primed = True
    return _classify(status, final, text, label)


# ---- browser fallback ------------------------------------------------------------------------

class _Browser:
    """One headful patchright Chrome parked on trends.google.com (fresh
    fallback exit), opened on demand, reused while busy and closed after
    GOOGLE_TRENDS_BROWSER_IDLE_SECONDS. Calls run as in-page fetches, so
    they carry the page's own cookies and TLS."""

    HOME = f"{SITE}/trends/explore?q=google&hl=en-US"

    def __init__(self):
        self.lock = threading.Lock()
        self.driver = None
        self.last_used = 0.0
        self.closer = None

    def _open(self):
        from chrome_manager import create_scope
        from patchright_driver import PatchrightDriver
        with create_scope():
            driver = PatchrightDriver(proxy_url=config.google_trends_fallback_proxy(), headless=False)
        try:
            driver.nav(self.HOME, mode="blocked", challenge_markers=("unusual traffic",))
        except Exception:
            driver.close()
            raise
        return driver

    def _drop(self):
        if self.driver is not None:
            try:
                self.driver.close()
            except Exception:
                pass
        self.driver = None

    def _close_when_idle(self):
        while True:
            time.sleep(30)
            with self.lock:
                if self.driver is None or time.time() - self.last_used > config.GOOGLE_TRENDS_BROWSER_IDLE_SECONDS:
                    self._drop()
                    self.closer = None
                    return

    def fetch(self, method, url, headers, body, label):
        from patchright_driver import _FETCH_JS, FETCH_TIMEOUT_MS
        headers = {k: v for k, v in (headers or {}).items()
                   if not k.startswith(("sec-", "origin", "referer", "accept-language", "upgrade-"))}
        with self.lock:
            if self.driver is None:
                self.driver = self._open()
            if self.closer is None:
                self.closer = threading.Thread(target=self._close_when_idle, daemon=True)
                self.closer.start()
            self.last_used = time.time()
            try:
                res = self.driver.call(lambda page: page.evaluate(_FETCH_JS, {
                    "url": url, "method": method, "headers": headers, "body": body,
                    "referrer": self.HOME, "timeoutMs": FETCH_TIMEOUT_MS}))
            except Exception:
                self._drop()
                raise
            status = int((res or {}).get("status") or 0)
            if status in (403, 429):
                self._drop()   # next fallback call starts on a fresh exit
        if not res or (not status and res.get("error")):
            raise TrendsUpstreamError(f"browser fetch failed on {label}: {(res or {}).get('error')}")
        return _classify(status, res.get("url") or "", res.get("body") or "", label)


_browser = _Browser()


# ---- requests with the escalation ladder -----------------------------------------------------

def request(method, url, *, kind, label, headers, timeout=TIMEOUT, body=None, browser=True):
    """One call -> response text. Every block moves the thread to a fresh
    exit; after GOOGLE_TRENDS_MAX_ATTEMPTS blocked attempts the headful
    browser replays it (when `browser`)."""
    attempts = max(1, int(config.GOOGLE_TRENDS_MAX_ATTEMPTS))
    last, blocked_only = None, True
    for attempt in range(1, attempts + 1):
        try:
            return _curl(method, url, kind, label, headers, timeout, body)
        except (TrendsBadRequest, TrendsNotFound):
            raise
        except TrendsBlocked as e:
            last = e
            _on_blocked(kind)
        except TrendsUpstreamError as e:
            last, blocked_only = e, False
            _drop_session(kind)
        if attempt < attempts:
            time.sleep(min(config.RETRY_BACKOFF * attempt * 0.5, 3))
    if browser and blocked_only and config.GOOGLE_TRENDS_BROWSER_FALLBACK:
        print(f"google-trends: every curl attempt on {label} was blocked; replaying it in a browser")
        return _browser.fetch(method, url, headers, body, label)
    raise last


def strip_xssi(text):
    """Drop Google's `)]}'` anti-XSSI prefix (and the comma/newline after it)."""
    text = (text or "").lstrip()
    if text.startswith(XSSI_PREFIX):
        text = text[len(XSSI_PREFIX):].lstrip(",\n\r ")
    return text


def parse_json(text, label):
    try:
        return json.loads(strip_xssi(text))
    except ValueError:
        dump_debug(f"badjson_{label}", text)
        raise TrendsUpstreamError(f"{label}: could not parse the JSON body")


def api(path, params, label):
    """GET one /trends/api/* endpoint on an explore session -> parsed JSON."""
    url = f"{SITE}{path}?{urlencode(params)}"
    return parse_json(request("GET", url, kind="explore", label=label, headers=API_HEADERS), label)


def page(url, label):
    """GET one server-rendered page (open session) -> (html, final url)."""
    holder = {}

    def once():
        sess = _session("open")
        try:
            resp = sess.get(url, headers=PAGE_HEADERS, timeout=PAGE_TIMEOUT, allow_redirects=True)
        except Exception as e:
            raise TrendsUpstreamError(f"{label}: request failed: {type(e).__name__}: {e}")
        holder["url"] = str(resp.url or url)
        return _classify(resp.status_code, holder["url"], resp.text or "", label)

    attempts = max(1, int(config.GOOGLE_TRENDS_MAX_ATTEMPTS))
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return once(), holder.get("url") or url
        except (TrendsBadRequest, TrendsNotFound):
            raise
        except TrendsBlocked as e:
            last = e
            _on_blocked("open")
        except TrendsUpstreamError as e:
            last = e
            _drop_session("open")
        if attempt < attempts:
            time.sleep(min(config.RETRY_BACKOFF * attempt * 0.5, 3))
    raise last


# ---- batchexecute ----------------------------------------------------------------------------

def decode_batch(text, label="batchexecute"):
    """A batchexecute body -> {tag: (rpcid, data | None, error code | None)}.
    Handles the plain `)]}'` JSON form and the `rt=c` chunked form."""
    body = strip_xssi(text)
    chunks = []
    try:
        chunks.append(json.loads(body))
    except ValueError:
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("[["):
                try:
                    chunks.append(json.loads(line))
                except ValueError:
                    continue
    out = {}
    for chunk in chunks:
        for item in chunk if isinstance(chunk, list) else []:
            if not (isinstance(item, list) and len(item) >= 3 and item[0] == "wrb.fr"):
                continue
            rpcid, payload = item[1], item[2]
            tag = str(item[6]) if len(item) > 6 and item[6] is not None else "generic"
            code = item[5][0] if len(item) > 5 and isinstance(item[5], list) and item[5] else None
            data = None
            if isinstance(payload, str):
                try:
                    data = json.loads(payload)
                except ValueError:
                    dump_debug(f"badjson_{label}_{rpcid}", payload)
                    code = code or "unparseable"
            out[tag] = (rpcid, data, code)
    if not out:
        dump_debug(f"nodata_{label}", text)
        raise TrendsUpstreamError(f"{label}: no rpc payload in the response")
    return out


def batch(calls, *, language="en-US", label=None):
    """Run several rpcs in ONE POST: [(rpcid, args), …] -> [data | None, …]
    aligned with `calls`. An rpc answered with an error code (or null) yields
    None — the caller decides whether that means "not found"."""
    if not calls:
        return []
    label = label or "+".join(dict.fromkeys(c[0] for c in calls))
    f_req = [[rpcid, json.dumps(args, separators=(",", ":"), ensure_ascii=False), None, str(i + 1)]
             for i, (rpcid, args) in enumerate(calls)]
    params = {"rpcids": ",".join(dict.fromkeys(c[0] for c in calls)), "source-path": "/trending",
              "hl": language or "en-US"}
    body = urlencode({"f.req": json.dumps([f_req], separators=(",", ":"), ensure_ascii=False)})
    text = request("POST", f"{SITE}{BATCH_PATH}?{urlencode(params)}", kind="open", label=label,
                   headers=BATCH_HEADERS, timeout=PAGE_TIMEOUT, body=body)
    replies = decode_batch(text, label)
    out = []
    for i, (rpcid, _args) in enumerate(calls):
        reply = replies.get(str(i + 1))
        if reply is None and len(calls) == 1:
            reply = next(iter(replies.values()))
        out.append(reply[1] if reply and reply[2] is None else None)
    return out


# ---- memo + fan-out --------------------------------------------------------------------------
# Reference datasets (geo / category pickers, trending geos, the 7-day
# trending list a news lookup searches) are shared by many requests; the memo
# keeps one fetch per process for a while, the response cache shares it
# across pods.
_memo = OrderedDict()
_memo_lock = threading.Lock()
MEMO_MAX = 128


def memoized(key, fn, ttl):
    now = time.monotonic()
    with _memo_lock:
        hit = _memo.get(key)
        if hit and now - hit[0] < ttl:
            _memo.move_to_end(key)
            return hit[1]
    value = fn()
    with _memo_lock:
        _memo[key] = (now, value)
        _memo.move_to_end(key)
        while len(_memo) > MEMO_MAX:
            _memo.popitem(last=False)
    return value


def cached_call(namespace, data, fn, ttl):
    """fn(**data) through the scrapers service's response cache (cache.py);
    run standalone (no cache module) the call is simply made."""
    try:
        from cache import cached_call as service_cached_call
    except ImportError:
        return fn(**data)
    return service_cached_call(namespace, data, fn, ttl)


def dataset(name, params, build, ttl):
    """A whole reference dataset: in-process memo, then the response cache
    (shared across pods), then build()."""
    seconds = ttl.total_seconds() if hasattr(ttl, "total_seconds") else float(ttl)
    key = (name, json.dumps(params, sort_keys=True))
    return memoized(key, lambda: cached_call(f"google-trends.dataset.{name}", params, lambda **_: build(), ttl),
                    ttl=min(seconds, 3600))


def run_parallel(fns, workers=FANOUT_WORKERS):
    """Run zero-arg callables concurrently, results in order; exceptions propagate."""
    if not fns:
        return []
    if len(fns) == 1:
        return [fns[0]()]
    with ThreadPoolExecutor(max_workers=min(workers, len(fns))) as pool:
        return list(pool.map(lambda f: f(), fns))
