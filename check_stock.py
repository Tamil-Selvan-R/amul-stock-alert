"""Check Amul shop stock for one product and send an alert on restock.

Environment variables (all optional except at least one alert channel):
  PRODUCT_URL         product page to check (default: chocolate whey 30 sachets)
  PRODUCT_SKU         product code to look for (default: WPCCP03_01)
  PINCODE             delivery pincode; Amul shows stock per area
  WHATSAPP_PHONE      your number with country code, e.g. +919876543210
  CALLMEBOT_API_KEY   key from CallMeBot
  TELEGRAM_BOT_TOKEN  optional backup channel: token from @BotFather
  TELEGRAM_CHAT_ID    optional backup channel: your chat id
  STATE_FILE          where the last result is remembered (default: state.json)
  USE_BROWSER         "auto" (default), "always" or "never"
  JITTER_SECONDS      wait a random 0..N seconds before checking (default 0)
  TEST_MESSAGE=1      only send a test message and exit
  DEBUG_DIR           if set, save what each method saw into this folder
"""

import datetime
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PRODUCT_URL = os.environ.get(
    "PRODUCT_URL",
    "https://shop.amul.com/en/product/amul-chocolate-whey-protein-34-g-or-pack-of-30-sachets",
)
PRODUCT_SKU = os.environ.get("PRODUCT_SKU", "WPCCP03_01")
PRODUCT_NAME = os.environ.get("PRODUCT_NAME", "Amul Chocolate Whey Protein (30 sachets)")
PINCODE = os.environ.get("PINCODE", "").strip()
STATE_FILE = os.environ.get("STATE_FILE", "state.json")
USE_BROWSER = os.environ.get("USE_BROWSER", "auto").lower()
FAILURE_ALERT_AFTER = 8  # 8 checks x 15 min = 2 hours of failures
KEEPALIVE_DAYS = 30  # GitHub pauses schedules after 60 days without commits
WINDOW = 1500  # characters after the product code to inspect
JITTER_SECONDS = int(os.environ.get("JITTER_SECONDS", "0"))
BACKOFF_MINUTES = [15, 30, 60, 120, 240]  # wait longer after each block in a row
DEBUG_DIR = os.environ.get("DEBUG_DIR", "").strip()

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
}

# Browser requests the bot doesn't need.
SKIP_TYPES = {"image", "media", "font"}

# Signs that the shop refused us instead of showing the product.
BLOCK_CODES = {403, 429, 503}
BLOCK_MARKERS = [
    r"captcha",
    r"access\s*denied",
    r"just\s*a\s*moment",
    r"attention\s*required",
    r"unusual\s*traffic",
    r"request\s*blocked",
]

OUT_PATTERNS = [
    r"out\s*of\s*stock",
    r"sold\s*out",
    r"notify\s*me",
    r"not\s*(in\s*stock|available)",
    r"unavailable",
    r'"available"\s*:\s*(0|false)',
    r'"inventory_quantity"\s*:\s*0\b',
]
IN_PATTERNS = [
    r"in\s*stock",
    r"add\s*to\s*cart",
    r'"available"\s*:\s*(1|true)',
    r'"inventory_quantity"\s*:\s*[1-9]',
]


class Blocked(Exception):
    def __init__(self, reason, retry_after=0):
        super().__init__(reason)
        self.retry_after = retry_after  # seconds the shop asked us to wait


def _retry_after(value):
    try:
        return int(value or 0)
    except ValueError:
        return 0


def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code in BLOCK_CODES:
            raise Blocked(f"HTTP {exc.code}", _retry_after(exc.headers.get("Retry-After")))
        raise


def looks_blocked(html):
    """True if the page looks like a block or captcha page.

    Only used when the product code wasn't found: a normal product page may
    mention "captcha" in a login form, and that must not hide a restock.
    """
    return any(re.search(p, html, re.I) for p in BLOCK_MARKERS)


