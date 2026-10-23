"""Browser mode against a fake shop that, like many modern shops, shows
nothing until a pincode is chosen and then loads stock as JSON."""

import functools
import http.server
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("playwright")

from check_stock import fetch_with_browser, parse_stock  # noqa: E402

PAGE = """<!doctype html><html><body>
<div id="modal"><input placeholder="Enter Your Pincode" id="pin">
<ul id="list"></ul></div>
<div id="product">Loading...</div>
<script>
document.getElementById('pin').addEventListener('input', e => {
  if (e.target.value.length === 6)
    document.getElementById('list').innerHTML = '<li id="opt">' + e.target.value + ' - Chennai</li>';
});
document.addEventListener('click', async e => {
  if (e.target.id !== 'opt') return;
  document.getElementById('modal').remove();
  const r = await fetch('/api/products');
  const d = await r.json();
  document.getElementById('product').innerHTML = '<span>' + d.sku + '</span> ' +
    (d.available ? '<b>In Stock</b>' : '<b>Sold Out</b>');
});
</script></body></html>"""


def serve(available):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/api"):
                body, ctype = '{"sku":"WPCCP03_01","available":%d}' % available, "application/json"
            else:
                body, ctype = PAGE, "text/html"
            self.send_response(200)
            self.send_header("content-type", ctype)
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.mark.parametrize("available,expected", [(1, "in"), (0, "out")])
def test_browser_enters_pincode_and_reads_stock(available, expected):
    server = serve(available)
    try:
        url = f"http://127.0.0.1:{server.server_port}/"
        assert parse_stock(fetch_with_browser(url, pincode="600001")) == expected
    finally:
        server.shutdown()


def test_without_pincode_page_is_unknown():
    server = serve(1)
    try:
        url = f"http://127.0.0.1:{server.server_port}/"
        assert parse_stock(fetch_with_browser(url, pincode="")) == "unknown"
    finally:
        server.shutdown()
