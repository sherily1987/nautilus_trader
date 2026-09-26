"""Fast bar-level simulation for parameter robustness and walk-forward checks.

Uses the same execution rules as the desk backtest: a signal is taken at a bar's
close, filled at the next bar's open, charged the taker fee, and funding is paid
on the position held at each settlement.
"""

from __future__ import annotations

import math
from collections import deque

import carver
import engine
import market


TAKER_FEE = float(engine.PERP_TAKER_FEE)
RESEARCH_BARS = market.RESEARCH_BARS
FAST_FACTORS = (0.5, 0.75, 1.0, 1.25, 1.5)
SLOW_FACTORS = (0.5, 1.0, 1.5, 2.0)
TRAIN_BARS = {"15": 2880, "60": 1440, "240": 540}
TEST_BARS = {"15": 960, "60": 480, "240": 180}


def prior_extreme(values: list[float], window: int, highest: bool) -> list[float | None]:
    """Max or min of the `window` values before each index, excluding the index itself."""
    out: list[float | None] = [None] * len(values)
    candidates: deque[int] = deque()
    for index in range(len(values)):
        if index >= window:
            while candidates and candidates[0] < index - window:
                candidates.popleft()
            out[index] = values[candidates[0]]
        value = values[index]
        while candidates and ((values[candidates[-1]] <= value) if highest else (values[candidates[-1]] >= value)):
            candidates.pop()
        candidates.append(index)
    return out


def breakout_positions(bars: list[dict], fast: int, slow: int, side: str, qty: float = 1.0) -> list[float]:
    """Position decided at each bar's close by the desk's breakout rules."""
    highs = [bar["high"] for bar in bars]
    lows = [bar["low"] for bar in bars]
    upper = prior_extreme(highs, fast, True)
    lower = prior_extreme(lows, fast, False)
    exit_high = prior_extreme(highs, slow, True)
    exit_low = prior_extreme(lows, slow, False)
    positions = [0.0] * len(bars)
    position = 0.0
    for index, bar in enumerate(bars):
        close = bar["close"]
        if upper[index] is not None:
            if position > 0 and close < exit_low[index]:
                position = 0.0
            elif position < 0 and close > exit_high[index]:
                position = 0.0
            elif position == 0 and close > upper[index] and side != "short":
                position = qty
            elif position == 0 and close < lower[index] and side != "long":
                position = -qty
        positions[index] = position
    return positions


def carver_positions(
    bars: list[dict],
    side: str,
    qty: float = 1.0,
    size_step: float = 0.001,
    lookbacks: tuple[int, ...] = carver.LOOKBACKS,
    buffer: float = carver.BUFFER_OF_BASE,
) -> list[float]:
    """Position decided at each bar's close by the Carver forecast, with its buffer."""
    signal = carver.CarverSignal(qty, size_step, side, lookbacks, buffer)
    positions = []
    position = 0.0
    for bar in bars:
        signal.update(bar["close"])
        position = signal.next_position(position)
        positions.append(position)
    return positions


def bar_pnl(
    bars: list[dict],
    positions: list[float],
    interval: str,
    events: list[tuple[int, float, bool]],
) -> list[float]:
    """PnL of every bar in quote currency, net of fees and funding."""
    bar_seconds = engine.INTERVALS[interval] * 60
    pnl = [0.0] * len(bars)
    event_cursor = 0
    for index in range(1, len(bars)):
        bar = bars[index]
        held_before = positions[index - 2] if index >= 2 else 0.0
        held = positions[index - 1]
        value = held_before * (bar["open"] - bars[index - 1]["close"])
        value += held * (bar["close"] - bar["open"])
        value -= TAKER_FEE * abs(held - held_before) * bar["open"]
        while event_cursor < len(events) and events[event_cursor][0] <= bar["time"]:
            event_cursor += 1
        while event_cursor < len(events) and events[event_cursor][0] <= bar["time"] + bar_seconds:
            value -= held * bar["close"] * events[event_cursor][1]
            event_cursor += 1
        pnl[index] = value
    return pnl


def summarize(pnl: list[float], interval: str) -> dict:
    total = sum(pnl)
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for value in pnl:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    returns = []
    running = 0.0
    for value in pnl:
        returns.append(value / (engine.STARTING_BALANCE + running))
        running += value
    sharpe = None
    if len(returns) > 1:
        mean = sum(returns) / len(returns)
        variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
        if variance > 0:
            sharpe = mean / math.sqrt(variance) * math.sqrt(365 * 24 * 60 / engine.INTERVALS[interval])
    return {
        "pnl": total,
        "sharpe": sharpe,
        "maxDrawdown": drawdown,
        "returnOverDrawdown": total / drawdown if drawdown > 0 else None,
    }


