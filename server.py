#!/usr/bin/env python3
import json
import time
import datetime
import urllib.request
import urllib.error
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, urlencode

try:
    import yfinance as yf
    _yf_ok = True
except ImportError:
    _yf_ok = False

import os
PORT            = int(os.environ.get("PORT", 4173))
SHEETS_WEBHOOK  = os.environ.get("SHEETS_WEBHOOK", "")
YAHOO_BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"
YAHOO_SEARCH = "https://query1.finance.yahoo.com/v1/finance/search"

_fundamentals_cache = {}
CACHE_TTL = 300  # 5 minutes


def get_fundamentals(symbol):
    now = time.time()
    if symbol in _fundamentals_cache:
        ts, data = _fundamentals_cache[symbol]
        if now - ts < CACHE_TTL:
            return data
    if not _yf_ok:
        return {}
    try:
        info = yf.Ticker(symbol).info
        data = {
            "open": info.get("open"),
            "volume": info.get("volume"),
            "marketCap": info.get("marketCap"),
            "trailingPE": info.get("trailingPE"),
            "forwardPE": info.get("forwardPE"),
            "eps": info.get("trailingEps"),
            "beta": info.get("beta"),
            "avgVolume": info.get("averageVolume"),
            "dividendYield": info.get("dividendYield"),
            "priceToBook": info.get("priceToBook"),
            "fiftyTwoWeekHigh": info.get("fiftyTwoWeekHigh"),
            "fiftyTwoWeekLow": info.get("fiftyTwoWeekLow"),
        }
    except Exception:
        data = {}
    _fundamentals_cache[symbol] = (now, data)
    return data


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/yahoo":
            self.handle_yahoo_proxy(parsed)
            return
        if parsed.path == "/api/yahoo-search":
            self.handle_yahoo_search(parsed)
            return
        if parsed.path == "/api/fundamentals":
            self.handle_fundamentals(parsed)
            return
        super().do_GET()

    def handle_fundamentals(self, parsed):
        qs = parse_qs(parsed.query)
        symbol = (qs.get("symbol") or [""])[0]
        if not symbol:
            self.send_json_error(400, "missing symbol parameter")
            return
        try:
            data = get_fundamentals(symbol)
            body = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self.send_json_error(502, "fundamentals error: " + str(e))

    def handle_yahoo_search(self, parsed):
        qs = parse_qs(parsed.query)
        query = (qs.get("q") or [""])[0]
        if not query:
            self.send_json_error(400, "missing q parameter")
            return
        quotes_count = (qs.get("quotesCount") or ["15"])[0]
        news_count = (qs.get("newsCount") or ["0"])[0]
        url = (YAHOO_SEARCH + "?q=" + urllib.request.quote(query) +
               "&quotesCount=" + urllib.request.quote(quotes_count) +
               "&newsCount=" + urllib.request.quote(news_count))
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                body = resp.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except urllib.error.HTTPError as e:
            self.send_json_error(e.code, "upstream error: " + str(e))
        except Exception as e:
            self.send_json_error(502, "proxy error: " + str(e))

    def handle_yahoo_proxy(self, parsed):
        qs = parse_qs(parsed.query)
        symbol = (qs.get("symbol") or [""])[0]
        if not symbol:
            self.send_json_error(400, "missing symbol parameter")
            return
        range_param = (qs.get("range") or [""])[0]
        interval_param = (qs.get("interval") or [""])[0]
        url = YAHOO_BASE + urllib.request.quote(symbol)
        extra = []
        if range_param:
            extra.append("range=" + urllib.request.quote(range_param))
        if interval_param:
            extra.append("interval=" + urllib.request.quote(interval_param))
        if extra:
            url += "?" + "&".join(extra)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                body = resp.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except urllib.error.HTTPError as e:
            self.send_json_error(e.code, "upstream error: " + str(e))
        except Exception as e:
            self.send_json_error(502, "proxy error: " + str(e))

    def send_json_error(self, code, message):
        body = json.dumps({"error": message}).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/register":
            self.handle_register()
            return
        self.send_json_error(404, "not found")

    def handle_register(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            name  = str(body.get("name",  "")).strip()
            email = str(body.get("email", "")).strip().lower()
            ts    = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            print(f"[REGISTER] {ts} | {name} | {email}", flush=True)

            # Save to Google Sheets via Apps Script (GET with params — more reliable)
            if SHEETS_WEBHOOK:
                params = urlencode({"name": name, "email": email, "timestamp": ts})
                url = SHEETS_WEBHOOK + "?" + params
                urllib.request.urlopen(url, timeout=8)

            resp = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
        except Exception as ex:
            print(f"[REGISTER ERROR] {ex}", flush=True)
            self.send_json_error(500, str(ex))

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Serving on http://localhost:{PORT}")
    server.serve_forever()