def fetch_with_browser(url, pincode=PINCODE):
    """Load the page in headless Chromium, enter the pincode if asked, and
    return the rendered page plus any JSON the page loaded in the background."""
    from playwright.sync_api import sync_playwright

    captured = []
    calls = []  # debug only: which data addresses the page used

    def on_response(resp):
        is_json = "json" in resp.headers.get("content-type", "")
        if is_json:
            try:
                captured.append(resp.text())
            except Exception:
                pass
        if DEBUG_DIR and "/api/" in resp.url:
            req = resp.request
            headers = {k: (v if k.lower() not in ("cookie", "authorization") else "<hidden>")
                       for k, v in req.headers.items()}
            try:
                body = resp.text()[:600] if is_json else ""
            except Exception:
                body = ""
            calls.append({"method": req.method, "url": resp.url, "status": resp.status,
                          "request_headers": headers, "post_data": (req.post_data or "")[:600],
                          "response_start": body})

    counts = {"loaded": 0, "skipped": 0}
    shop = urllib.parse.urlparse(url).hostname or ""
    shop_site = ".".join(shop.split(".")[-2:])  # shop.amul.com -> amul.com

    def trim(route):
        # The bot only needs the shop's page code and data. Skipping pictures,
        # fonts and other sites' trackers cuts the load on Amul a lot.
        req = route.request
        host = urllib.parse.urlparse(req.url).hostname or ""
        if req.resource_type in SKIP_TYPES or not (host == shop or host.endswith(shop_site)):
            counts["skipped"] += 1
            route.abort()
        else:
            counts["loaded"] += 1
            route.continue_()

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        try:
            page = browser.new_page(user_agent=USER_AGENT, locale="en-IN")
            page.route("**/*", trim)
            page.on("response", on_response)
            resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
            if resp and resp.status in BLOCK_CODES:
                raise Blocked(f"HTTP {resp.status}", _retry_after(resp.headers.get("retry-after")))
            page.wait_for_timeout(3000)
            if pincode:
                _enter_pincode(page, pincode)
            page.wait_for_load_state("networkidle", timeout=20000)
            page.wait_for_timeout(2000)
            if DEBUG_DIR:
                cookies = [{"name": c["name"], "domain": c["domain"]} for c in page.context.cookies()]
                save_debug("network.json", json.dumps(
                    {"requests": counts, "calls": calls, "cookies": cookies}, indent=2))
                _try_feed_directly(calls)
            return page.content() + "\n" + "\n".join(captured)
        finally:
            browser.close()


