import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import check_stock  # noqa: E402
from check_stock import keepalive, parse_stock, run  # noqa: E402

OTHER = '<div class="p">WPVAN03_01 Vanilla Whey <span>Sold Out</span></div>'


# --- reading the page -------------------------------------------------------

def test_in_stock_ignores_other_products():
    html = OTHER + '<div>SKU: WPCCP03_01</div><span>In Stock</span><button>Add to Cart</button>' + OTHER
    assert parse_stock(html) == "in"


def test_out_of_stock():
    html = '<div>SKU: WPCCP03_01</div><span>Out of Stock</span>' + OTHER
    assert parse_stock(html) == "out"


def test_out_of_stock_wording():
    assert parse_stock("<p>WPCCP03_01</p><b>Sold Out</b>") == "out"
    assert parse_stock("<p>WPCCP03_01</p><b>Notify Me</b>") == "out"
    assert parse_stock("<p>WPCCP03_01</p><b>Not in stock</b>") == "out"
    assert parse_stock("<p>WPCCP03_01</p><b>Currently unavailable</b>") == "out"


def test_disabled_cart_button_after_sold_out_label():
    html = '<p>WPCCP03_01</p><b>Sold Out</b><button disabled>Add to Cart</button>'
    assert parse_stock(html) == "out"


def test_json_data():
    assert parse_stock('{"sku":"WPCCP03_01","available":1,"inventory_quantity":12}') == "in"
    assert parse_stock('{"sku":"WPCCP03_01","available":0,"inventory_quantity":0}') == "out"


def test_unknown_when_code_missing():
    assert parse_stock("<html><body>Please select your pincode</body></html>") == "unknown"
    assert parse_stock(OTHER) == "unknown"


# --- deciding when to alert -------------------------------------------------

class FakeNotify:
    def __init__(self, delivered=True):
        self.delivered = delivered
        self.sent = []

    def __call__(self, text):
        self.sent.append(text)
        return self.delivered


def use_notify(monkeypatch, delivered=True):
    fake = FakeNotify(delivered)
    monkeypatch.setattr(check_stock, "notify", fake)
    return fake


def test_restock_alerts_once(monkeypatch):
    sent = use_notify(monkeypatch).sent
    state = {"status": "out", "failures": 0}
    for status in ["out", "in", "in", "in"]:
        state = run(state, status)
    assert len(sent) == 1 and "Back in stock" in sent[0]
    assert state["status"] == "in"


def test_second_restock_alerts_again(monkeypatch):
    sent = use_notify(monkeypatch).sent
    state = {"status": "out", "failures": 0}
    for status in ["in", "out", "in"]:
        state = run(state, status)
    assert len(sent) == 2


def test_failed_delivery_is_retried(monkeypatch):
    fake = use_notify(monkeypatch, delivered=False)
    state = run({"status": "out", "failures": 0}, "in")
    assert state["status"] == "out"  # not marked, so next run retries
    fake.delivered = True
    state = run(state, "in")
    assert state["status"] == "in" and len(fake.sent) == 2


def test_unknown_never_alerts_restock_and_warns_once(monkeypatch):
    sent = use_notify(monkeypatch).sent
    state = {"status": "out", "failures": 0}
    for _ in range(20):
        state = run(state, "unknown")
    assert state["status"] == "out"
    assert len(sent) == 1 and "couldn't read" in sent[0]
    state = run(state, "out")
    assert len(sent) == 2 and "again" in sent[1]
    assert state["failures"] == 0


def test_keepalive_monthly():
    state = {}
    keepalive(state, datetime.date(2026, 1, 1))
    assert state["keepalive"] == "2026-01-01"
    keepalive(state, datetime.date(2026, 1, 20))
    assert state["keepalive"] == "2026-01-01"
    keepalive(state, datetime.date(2026, 2, 5))
    assert state["keepalive"] == "2026-02-05"


# --- not getting blocked ----------------------------------------------------

from check_stock import Blocked, in_backoff, looks_blocked, read  # noqa: E402

NOW = datetime.datetime(2026, 10, 23, 12, 0, tzinfo=datetime.timezone.utc)


def test_captcha_page_is_blocked():
    import pytest
    with pytest.raises(Blocked):
        read("<title>Just a moment...</title><div>Checking your browser</div>")


def test_captcha_word_on_real_product_page_is_ignored():
    html = '<script src="recaptcha.js"></script><p>WPCCP03_01</p><b>In Stock</b>'
    assert looks_blocked(html) and read(html) == "in"


def test_backoff_grows_and_resets(monkeypatch):
    use_notify(monkeypatch)
    state = {"status": "out", "failures": 0}
    waits = []
    for _ in range(6):
        state = run(state, "blocked", now=NOW)
        until = datetime.datetime.fromisoformat(state["backoff_until"])
        waits.append(int((until - NOW).total_seconds() // 60))
    assert waits == [15, 30, 60, 120, 240, 240]
    assert in_backoff(state, NOW) and not in_backoff(state, NOW + datetime.timedelta(hours=5))
    state = run(state, "out", now=NOW)
    assert "blocks" not in state and "backoff_until" not in state


def test_retry_after_is_respected(monkeypatch):
    use_notify(monkeypatch)
    state = run({"status": "out"}, "blocked", now=NOW, retry_after=3600)
    assert datetime.datetime.fromisoformat(state["backoff_until"]) - NOW == datetime.timedelta(hours=1)


def test_block_warning_says_blocked(monkeypatch):
    sent = use_notify(monkeypatch).sent
    state = {"status": "out", "failures": 7}
    run(state, "blocked", now=NOW)
    assert "blocked" in sent[0]
