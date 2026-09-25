"""Local TradingView-style desk. Run with the project virtualenv."""

from __future__ import annotations

import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs
from urllib.parse import urlparse

import engine


HOST = "127.0.0.1"
PORT = 8765
STATIC = Path(__file__).resolve().parent / "static"
READY = False
BOOT_ERROR = ""


class DeskHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._json({"ready": READY, "error": BOOT_ERROR})
            return
        if not READY:
            self._json({"error": BOOT_ERROR or "引擎正在启动"}, status=503)
            return
        if parsed.path == "/api/watchlist":
            self._json({"symbols": engine.watchlist()})
            return
        if parsed.path == "/api/bars":
            self._bars(parse_qs(parsed.query))
            return
        if parsed.path == "/api/live":
            self._live(parse_qs(parsed.query))
            return
        if parsed.path == "/":
            self._file(STATIC / "index.html")
            return
        self._file(STATIC / parsed.path.lstrip("/"))

    def do_POST(self) -> None:
        if not READY:
            self._json({"error": BOOT_ERROR or "引擎正在启动"}, status=503)
            return
        if urlparse(self.path).path != "/api/backtest":
            self._json({"error": "未知接口"}, status=404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
            result = engine.run_backtest(
                symbol=str(body.get("symbol", "EURUSD")),
                interval=str(body.get("interval", "15")),
                fast=int(body["fast"]) if "fast" in body else 120,
                slow=int(body["slow"]) if "slow" in body else 10,
                qty=float(body["qty"]) if "qty" in body else engine.SYMBOLS.get(str(body.get("symbol", "EURUSD")), {}).get("qty", 100_000),
                side=str(body.get("side", "both")),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc) or "参数无效"}, status=400)
            return
        except Exception as exc:
            self._json({"error": str(exc)}, status=500)
            return
        self._json(result)

    def _query_chart(self, query: dict) -> tuple[str, str, int, int]:
        symbol = query.get("symbol", ["BTCUSDT"])[0]
        interval = query.get("interval", ["15"])[0]
        fast = int(query.get("fast", ["120"])[0])
        slow = int(query.get("slow", ["10"])[0])
        return symbol, interval, fast, slow

    def _bars(self, query: dict) -> None:
        try:
            symbol, interval, fast, slow = self._query_chart(query)
            if engine.market.is_live(symbol):
                self._json(engine.live_view(symbol, interval, fast, slow))
            else:
                self._json(engine.chart(symbol, interval, fast, slow))
        except (ValueError, TypeError) as exc:
            self._json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._json({"error": f"行情获取失败: {exc}"}, status=502)

    def _live(self, query: dict) -> None:
        try:
            symbol, interval, fast, slow = self._query_chart(query)
            self._json(engine.live_tail(symbol, interval, fast, slow))
        except (ValueError, TypeError) as exc:
            self._json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._json({"error": f"行情获取失败: {exc}"}, status=502)

    def _json(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path: Path) -> None:
        root = STATIC.resolve()
        target = path.resolve()
        if not target.is_relative_to(root) or not target.is_file():
            self._json({"error": "未找到页面"}, status=404)
            return
        data = target.read_bytes()
        mime = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
        }.get(target.suffix, mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args) -> None:
        return


def _warm() -> None:
    global READY, BOOT_ERROR
    try:
        engine.watchlist()
        READY = True
        threading.Thread(target=engine.market.warm_taker, daemon=True).start()
    except Exception as exc:
        BOOT_ERROR = str(exc)


def main() -> None:
    threading.Thread(target=_warm, daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), DeskHandler)
    print(f"Nautilus desk: http://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
