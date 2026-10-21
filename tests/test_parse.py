import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from check_stock import parse_stock  # noqa: E402

OTHER = '<div class="p">WPVAN03_01 Vanilla Whey <span>Sold Out</span></div>'


def test_in_stock_ignores_other_products():
    html = OTHER + '<div>SKU: WPCCP03_01</div><span>In Stock</span><button>Add to Cart</button>' + OTHER
    assert parse_stock(html) == "in"


def test_out_of_stock():
    html = '<div>SKU: WPCCP03_01</div><span>Out of Stock</span>' + OTHER
    assert parse_stock(html) == "out"


def test_sold_out_label():
    assert parse_stock('<p>WPCCP03_01</p><b>Sold Out</b>') == "out"


def test_json_data():
    assert parse_stock('{"sku":"WPCCP03_01","available":1,"inventory_quantity":12}') == "in"
    assert parse_stock('{"sku":"WPCCP03_01","available":0,"inventory_quantity":0}') == "out"


def test_unknown_when_code_missing():
    assert parse_stock("<html><body>Please select your pincode</body></html>") == "unknown"
    assert parse_stock(OTHER) == "unknown"