def _try_feed_directly(calls):
    """Debug only: ask the stock feed once, without the page's signed `tid`
    header, to learn whether a plain request is accepted."""
    feed = next((c["url"] for c in calls if "fields[available]" in c["url"]
                 and "filters[0][field]=_id" in c["url"]), None)
    if not feed:
        save_debug("direct.txt", "no stock feed call seen\n")
        return
    req = urllib.request.Request(feed, headers={**HEADERS, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            result = f"HTTP {resp.status} has_available={'\"available\"' in body}\n{body[:300]}\n"
    except urllib.error.HTTPError as exc:
        result = f"HTTP {exc.code}\n{exc.read()[:300].decode('utf-8', 'replace')}\n"
    except Exception as exc:
        result = f"error {type(exc).__name__}: {exc}\n"
    save_debug("direct.txt", result)


def _enter_pincode(page, pincode):
    box = page.locator('input[placeholder*="incode" i], input[name*="pincode" i]').first
    if not box.count() or not box.is_visible():
        return
    box.click()
    box.press_sequentially(pincode, delay=120)  # type like a person so the list appears
    # Amul lists matches under the box as links; the line above them
    # ("Showing result for ...") is not clickable, so pick the link itself.
    choice = page.locator("#automatic a, .searchitem-name").filter(has_text=pincode).first
    try:
        choice.wait_for(state="visible", timeout=10000)
        choice.click()
    except Exception:
        exact = page.get_by_text(pincode, exact=True).first
        if exact.count() and exact.is_visible():
            exact.click()
        else:
            box.press("Enter")
    page.wait_for_timeout(4000)


def _first_match(patterns, text):
    """Position of the earliest match of any pattern, or None."""
    positions = [m.start() for p in patterns for m in re.finditer(p, text, re.I)]
    return min(positions) if positions else None


def parse_stock(html, sku=PRODUCT_SKU):
    """Return "in", "out" or "unknown" for the product with this code.

    Only text right after the product code counts, so "Sold Out" badges on
    other products listed on the same page are ignored.
    """
    verdicts = []
    for m in re.finditer(re.escape(sku), html, re.I):
        window = html[m.end(): m.end() + WINDOW]
        out_pos = _first_match(OUT_PATTERNS, window)
        in_pos = _first_match(IN_PATTERNS, window)
        if out_pos is None and in_pos is None:
            continue
        if in_pos is None or (out_pos is not None and out_pos < in_pos):
            verdicts.append("out")
        else:
            verdicts.append("in")
    if not verdicts:
        return "unknown"
    # Out of stock wins any disagreement: a missed alert is retried in 15
    # minutes, a false alarm sends you to the shop for nothing.
    return "out" if "out" in verdicts else "in"


def save_debug(name, text):
    """Keep a copy of what the bot saw, to tune it against the real page."""
    if not DEBUG_DIR:
        return
    os.makedirs(DEBUG_DIR, exist_ok=True)
    with open(os.path.join(DEBUG_DIR, name), "w") as f:
        f.write(text[:400_000])


def read(html, name="page"):
    save_debug(name + ".html", html)
    status = parse_stock(html)
    save_debug(name + "-result.txt", f"status={status} sku_found={PRODUCT_SKU.lower() in html.lower()} length={len(html)}\n")
    if status == "unknown" and looks_blocked(html):
        raise Blocked("block or captcha page")
    return status


def check():
    """Return (status, method, retry_after).

    status is "in", "out", "unknown" (couldn't tell) or "blocked" (the shop
    refused us). Tries a plain download first, then a real browser.
    """
    status, retry_after = "unknown", 0
    if USE_BROWSER != "always":
        try:
            status = read(fetch(PRODUCT_URL), "plain")
        except Blocked as exc:
            print("Plain fetch blocked:", exc)
            status, retry_after = "blocked", exc.retry_after
        except Exception as exc:  # network error, timeout
            print("Plain fetch failed:", exc)
        # A 429 means "too many requests": a browser from the same address
        # would only make it worse, so stop here.
        if (status in ("in", "out") and not DEBUG_DIR) or retry_after or USE_BROWSER == "never":
            return status, "plain", retry_after
    try:
        status = read(fetch_with_browser(PRODUCT_URL), "browser")
    except Blocked as exc:
        print("Browser blocked:", exc)
        status, retry_after = "blocked", exc.retry_after
    except Exception as exc:
        print("Browser check failed:", exc)
        status = "unknown"
    return status, "browser", retry_after


def send_whatsapp(text):
    phone = os.environ.get("WHATSAPP_PHONE", "").strip()
    key = os.environ.get("CALLMEBOT_API_KEY", "").strip()
    if not phone or not key:
        return None
    query = urllib.parse.urlencode({"phone": phone, "text": text, "apikey": key})
    try:
        body = fetch("https://api.callmebot.com/whatsapp.php?" + query)
    except Exception as exc:
        print("WhatsApp failed:", exc)
        return False
    ok = "error" not in body.lower()
    print("WhatsApp:", "sent" if ok else body[:300])
    return ok


def send_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return None
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    try:
        with urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=30
        ) as resp:
            ok = json.load(resp).get("ok", False)
    except urllib.error.HTTPError as exc:
        # Telegram explains the problem, e.g. "Unauthorized" or "chat not found".
        try:
            reason = json.load(exc).get("description", "")
        except Exception:
            reason = ""
        print(f"Telegram failed: HTTP {exc.code} {reason}")
        save_debug("telegram.txt", f"HTTP {exc.code}: {reason}\n")
        return False
    except Exception as exc:
        print("Telegram failed:", exc)
        save_debug("telegram.txt", f"error: {type(exc).__name__}\n")
        return False
    print("Telegram:", "sent" if ok else "failed")
    save_debug("telegram.txt", "sent\n" if ok else "not sent\n")
    return ok


def describe_secrets():
    """Which alert settings exist and look well formed. Never the values."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    lines = [
        f"TELEGRAM_BOT_TOKEN set={bool(token)} looks_valid={bool(re.fullmatch(r'[0-9]{6,12}:[A-Za-z0-9_-]{30,}', token))}",
        f"TELEGRAM_CHAT_ID set={bool(chat_id)} looks_valid={bool(re.fullmatch(r'-?[0-9]{5,16}', chat_id))}",
        f"PINCODE set={bool(PINCODE)} looks_valid={bool(re.fullmatch(r'[0-9]{6}', PINCODE))}",
        f"WHATSAPP_PHONE set={bool(os.environ.get('WHATSAPP_PHONE', '').strip())}",
        f"CALLMEBOT_API_KEY set={bool(os.environ.get('CALLMEBOT_API_KEY', '').strip())}",
    ]
    return "\n".join(lines) + "\n"


def notify(text):
    """Send on every configured channel. True if at least one delivered."""
    results = [send_whatsapp(text), send_telegram(text)]
    if all(r is None for r in results):
        print("No alert channel configured, message was:", text)
        return False
    return any(results)


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"status": "out", "failures": 0}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


def keepalive(state, today=None):
    """Touch the state about once a month so GitHub sees repo activity."""
    today = today or datetime.date.today()
    last = state.get("keepalive")
    if not last or (today - datetime.date.fromisoformat(last)).days >= KEEPALIVE_DAYS:
        state["keepalive"] = today.isoformat()


def in_backoff(state, now):
    until = state.get("backoff_until")
    return bool(until) and now < datetime.datetime.fromisoformat(until)


def run(state, status, now=None, retry_after=0):
    """Update state for one check result and send any alerts. Returns state."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if status in ("unknown", "blocked"):
        state["failures"] = state.get("failures", 0) + 1
        if status == "blocked":
            state["blocks"] = state.get("blocks", 0) + 1
            step = BACKOFF_MINUTES[min(state["blocks"], len(BACKOFF_MINUTES)) - 1]
            wait = max(step * 60, retry_after)
            state["backoff_until"] = (now + datetime.timedelta(seconds=wait)).isoformat()
            print(f"Blocked {state['blocks']} time(s) in a row; pausing {wait // 60} min.")
        if state["failures"] == FAILURE_ALERT_AFTER:
            reason = "is being blocked by" if status == "blocked" else "couldn't read"
            notify(
                f"⚠️ Amul stock bot {reason} the product page for a while. "
                "It keeps trying; check the Actions tab on GitHub if this continues."
            )
        return state

    if state.get("failures", 0) >= FAILURE_ALERT_AFTER:
        notify("✅ Amul stock bot can read the page again.")
    state["failures"] = 0
    state.pop("blocks", None)
    state.pop("backoff_until", None)

    if status == "in" and state.get("status") != "in":
        delivered = notify(f"🟢 Back in stock! {PRODUCT_NAME}\n{PRODUCT_URL}")
        if not delivered:
            # Leave the status as "out" so the next run tries the alert again.
            print("Alert not delivered; will retry next run.")
            return state
    state["status"] = status
    return state


def main():
    if os.environ.get("TEST_MESSAGE") == "1":
        save_debug("secrets.txt", describe_secrets())
        ok = notify("✅ Amul stock bot is connected. You'll get a message here when it's back in stock.")
        return 0 if ok else 1

    state = load_state()
    now = datetime.datetime.now(datetime.timezone.utc)
    if in_backoff(state, now):
        print("Paused after being blocked until", state["backoff_until"], "- skipping this check.")
        return 0

    if JITTER_SECONDS:
        time.sleep(random.uniform(0, JITTER_SECONDS))  # don't hit the shop at exact times
    status, method, retry_after = check()
    print(f"Status: {status} (via {method})")
    state = run(state, status, retry_after=retry_after)
    keepalive(state)
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
