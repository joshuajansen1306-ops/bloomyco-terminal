#!/usr/bin/env python3
import json
import time
import datetime
import gzip
import io
import mimetypes
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
PORT           = int(os.environ.get("PORT", 4173))
SHEETS_WEBHOOK = os.environ.get("SHEETS_WEBHOOK", "")
YAHOO_BASE     = "https://query1.finance.yahoo.com/v8/finance/chart/"
YAHOO_SEARCH   = "https://query1.finance.yahoo.com/v1/finance/search"
COINGECKO_BASE = "https://api.coingecko.com/api/v3"

_fundamentals_cache = {}
CACHE_TTL = 3600
ERROR_CACHE_TTL = 45

_coingecko_cache = {}
COINGECKO_TTL = 120  # categories change slowly — cache 2 minutes

def _fetch_fundamentals_once(symbol):
    t = yf.Ticker(symbol)
    info = t.info or {}
    print(f"[FUNDAMENTALS] {symbol}: info has {len(info)} keys", flush=True)
    try:
        fi = t.fast_info
        mc = getattr(fi, "market_cap", None)
    except Exception:
        mc = None
    data = {
        "open":           info.get("open") or info.get("regularMarketOpen"),
        "volume":         info.get("volume") or info.get("regularMarketVolume"),
        "avgVolume":      info.get("averageVolume") or info.get("averageDailyVolume10Day"),
        "marketCap":      info.get("marketCap") or mc,
        "trailingPE":     info.get("trailingPE"),
        "forwardPE":      info.get("forwardPE"),
        "eps":            info.get("trailingEps"),
        "beta":           info.get("beta"),
        "dividendYield":  info.get("dividendYield") or info.get("trailingAnnualDividendYield"),
        "priceToBook":    info.get("priceToBook"),
        "fiftyTwoWeekHigh": info.get("fiftyTwoWeekHigh"),
        "fiftyTwoWeekLow":  info.get("fiftyTwoWeekLow"),
    }
    return {k: v for k, v in data.items() if v is not None}

def get_fundamentals(symbol):
    now = time.time()
    if symbol in _fundamentals_cache:
        ts, data, ok = _fundamentals_cache[symbol]
        ttl = CACHE_TTL if ok else ERROR_CACHE_TTL
        if now - ts < ttl:
            return data
    if not _yf_ok:
        print(f"[FUNDAMENTALS] {symbol}: yfinance import failed at startup", flush=True)
        return {"_debug": "yfinance not importable"}

    data, ok = {}, False
    for attempt in range(2):
        try:
            data = _fetch_fundamentals_once(symbol)
            ok = bool(data)
            if not data:
                data = {"_debug": "info returned no usable fields"}
            break
        except Exception as e:
            print(f"[FUNDAMENTALS] {symbol} attempt {attempt + 1}: {type(e).__name__}: {e}", flush=True)
            data = {"_debug": f"{type(e).__name__}: {e}"}
            if attempt == 0 and "rate" in type(e).__name__.lower():
                time.sleep(2)
                continue
            break
    _fundamentals_cache[symbol] = (now, data, ok)
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
        if parsed.path == "/api/coingecko-categories":
            self.handle_coingecko_categories()
            return
        self.serve_static()

    def serve_static(self):
        parsed = urlparse(self.path)
        path = parsed.path.lstrip("/") or "index.html"
        import os
        if not os.path.isfile(path):
            self.send_error(404, "Not Found")
            return
        mime, _ = mimetypes.guess_type(path)
        if mime is None:
            mime = "application/octet-stream"
        with open(path, "rb") as f:
            raw = f.read()
        accepts_gzip = "gzip" in self.headers.get("Accept-Encoding", "")
        if accepts_gzip:
            buf = io.BytesIO()
            with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6) as gz:
                gz.write(raw)
            body = buf.getvalue()
        else:
            body = raw
        is_html = mime == "text/html"
        cache_control = "no-store, no-cache, must-revalidate, max-age=0" if is_html else "public, max-age=86400"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", cache_control)
        if is_html:
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        if accepts_gzip:
            self.send_header("Content-Encoding", "gzip")
        self.end_headers()
        self.wfile.write(body)



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

    def handle_coingecko_categories(self):
        now = time.time()
        if "categories" in _coingecko_cache:
            ts, body = _coingecko_cache["categories"]
            if now - ts < COINGECKO_TTL:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
        url = COINGECKO_BASE + "/coins/categories"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read()
            _coingecko_cache["categories"] = (now, body)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except urllib.error.HTTPError as e:
            self.send_json_error(e.code, "coingecko error: " + str(e))
        except Exception as e:
            self.send_json_error(502, "coingecko proxy error: " + str(e))

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
        if parsed.path == "/api/feedback":
            self.handle_feedback()
            return
        if parsed.path == "/api/logout":
            self.handle_logout()
            return
        self.send_json_error(404, "not found")

    def get_client_ip(self):
        fwd = self.headers.get("X-Forwarded-For", "")
        if fwd:
            return fwd.split(",")[0].strip()
        return self.client_address[0]

    def handle_register(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            name  = str(body.get("name",  "")).strip()
            email = str(body.get("email", "")).strip().lower()
            ip    = self.get_client_ip()
            ts    = (datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d %H:%M:%S IST")
            print(f"[REGISTER] {ts} | {name} | {email} | {ip}", flush=True)

            # Save to Google Sheets via Apps Script (GET with params — more reliable)
            if SHEETS_WEBHOOK:
                params = urlencode({"name": name, "email": email, "ip": ip, "timestamp": ts})
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

    def handle_feedback(self):
        try:
            length  = int(self.headers.get("Content-Length", 0))
            body    = json.loads(self.rfile.read(length))
            message = str(body.get("message", "")).strip()
            phone   = str(body.get("phone",   "")).strip()
            email   = str(body.get("email",   "")).strip().lower()
            name    = str(body.get("name",    "")).strip()
            ip      = self.get_client_ip()
            if not message:
                self.send_json_error(400, "missing message")
                return
            ts = (datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d %H:%M:%S IST")
            print(f"[FEEDBACK] {ts} | {name} | {email} | {phone} | {ip} | {message[:120]}", flush=True)

            if SHEETS_WEBHOOK:
                params = urlencode({
                    "type": "feedback",
                    "message": message,
                    "phone": phone,
                    "email": email,
                    "name": name,
                    "ip": ip,
                    "timestamp": ts
                })
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
            print(f"[FEEDBACK ERROR] {ex}", flush=True)
            self.send_json_error(500, str(ex))

    def handle_logout(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length))
            name   = str(body.get("name",  "")).strip()
            email  = str(body.get("email", "")).strip().lower()
            dur    = int(body.get("duration_seconds", 0))
            ip     = self.get_client_ip()
            ts     = (datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d %H:%M:%S IST")
            mins, secs = divmod(dur, 60)
            hrs,  mins = divmod(mins, 60)
            dur_fmt = f"{hrs}h {mins}m {secs}s" if hrs else f"{mins}m {secs}s"
            print(f"[LOGOUT] {ts} | {name} | {email} | {ip} | {dur_fmt}", flush=True)

            if SHEETS_WEBHOOK:
                params = urlencode({
                    "type": "logout",
                    "name": name,
                    "email": email,
                    "ip": ip,
                    "duration_seconds": dur,
                    "duration_fmt": dur_fmt,
                    "timestamp": ts
                })
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
            print(f"[LOGOUT ERROR] {ex}", flush=True)
            self.send_json_error(500, str(ex))

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Serving on http://localhost:{PORT}")
    server.serve_forever()
