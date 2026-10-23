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
  TEST_MESSAGE=1      only send a test message and exit
"""

import datetime
import json
import os
import re
import sys
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

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

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


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_with_browser(url, pincode=PINCODE):
    """Load the page in headless Chromium, enter the pincode if asked, and
    return the rendered page plus any JSON the page loaded in the background."""
    from playwright.sync_api import sync_playwright

    captured = []

    def on_response(resp):
        if "json" in resp.headers.get("content-type", ""):
            try:
                captured.append(resp.text())
            except Exception:
                pass

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        try:
            page = browser.new_page(user_agent=USER_AGENT, locale="en-IN")
            page.on("response", on_response)
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3000)
            if pincode:
                _enter_pincode(page, pincode)
            page.wait_for_load_state("networkidle", timeout=20000)
            page.wait_for_timeout(2000)
            return page.content() + "\n" + "\n".join(captured)
        finally:
            browser.close()


def _enter_pincode(page, pincode):
    box = page.locator('input[placeholder*="incode" i], input[name*="pincode" i]').first
    if not box.count() or not box.is_visible():
        return
    box.fill(pincode)
    page.wait_for_timeout(2500)
    # Amul lists matching areas under the box; pick the first one.
    choice = page.get_by_text(pincode).first
    if choice.count() and choice.is_visible():
        choice.click()
    else:
        box.press("Enter")
    page.wait_for_timeout(3000)


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


def check():
    """Return (status, method). Tries a plain download first, then a browser."""
    status = "unknown"
    if USE_BROWSER != "always":
        try:
            status = parse_stock(fetch(PRODUCT_URL))
        except Exception as exc:  # network error, blocked, timeout
            print("Plain fetch failed:", exc)
        if status != "unknown" or USE_BROWSER == "never":
            return status, "plain"
    try:
        status = parse_stock(fetch_with_browser(PRODUCT_URL))
    except Exception as exc:
        print("Browser check failed:", exc)
    return status, "browser"


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
    except Exception as exc:
        print("Telegram failed:", exc)
        return False
    print("Telegram:", "sent" if ok else "failed")
    return ok


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


def run(state, status):
    """Update state for one check result and send any alerts. Returns state."""
    if status == "unknown":
        state["failures"] = state.get("failures", 0) + 1
        if state["failures"] == FAILURE_ALERT_AFTER:
            notify(
                "⚠️ Amul stock bot couldn't read the product page for 2 hours. "
                "It keeps trying; check the Actions tab on GitHub if this continues."
            )
        return state

    if state.get("failures", 0) >= FAILURE_ALERT_AFTER:
        notify("✅ Amul stock bot can read the page again.")
    state["failures"] = 0

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
        ok = notify("✅ Amul stock bot is connected. You'll get a message here when it's back in stock.")
        return 0 if ok else 1

    status, method = check()
    print(f"Status: {status} (via {method})")
    state = run(load_state(), status)
    keepalive(state)
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
