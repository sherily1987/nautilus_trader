"""Carver-style breakout forecast with volatility-targeted position sizing.

Follows the breakout rule in Rob Carver's pysystemtrade: where the close sits in
its rolling channel, scaled to roughly -20..+20 and smoothed. Several lookbacks
are averaged, and the position scales with the forecast and inversely with
recent volatility, so a forecast of +10 at average volatility holds the base
quantity.
"""

from __future__ import annotations

import math
from collections import deque


LOOKBACK_DAYS = (10, 20, 40, 80)
LOOKBACKS = (20, 40, 80, 160, 320)
FORECAST_CAP = 20.0
DIVERSIFICATION_MULTIPLIER = 1.2
VOL_SPAN = 36
LONG_VOL_BARS = 500
LONG_VOL_WEIGHT = 0.3
MAX_LEVERAGE_OF_BASE = 2.0
BUFFER_OF_BASE = 0.25


def lookbacks_for(bar_minutes: int) -> tuple[int, ...]:
    """Carver's daily breakout lookbacks expressed in bars of this size."""
    per_day = max(1, 24 * 60 // bar_minutes)
    return tuple(days * per_day for days in LOOKBACK_DAYS)


class CarverSignal:
    """Streaming forecast and target position. Feed one closed bar at a time."""

    def __init__(
        self,
        quantity: float,
        size_step: float,
        side: str = "both",
        lookbacks: tuple[int, ...] = LOOKBACKS,
        buffer: float = BUFFER_OF_BASE,
    ) -> None:
        self.quantity = quantity
        self.size_step = size_step
        self.side = side
        self.lookbacks = lookbacks
        self.buffer = buffer
        self._closes: deque[float] = deque(maxlen=max(lookbacks))
        self._smoothed: dict[int, float | None] = {lookback: None for lookback in lookbacks}
        self._variance: float | None = None
        self._vols: deque[float] = deque(maxlen=LONG_VOL_BARS)
        self.count = 0
        self.forecast = 0.0
        self.volatility: float | None = None

    @property
    def ready(self) -> bool:
        return self.count >= max(self.lookbacks) and self.volatility is not None

    def update(self, close: float) -> None:
        if self._closes:
            change = close / self._closes[-1] - 1.0
            alpha = 2.0 / (VOL_SPAN + 1.0)
            squared = change * change
            self._variance = squared if self._variance is None else alpha * squared + (1 - alpha) * self._variance
            self.volatility = math.sqrt(self._variance)
            self._vols.append(self.volatility)
        self._closes.append(close)
        self.count += 1

        forecasts = []
        closes = list(self._closes)
        for lookback in self.lookbacks:
            if len(closes) < max(2, math.ceil(lookback / 2)):
                continue
            window = closes[-lookback:]
            high = max(window)
            low = min(window)
            raw = 0.0 if high == low else 40.0 * (close - (high + low) / 2.0) / (high - low)
            span = max(int(lookback / 4), 1)
            alpha = 2.0 / (span + 1.0)
            previous = self._smoothed[lookback]
            smoothed = raw if previous is None else alpha * raw + (1 - alpha) * previous
            self._smoothed[lookback] = smoothed
            forecasts.append(smoothed)
        if forecasts:
            combined = sum(forecasts) / len(forecasts) * DIVERSIFICATION_MULTIPLIER
            self.forecast = max(-FORECAST_CAP, min(FORECAST_CAP, combined))

    def target(self) -> float:
        """Target position in base units for the latest bar."""
        if not self.ready or not self._vols:
            return 0.0
        long_run = sum(self._vols) / len(self._vols)
        blended = (1 - LONG_VOL_WEIGHT) * self.volatility + LONG_VOL_WEIGHT * long_run
        if blended <= 0:
            return 0.0
        forecast = self.forecast
        if self.side == "long":
            forecast = max(forecast, 0.0)
        elif self.side == "short":
            forecast = min(forecast, 0.0)
        raw = self.quantity * (forecast / 10.0) * (long_run / blended)
        cap = self.quantity * MAX_LEVERAGE_OF_BASE
        raw = max(-cap, min(cap, raw))
        return self._lots(raw) * self.size_step

    def _lots(self, amount: float) -> int:
        return round(amount / self.size_step)

    def next_position(self, current: float) -> float:
        """Target if it moved beyond the buffer, otherwise the current position.

        Compared in whole lots so the buffer edge does not depend on float noise.
        """
        wanted = self._lots(self.target())
        held = self._lots(current)
        if abs(wanted - held) <= self._lots(self.quantity * self.buffer):
            return held * self.size_step
        return wanted * self.size_step