def _grid(fast: int, slow: int) -> tuple[list[int], list[int]]:
    fasts = sorted({max(5, min(200, round(fast * factor))) for factor in FAST_FACTORS})
    slows = sorted({max(2, min(100, round(slow * factor))) for factor in SLOW_FACTORS})
    return fasts, slows


def robustness(symbol: str, interval: str, fast: int, slow: int, side: str) -> dict:
    """Neighbourhood scan and rolling walk-forward on about a year of closed bars."""
    if interval not in RESEARCH_BARS:
        raise ValueError("稳健性检查支持 15m、1H、4H")
    engine._validate(symbol, interval, fast, slow, 1)
    bars = market.research_bars(symbol, interval, RESEARCH_BARS[interval])
    if len(bars) < TRAIN_BARS[interval] + TEST_BARS[interval]:
        raise ValueError("历史数据不足")
    events = engine._funding_events(symbol, bars, interval)
    fasts, slows = _grid(fast, slow)

    streams: dict[tuple[int, int], list[float]] = {}
    cells = []
    for grid_fast in fasts:
        for grid_slow in slows:
            if grid_slow >= grid_fast:
                continue
            pnl = bar_pnl(bars, breakout_positions(bars, grid_fast, grid_slow, side), interval, events)
            streams[(grid_fast, grid_slow)] = pnl
            cells.append({"fast": grid_fast, "slow": grid_slow, **summarize(pnl, interval)})

    chosen_key = (fast, slow)
    if chosen_key not in streams:
        streams[chosen_key] = bar_pnl(bars, breakout_positions(bars, fast, slow, side), interval, events)
    chosen = summarize(streams[chosen_key], interval)

    neighbours = [cell for cell in cells if (cell["fast"], cell["slow"]) != chosen_key]
    profitable = sum(1 for cell in neighbours if cell["pnl"] > 0)
    sharpes = sorted(cell["sharpe"] for cell in neighbours if cell["sharpe"] is not None)
    median_sharpe = sharpes[len(sharpes) // 2] if sharpes else None

    walk = _walk_forward(bars, streams, interval)
    oos_start = walk["trainBars"]
    oos_stop = oos_start + len(walk["curve"])
    walk["fixed"] = summarize(streams[chosen_key][oos_start:oos_stop], interval) if walk["curve"] else None
    return {
        "symbol": symbol,
        "interval": interval,
        "side": side,
        "bars": len(bars),
        "from": bars[0]["time"],
        "to": bars[-1]["time"],
        "fasts": fasts,
        "slows": slows,
        "cells": cells,
        "chosen": {"fast": fast, "slow": slow, **chosen},
        "neighbours": {
            "count": len(neighbours),
            "profitable": profitable,
            "medianSharpe": median_sharpe,
        },
        "walkForward": walk,
    }


def _walk_forward(bars: list[dict], streams: dict[tuple[int, int], list[float]], interval: str) -> dict:
    """Pick the best train-window Sharpe, then trade the following window with it."""
    train = TRAIN_BARS[interval]
    test = TEST_BARS[interval]
    windows = []
    oos: list[float] = []
    start = 0
    while start + train + test <= len(bars):
        train_slice = slice(start, start + train)
        test_slice = slice(start + train, start + train + test)
        scored = []
        for key, pnl in streams.items():
            stats = summarize(pnl[train_slice], interval)
            scored.append((stats["sharpe"] if stats["sharpe"] is not None else -math.inf, key, stats))
        scored.sort(key=lambda item: item[0], reverse=True)
        _, best_key, best_train = scored[0]
        test_pnl = streams[best_key][test_slice]
        test_stats = summarize(test_pnl, interval)
        oos.extend(test_pnl)
        windows.append(
            {
                "from": bars[test_slice.start]["time"],
                "to": bars[test_slice.stop - 1]["time"],
                "fast": best_key[0],
                "slow": best_key[1],
                "trainSharpe": best_train["sharpe"],
                "testSharpe": test_stats["sharpe"],
                "testPnl": test_stats["pnl"],
            },
        )
        start += test
    curve = []
    equity = 0.0
    offset = train
    for index, value in enumerate(oos):
        equity += value
        curve.append({"time": bars[offset + index]["time"], "value": equity})
    summary = summarize(oos, interval) if oos else None
    return {
        "trainBars": train,
        "testBars": test,
        "windows": windows,
        "oos": summary,
        "positiveWindows": sum(1 for window in windows if window["testPnl"] > 0),
        "curve": curve,
    }
