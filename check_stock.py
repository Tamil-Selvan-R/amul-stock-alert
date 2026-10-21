"""Check Amul shop stock for one product and send a WhatsApp alert on restock.

Environment variables:
  PRODUCT_URL        product page to check (default: chocolate whey 30 sachets)
  PRODUCT_SKU        product code to look for on the page (default: WPCCP03_01)
  WHATSAPP_PHONE     your number with country code, e.g. +919876543210
  CALLMEBOT_API_KEY  key from CallMeBot
  STATE_FILE         where the last result is remembered (default: state.json)
  TEST_MESSAGE=1     only send a test WhatsApp message and exit
"""

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
STATE_FILE = os.environ.get("STATE_FILE", "state.json")
FAILURE_ALERT_AFTER = 8  # 8 checks x 15 min = 2 hours of failures
WINDOW = 1500  # characters around the product code to inspect

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)

OUT_PATTERNS = [
    r"out\s*of\s*stock",
    r"sold\s*out",
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


def send_whatsapp(text):
    phone = os.environ.get("WHATSAPP_PHONE", "").strip()
    key = os.environ.get("CALLMEBOT_API_KEY", "").strip()
    if not phone or not key:
        print("WhatsApp secrets missing, not sending:", text)
        return False
    query = urllib.parse.urlencode({"phone": phone, "text": text, "apikey": key})
    body = fetch("https://api.callmebot.com/whatsapp.php?" + query)
    ok = "error" not in body.lower()
    print("CallMeBot:", "sent" if ok else body[:300])
    return ok


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


def main():
    if os.environ.get("TEST_MESSAGE") == "1":
        ok = send_whatsapp("✅ Amul stock bot is connected. You'll get a message here when it's back in stock.")
        return 0 if ok else 1

    state = load_state()
    try:
        status = parse_stock(fetch(PRODUCT_URL))
    except Exception as exc:  # network error, blocked, timeout
        print("Fetch failed:", exc)
        status = "unknown"
    print("Status:", status)

    if status == "unknown":
        state["failures"] = state.get("failures", 0) + 1
        if state["failures"] == FAILURE_ALERT_AFTER:
            send_whatsapp(
                "⚠️ Amul stock bot couldn't read the product page for 2 hours. "
                "It keeps trying; check the Actions tab on GitHub if this continues."
            )
    else:
        if status == "in" and state.get("status") != "in":
            send_whatsapp("🟢 Back in stock! Amul Chocolate Whey Protein (30 sachets): " + PRODUCT_URL)
        state["status"] = status
        state["failures"] = 0

    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
