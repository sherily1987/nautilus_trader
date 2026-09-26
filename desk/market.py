"""Public BTC-USDT perpetual candles. No API key and no orders.

Binance futures is blocked from this machine, so the series comes from the
OKX BTC-USDT-SWAP market.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
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
# Taker buy/sell volume is only published for these periods.
TAKER_PERIOD = {
    "5": "5m",
    "15": "15m",
    "60": "1H",
    "240": "4H",
}
TAKER_REFRESH_SECONDS = 5

_LOCK = threading.Lock()
_BARS: dict[tuple[str, str], list[dict]] = {}
_TAKER: dict[str, dict[int, tuple[float, float]]] = {}
_TAKER_AT: dict[str, float] = {}
_TAKER_LOCKS = {interval: threading.Lock() for interval in TAKER_PERIOD}


def is_live(symbol: str) -> bool:
    return symbol in LIVE_SYMBOLS


RATE_LIMIT_RETRIES = 3


def _get(path: str) -> dict:
    request = urllib.request.Request(
        BASE + path,
        headers={"User-Agent": "nautilus-desk", "Accept": "application/json"},
    )
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < RATE_LIMIT_RETRIES:
                time.sleep(1.0 + attempt)
                continue
            raise
        if payload.get("code") == "50011" and attempt < RATE_LIMIT_RETRIES:
            time.sleep(1.0 + attempt)
            continue
        if payload.get("code") != "0":
            raise RuntimeError(payload.get("msg") or "行情获取失败")
        return payload
    raise RuntimeError("行情获取失败")


def _bar(row: list) -> dict:
    return {
        "time": int(row[0]) // 1000,
        "open": float(row[1]),
        "high": float(row[2]),
        "low": float(row[3]),
        "close": float(row[4]),
        "volume": float(row[6]),
        "closed": row[8] == "1",
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


RESEARCH_REFRESH_SECONDS = 1800
RESEARCH_BARS = {"15": 6000, "60": 6000, "240": 2400}
_RESEARCH: dict[str, tuple[float, list[dict]]] = {}


def research_bars(symbol: str, interval: str, limit: int) -> list[dict]:
    """Up to `limit` closed candles for offline research, cached for half an hour."""
    if symbol not in LIVE_SYMBOLS or interval not in OKX_BAR:
        raise ValueError("这个品种没有实盘行情")
    key = f"{interval}:{limit}"
    with _LOCK:
        cached = _RESEARCH.get(key)
    if cached is not None and time.monotonic() - cached[0] < RESEARCH_REFRESH_SECONDS:
        return cached[1]
    bars = [bar for bar in _history(interval, limit) if bar["closed"]]
    with _LOCK:
        _RESEARCH[key] = (time.monotonic(), bars)
    return bars


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


def _taker_page(interval: str, limit: int, end: str | None = None) -> list[list]:
    path = (
        f"/api/v5/rubik/stat/taker-volume-contract?instId={INST_ID}"
        f"&period={TAKER_PERIOD[interval]}&unit=0&limit={limit}"
    )
    if end is not None:
        path += f"&end={end}"
    return list(_get(path)["data"])


def _taker_history(interval: str, limit: int) -> dict[int, tuple[float, float]]:
    rows: list[list] = []
    end = None
    while len(rows) < limit:
        size = min(100, limit - len(rows))
        page = _taker_page(interval, size, end)
        if not page:
            break
        rows.extend(page)
        end = page[-1][0]
        if len(page) < size:
            break
        time.sleep(0.45)
    return {int(row[0]) // 1000: (float(row[2]), float(row[1])) for row in rows}


def _taker(interval: str) -> dict[int, tuple[float, float]]:
    """Taker volume keyed by bar open time: (buy, sell) in BTC."""
    with _TAKER_LOCKS[interval]:
        with _LOCK:
            cached = _TAKER.get(interval)
            fetched_at = _TAKER_AT.get(interval, 0.0)
        if cached is None:
            fresh = _taker_history(interval, 1000)
        elif time.monotonic() - fetched_at >= TAKER_REFRESH_SECONDS:
            fresh = {**cached, **_taker_history(interval, 2)}
        else:
            return cached
        with _LOCK:
            _TAKER[interval] = fresh
            _TAKER_AT[interval] = time.monotonic()
        return fresh


def warm_taker() -> None:
    """Fetch taker history for every supported period ahead of the first chart."""
    for interval in ("240", "60", "15", "5"):
        try:
            _taker(interval)
        except Exception:
            continue


def with_taker(symbol: str, interval: str, bars: list[dict]) -> list[dict]:
    """Copies of the bars with buyVol and sellVol where OKX publishes them."""
    if symbol not in LIVE_SYMBOLS or interval not in TAKER_PERIOD:
        return bars
    try:
        split = _taker(interval)
    except Exception:
        return bars
    out = []
    for bar in bars:
        pair = split.get(bar["time"])
        if pair is None:
            out.append(bar)
        else:
            out.append({**bar, "buyVol": pair[0], "sellVol": pair[1]})
    return out


FUNDING_REFRESH_SECONDS = 600
_FUNDING: dict[str, object] = {}


def funding_history() -> dict[int, float]:
    """Settled funding rates keyed by settlement time in seconds. OKX keeps about three months."""
    with _LOCK:
        cached = _FUNDING.get("rates")
        fetched_at = float(_FUNDING.get("at", 0.0))
    if cached is not None and time.monotonic() - fetched_at < FUNDING_REFRESH_SECONDS:
        return cached
    rates: dict[int, float] = {}
    after = None
    for _ in range(10):
        path = f"/api/v5/public/funding-rate-history?instId={INST_ID}&limit=100"
        if after is not None:
            path += f"&after={after}"
        page = _get(path)["data"]
        if not page:
            break
        for row in page:
            rate = row.get("realizedRate") or row.get("fundingRate")
            rates[int(row["fundingTime"]) // 1000] = float(rate)
        after = page[-1]["fundingTime"]
        if len(page) < 100:
            break
        time.sleep(0.25)
    with _LOCK:
        _FUNDING["rates"] = rates
        _FUNDING["at"] = time.monotonic()
    return rates


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
