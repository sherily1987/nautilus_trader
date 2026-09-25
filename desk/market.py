"""Public BTC-USDT perpetual candles. No API key and no orders.

Binance futures is blocked from this machine, so the series comes from the
OKX BTC-USDT-SWAP market.
"""

from __future__ import annotations

import json
import threading
import urllib.request


BASE = "https://www.okx.com"
INST_ID = "BTC-USDT-SWAP"
VENUE_NAME = "OKX"
OKX_BAR = {
    "1": "1m",
    "5": "5m",
    "15": "15m",
    "60": "1H",
    "240": "4H",
}
LIVE_SYMBOLS = {"BTCUSDT"}

_LOCK = threading.Lock()
_BARS: dict[tuple[str, str], list[dict]] = {}


def is_live(symbol: str) -> bool:
    return symbol in LIVE_SYMBOLS


def _get(path: str) -> dict:
    request = urllib.request.Request(
        BASE + path,
        headers={"User-Agent": "nautilus-desk", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        payload = json.load(response)
    if payload.get("code") != "0":
        raise RuntimeError(payload.get("msg") or "行情获取失败")
    return payload


def _bar(row: list) -> dict:
    return {
        "time": int(row[0]) // 1000,
        "open": float(row[1]),
        "high": float(row[2]),
        "low": float(row[3]),
        "close": float(row[4]),
        "volume": float(row[6]),
    }


def _latest(interval: str, count: int) -> list[dict]:
    spec = OKX_BAR[interval]
    payload = _get(f"/api/v5/market/candles?instId={INST_ID}&bar={spec}&limit={count}")
    rows = list(payload["data"])
    rows.reverse()
    return [_bar(row) for row in rows]


def _history(interval: str, limit: int) -> list[dict]:
    spec = OKX_BAR[interval]
    rows: list[list] = []
    after = None
    while len(rows) < limit:
        path = f"/api/v5/market/history-candles?instId={INST_ID}&bar={spec}&limit=300"
        if after is not None:
            path += f"&after={after}"
        data = _get(path)["data"]
        if not data:
            break
        rows.extend(data)
        after = data[-1][0]
        if len(data) < 300:
            break
    chosen = list(reversed(rows[:limit]))
    return [_bar(row) for row in chosen]


def _fetch(symbol: str, interval: str, limit: int) -> list[dict]:
    if symbol not in LIVE_SYMBOLS or interval not in OKX_BAR:
        raise ValueError("这个品种没有实盘行情")
    bars = _history(interval, limit)
    return _merge(bars, _latest(interval, 2))


def _merge(existing: list[dict], fresh: list[dict]) -> list[dict]:
    bars = list(existing)
    for bar in fresh:
        if bars and bar["time"] == bars[-1]["time"]:
            bars[-1] = bar
        elif not bars or bar["time"] > bars[-1]["time"]:
            bars.append(bar)
    if len(bars) > 1000:
        bars = bars[-1000:]
    return bars


def load_bars(symbol: str, interval: str) -> list[dict]:
    """Recent perpetual candles, including the one that is still forming."""
    key = (symbol, interval)
    with _LOCK:
        cached = _BARS.get(key)
    if cached:
        return refresh_tail(symbol, interval)
    bars = _fetch(symbol, interval, 1000)
    with _LOCK:
        _BARS[key] = bars
    return bars


def refresh_tail(symbol: str, interval: str) -> list[dict]:
    """Replace the forming candle and append a bar when the timeframe rolls."""
    key = (symbol, interval)
    if symbol not in LIVE_SYMBOLS or interval not in OKX_BAR:
        raise ValueError("这个品种没有实盘行情")
    fresh = _latest(interval, 2)
    with _LOCK:
        cached = _BARS.get(key)
    if not cached:
        cached = _fetch(symbol, interval, 1000)
    merged = _merge(cached, fresh)
    with _LOCK:
        _BARS[key] = merged
    return merged


def ticker(symbol: str) -> dict:
    """24-hour last price and change from the public perpetual ticker."""
    if symbol not in LIVE_SYMBOLS:
        raise ValueError("这个品种没有实盘行情")
    tick = _get(f"/api/v5/market/ticker?instId={INST_ID}")["data"][0]
    last = float(tick["last"])
    open_price = float(tick["open24h"])
    change = last - open_price
    return {
        "last": last,
        "change": change,
        "changePct": (change / open_price) * 100 if open_price else 0.0,
    }
