"""Local market data and Nautilus backtests for the desk UI."""

from __future__ import annotations

import random
import threading
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

from nautilus_trader.backtest import BacktestEngine
from nautilus_trader.backtest import BacktestEngineConfig
from nautilus_trader.common import LogLevel
from nautilus_trader.common import LoggerConfig
from nautilus_trader.core.datetime import dt_to_unix_nanos
from nautilus_trader.execution import MakerTakerFeeModel
from nautilus_trader.model import AccountType
from nautilus_trader.model import Bar
from nautilus_trader.model import Currency
from nautilus_trader.model import BarType
from nautilus_trader.model import Money
from nautilus_trader.model import OmsType
from nautilus_trader.model import OrderSide
from nautilus_trader.model import Quantity
from nautilus_trader.model import TimeInForce
from nautilus_trader.model import TraderId
from nautilus_trader.model import Venue
from nautilus_trader.testkit.providers import TestInstrumentProvider
from nautilus_trader.trading import Strategy

import market


VENUE_NAME = "SIM"
USD = Currency.from_str("USD")
STARTING_BALANCE = 1_000_000
HISTORY_DAYS = 21
MINUTE_BARS = HISTORY_DAYS * 24 * 60
INTERVALS = {
    "1": 1,
    "5": 5,
    "15": 15,
    "60": 60,
    "240": 240,
}
BAR_SPECS = {
    "1": "1-MINUTE",
    "5": "5-MINUTE",
    "15": "15-MINUTE",
    "60": "1-HOUR",
    "240": "4-HOUR",
}
SYMBOLS = {
    "BTCUSDT": {
        "name": "比特币永续",
        "price": 63450.0,
        "digits": 2,
        "sigma": 0.00085,
        "volume": 8,
        "floor": 15000.0,
        "qty": 1,
        "minQty": 0.001,
        "maxQty": 100,
        "qtyStep": 0.001,
        "currency": "USDT",
    },
    "EURUSD": {"name": "欧元/美元", "price": 1.08520, "digits": 5, "qty": 100_000, "minQty": 1, "maxQty": 5_000_000, "qtyStep": 1000, "currency": "USD"},
    "GBPUSD": {"name": "英镑/美元", "price": 1.27140, "digits": 5, "qty": 100_000, "minQty": 1, "maxQty": 5_000_000, "qtyStep": 1000, "currency": "USD"},
    "USDJPY": {"name": "美元/日元", "price": 149.250, "digits": 3, "qty": 100_000, "minQty": 1, "maxQty": 5_000_000, "qtyStep": 1000, "currency": "USD"},
    "AUDUSD": {"name": "澳元/美元", "price": 0.66280, "digits": 5, "qty": 100_000, "minQty": 1, "maxQty": 5_000_000, "qtyStep": 1000, "currency": "USD"},
    "USDCAD": {"name": "美元/加元", "price": 1.35420, "digits": 5, "qty": 100_000, "minQty": 1, "maxQty": 5_000_000, "qtyStep": 1000, "currency": "USD"},
    "NZDUSD": {"name": "纽元/美元", "price": 0.61450, "digits": 5, "qty": 100_000, "minQty": 1, "maxQty": 5_000_000, "qtyStep": 1000, "currency": "USD"},
}

_LOCK = threading.Lock()
_MINUTE_CACHE: dict[str, list[dict]] = {}


def _fee_model() -> MakerTakerFeeModel:
    try:
        return MakerTakerFeeModel()
    except TypeError:
        return MakerTakerFeeModel(
            maker_rate=Decimal("0.00002"),
            taker_rate=Decimal("0.00002"),
        )


def _round_price(value: float, digits: int) -> float:
    return round(value, digits)


def generate_minutes(symbol: str) -> list[dict]:
    """Build a stable one-minute series for a symbol."""
    cached = _MINUTE_CACHE.get(symbol)
    if cached is not None:
        return cached

    meta = SYMBOLS[symbol]
    digits = meta["digits"]
    rng = random.Random(symbol)
    price = float(meta["price"])
    sigma = float(meta.get("sigma", 0.00016))
    floor = float(meta.get("floor", 10 ** (-digits)))
    vol_mean = float(meta.get("volume", 1200))
    start = datetime(2024, 9, 2, tzinfo=UTC)
    bars: list[dict] = []
    for index in range(MINUTE_BARS):
        shock = rng.gauss(0, sigma)
        open_px = price
        close_px = max(open_px * (1 + shock), floor)
        wick = abs(rng.gauss(0, sigma * 0.55))
        high_px = max(open_px, close_px) * (1 + wick)
        low_px = min(open_px, close_px) * (1 - wick)
        moment = start + timedelta(minutes=index + 1)
        bars.append(
            {
                "time": int(moment.timestamp()),
                "open": _round_price(open_px, digits),
                "high": _round_price(high_px, digits),
                "low": _round_price(low_px, digits),
                "close": _round_price(close_px, digits),
                "volume": max(1, int(abs(rng.gauss(vol_mean, vol_mean * 0.3)))),
            },
        )
        price = close_px

    _MINUTE_CACHE[symbol] = bars
    return bars


def aggregate(bars: list[dict], minutes: int) -> list[dict]:
    """Roll one-minute bars up to a higher timeframe."""
    if minutes <= 1:
        return bars
    grouped: list[dict] = []
    for offset in range(0, len(bars) - minutes + 1, minutes):
        chunk = bars[offset : offset + minutes]
        grouped.append(
            {
                "time": chunk[-1]["time"],
                "open": chunk[0]["open"],
                "high": max(item["high"] for item in chunk),
                "low": min(item["low"] for item in chunk),
                "close": chunk[-1]["close"],
                "volume": sum(item["volume"] for item in chunk),
            },
        )
    return grouped


def ema_line(bars: list[dict], period: int) -> list[dict]:
    """Exponential moving average aligned to bar close times."""
    if period < 1 or not bars:
        return []
    weight = 2 / (period + 1)
    value = bars[0]["close"]
    line = [{"time": bars[0]["time"], "value": value}]
    for bar in bars[1:]:
        value = bar["close"] * weight + value * (1 - weight)
        line.append({"time": bar["time"], "value": value})
    return line


def channel_line(bars: list[dict], period: int, field: str) -> list[dict]:
    """Prior-window high or low. The current bar is not included."""
    if period < 1 or len(bars) <= period:
        return []
    line = []
    for index in range(period, len(bars)):
        window = bars[index - period : index]
        if field == "high":
            value = max(item["high"] for item in window)
        else:
            value = min(item["low"] for item in window)
        line.append({"time": bars[index]["time"], "value": value})
    return line


def watchlist() -> list[dict]:
    """Last price and one-day change for every symbol."""
    quotes = []
    for symbol, meta in SYMBOLS.items():
        row = {
            "symbol": symbol,
            "name": meta["name"],
            "digits": meta["digits"],
            "defaultQty": meta["qty"],
            "minQty": meta["minQty"],
            "qtyStep": meta["qtyStep"],
            "currency": meta["currency"],
            "live": market.is_live(symbol),
        }
        if market.is_live(symbol):
            try:
                row.update(market.ticker(symbol))
                quotes.append(row)
                continue
            except Exception:
                row["live"] = False
        bars = generate_minutes(symbol)
        last = bars[-1]
        previous = bars[-1440]["close"]
        change = last["close"] - previous
        row.update(
            {
                "last": last["close"],
                "change": change,
                "changePct": (change / previous) * 100 if previous else 0,
            },
        )
        quotes.append(row)
    return quotes


def live_view(symbol: str, interval: str, fast: int = 20, slow: int = 10) -> dict:
    """Real exchange candles plus EMA overlays."""
    _validate(symbol, interval, fast, slow, SYMBOLS[symbol]["qty"])
    bars = market.load_bars(symbol, interval)
    quote = market.ticker(symbol)
    return {
        "symbol": symbol,
        "name": SYMBOLS[symbol]["name"],
        "interval": interval,
        "digits": SYMBOLS[symbol]["digits"],
        "live": True,
        "venue": market.VENUE_NAME,
        "bars": bars,
        "emaFast": channel_line(bars, fast, "high"),
        "emaSlow": channel_line(bars, fast, "low"),
        **quote,
    }


def live_tail(symbol: str, interval: str, fast: int = 20, slow: int = 10) -> dict:
    """Latest forming candle for the live chart."""
    _validate(symbol, interval, fast, slow, SYMBOLS[symbol]["qty"])
    bars = market.refresh_tail(symbol, interval)
    fast_line = channel_line(bars, fast, "high")
    slow_line = channel_line(bars, fast, "low")
    return {
        "bar": bars[-1],
        "emaFast": fast_line[-1] if fast_line else None,
        "emaSlow": slow_line[-1] if slow_line else None,
        "live": True,
        "venue": market.VENUE_NAME,
        **market.ticker(symbol),
    }


def chart(symbol: str, interval: str, fast: int = 20, slow: int = 10) -> dict:
    """Candles plus EMA overlays, without running a backtest."""
    _validate(symbol, interval, fast, slow, 1)
    bars = aggregate(generate_minutes(symbol), INTERVALS[interval])
    return {
        "symbol": symbol,
        "name": SYMBOLS[symbol]["name"],
        "interval": interval,
        "digits": SYMBOLS[symbol]["digits"],
        "live": False,
        "bars": bars,
        "emaFast": channel_line(bars, fast, "high"),
        "emaSlow": channel_line(bars, fast, "low"),
    }


class BreakoutStrategy(Strategy):
    """Hold a Donchian breakout and exit on a shorter opposite channel."""

    def __new__(cls, *_args: object, **_kwargs: object) -> BreakoutStrategy:
        return super().__new__(cls)

    def __init__(
        self,
        bar_type: BarType,
        fast: int,
        slow: int,
        quantity: float,
        side: str,
        size_precision: int,
    ) -> None:
        super().__init__()
        self._bar_type = bar_type
        self._fast = fast
        self._slow = slow
        self._quantity = quantity
        self._side = side
        self._size_precision = size_precision
        self._highs: list[float] = []
        self._lows: list[float] = []
        self._count = 0
        self._pos = 0.0
        self._entry_px = 0.0
        self._entry_fee = 0.0
        self._queue: list[dict] = []
        self.fills: list[dict] = []
        self.logs: list[str] = []

    def on_start(self) -> None:
        self.subscribe_bars(self._bar_type)
        direction = {"both": "多空", "long": "只做多", "short": "只做空"}.get(self._side, self._side)
        self._note(f"永续突破已启动，{direction}，突破 {self._fast} / 离场 {self._slow}")

    def on_bar(self, bar: Bar) -> None:
        close = bar.close.as_double()
        self._highs.append(bar.high.as_double())
        self._lows.append(bar.low.as_double())
        self._count += 1
        needed = self._fast + 1
        if self._count < needed:
            return

        upper = max(self._highs[-self._fast - 1 : -1])
        lower = min(self._lows[-self._fast - 1 : -1])
        exit_low = min(self._lows[-self._slow - 1 : -1])
        exit_high = max(self._highs[-self._slow - 1 : -1])
        net = self._net_qty()
        step = self._qty_step()
        if net > step and close < exit_low:
            self._flatten(close, f"收盘 {close:.2f} 跌破离场低点 {exit_low:.2f}")
        elif net < -step and close > exit_high:
            self._flatten(close, f"收盘 {close:.2f} 升破离场高点 {exit_high:.2f}")
        elif abs(net) < step and close > upper and self._side != "short":
            self._rebalance(OrderSide.BUY, close, f"收盘 {close:.2f} 突破前高 {upper:.2f}")
        elif abs(net) < step and close < lower and self._side != "long":
            self._rebalance(OrderSide.SELL, close, f"收盘 {close:.2f} 跌破前低 {lower:.2f}")
        if len(self._highs) > needed + 2:
            self._highs = self._highs[-(needed + 2) :]
            self._lows = self._lows[-(needed + 2) :]

    def on_order_filled(self, event) -> None:
        commission = _num(event.commission)
        price = _num(event.last_px)
        qty = _num(event.last_qty)
        buy = bool(event.is_buy)
        meta = self._queue.pop(0) if self._queue else {}
        was = self._pos
        self._pos += qty if buy else -qty
        trade_pnl = None
        step = self._qty_step()
        if abs(was) < step and abs(self._pos) >= step:
            self._entry_px = price
            self._entry_fee = commission
        elif abs(self._pos) < step and abs(was) >= step:
            closed = abs(was)
            gross = (price - self._entry_px) * closed if was > 0 else (self._entry_px - price) * closed
            trade_pnl = gross - self._entry_fee - commission
        self.fills.append(
            {
                "time": int(event.ts_event // 1_000_000_000),
                "side": "BUY" if buy else "SELL",
                "action": meta.get("action", "买入" if buy else "卖出"),
                "reason": meta.get("reason", ""),
                "orderType": "市价",
                "status": "已成交",
                "orderId": meta.get("orderId", ""),
                "signal": meta.get("signal", price),
                "price": price,
                "qty": qty,
                "notional": price * qty,
                "commission": commission,
                "pnl": trade_pnl,
                "position": self._pos,
            },
        )

    def on_stop(self) -> None:
        self._note(f"策略结束，处理 {self._count} 根 K 线，成交 {len(self.fills)} 笔")

    def _net_qty(self) -> float:
        position = self.portfolio.net_position(self._bar_type.instrument_id)
        if position is None:
            return 0.0
        if hasattr(position, "as_double"):
            return float(position.as_double())
        return float(position)

    def _rebalance(self, side: OrderSide, price: float, reason: str) -> None:
        current = self._net_qty()
        target = float(self._quantity if side == OrderSide.BUY else -self._quantity)
        delta = target - current
        if abs(delta) < self._qty_step():
            return
        order_side = OrderSide.BUY if delta > 0 else OrderSide.SELL
        action = "开多" if order_side == OrderSide.BUY else "开空"
        self._submit(order_side, abs(delta), action, reason, price)

    def _flatten(self, price: float, reason: str) -> None:
        current = self._net_qty()
        if abs(current) < self._qty_step():
            return
        order_side = OrderSide.SELL if current > 0 else OrderSide.BUY
        action = "平多" if current > 0 else "平空"
        self._submit(order_side, abs(current), action, reason, price)

    def _submit(self, order_side: OrderSide, amount: float, action: str, reason: str, signal: float) -> None:
        order = self.order_factory.market(
            instrument_id=self._bar_type.instrument_id,
            order_side=order_side,
            quantity=self._make_qty(amount),
            time_in_force=TimeInForce.GTC,
        )
        self._queue.append(
            {
                "action": action,
                "reason": reason,
                "signal": signal,
                "orderId": str(order.client_order_id),
            },
        )
        self.submit_order(order)
        self._note(f"{action} 市价 {amount:.8g} @ {signal:.2f}，{reason}")

    def _qty_step(self) -> float:
        if self._size_precision <= 0:
            return 1
        return 10 ** (-self._size_precision)

    def _make_qty(self, amount: float) -> Quantity:
        if self._size_precision <= 0:
            return Quantity.from_int(max(1, int(round(amount))))
        return Quantity.from_str(f"{amount:.{self._size_precision}f}")

    def _note(self, message: str) -> None:
        self.logs.append(message)
        if len(self.logs) > 200:
            del self.logs[: len(self.logs) - 200]


def _instrument(symbol: str):
    if symbol == "BTCUSDT":
        return TestInstrumentProvider.btcusdt_perp_binance()
    return TestInstrumentProvider.default_fx_ccy(symbol=symbol, venue=Venue(VENUE_NAME))


def run_backtest(symbol: str, interval: str, fast: int, slow: int, qty: float, side: str) -> dict:
    """Run one breakout backtest and return chart-ready JSON."""
    _validate(symbol, interval, fast, slow, qty)
    if side not in {"both", "long", "short"}:
        raise ValueError("方向无效")

    if market.is_live(symbol):
        payload = live_view(symbol, interval, fast, slow)
    else:
        payload = chart(symbol, interval, fast, slow)
    bars = payload["bars"]
    instrument = _instrument(symbol)
    if symbol == "BTCUSDT":
        venue = instrument.venue
        base_currency = instrument.quote_currency
        balance = Money(STARTING_BALANCE, base_currency)
    else:
        venue = Venue(VENUE_NAME)
        base_currency = USD
        balance = Money.from_str(f"{STARTING_BALANCE} USD")
    bar_type = BarType.from_str(f"{instrument.id}-{BAR_SPECS[interval]}-LAST-EXTERNAL")
    nautilus_bars = [
        Bar(
            bar_type=bar_type,
            open=instrument.make_price(float(f"{item['open']:.{SYMBOLS[symbol]['digits']}f}")),
            high=instrument.make_price(float(f"{item['high']:.{SYMBOLS[symbol]['digits']}f}")),
            low=instrument.make_price(float(f"{item['low']:.{SYMBOLS[symbol]['digits']}f}")),
            close=instrument.make_price(float(f"{item['close']:.{SYMBOLS[symbol]['digits']}f}")),
            volume=instrument.make_qty(float(item["volume"])),
            ts_event=dt_to_unix_nanos(datetime.fromtimestamp(item["time"], tz=UTC)),
            ts_init=dt_to_unix_nanos(datetime.fromtimestamp(item["time"], tz=UTC)),
        )
        for item in bars
    ]

    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id=TraderId.from_str("DESK-001"),
            logging=LoggerConfig(stdout_level=LogLevel.ERROR, print_config=False),
        ),
    )
    try:
        engine.add_venue(
            venue=venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            starting_balances=[balance],
            base_currency=base_currency,
            default_leverage=Decimal(20),
            fee_model=_fee_model(),
        )
        engine.add_instrument(instrument)
        engine.add_data(nautilus_bars)
        strategy = BreakoutStrategy(bar_type, fast, slow, float(qty), side, instrument.size_precision)
        engine.add_strategy(strategy)
        with _LOCK:
            engine.run()

        positions = [
            _position_row(position, SYMBOLS[symbol]["digits"])
            for position in (*engine.cache.positions_closed(), *engine.cache.positions_open())
        ]
        stats = _stats(engine, venue, positions, len(strategy.fills), SYMBOLS[symbol]["currency"])
        markers = _markers(strategy.fills)
    finally:
        engine.dispose()

    payload.update(
        {
            "fills": strategy.fills,
            "positions": positions,
            "markers": markers,
            "stats": stats,
            "logs": strategy.logs,
            "fast": fast,
            "slow": slow,
            "qty": qty,
            "side": side,
        },
    )
    return payload


def _num(value) -> float:
    if value is None:
        return 0.0
    if hasattr(value, "as_double"):
        return float(value.as_double())
    return float(value)


def _position_row(position, digits: int) -> dict:
    return {
        "id": str(position.id),
        "side": "多" if position.is_long else "空" if position.is_short else "平",
        "qty": _num(position.peak_qty),
        "open": round(_num(position.avg_px_open), digits),
        "close": None if position.avg_px_close is None else round(_num(position.avg_px_close), digits),
        "pnl": _num(position.realized_pnl),
        "status": "持仓" if position.is_open else "已平",
    }


def _stats(engine: BacktestEngine, venue: Venue, positions: list[dict], fill_count: int, currency: str) -> dict:
    closed = [item for item in positions if item["status"] == "已平"]
    wins = [item for item in closed if item["pnl"] > 0]
    pnl_map = engine.portfolio.total_pnls(venue)
    total = 0.0
    for money in pnl_map.values():
        total += _num(money)
    return {
        "starting": STARTING_BALANCE,
        "pnl": total,
        "equity": STARTING_BALANCE + total,
        "trades": fill_count,
        "closed": len(closed),
        "wins": len(wins),
        "winRate": (len(wins) / len(closed) * 100) if closed else None,
        "openPositions": sum(1 for item in positions if item["status"] == "持仓"),
        "currency": currency,
    }


def _markers(fills: list[dict]) -> list[dict]:
    by_time: dict[int, dict] = {}
    for fill in fills:
        buy = fill["side"] == "BUY"
        by_time[fill["time"]] = {
            "time": fill["time"],
            "position": "belowBar" if buy else "aboveBar",
            "color": "#089981" if buy else "#f23645",
            "shape": "arrowUp" if buy else "arrowDown",
            "text": "B" if buy else "S",
        }
    return [by_time[key] for key in sorted(by_time)]


def _validate(symbol: str, interval: str, fast: int, slow: int, qty: float) -> None:
    if symbol not in SYMBOLS:
        raise ValueError("未知品种")
    if interval not in INTERVALS:
        raise ValueError("未知周期")
    if not isinstance(fast, int) or not isinstance(slow, int):
        raise ValueError("周期必须是整数")
    if fast < 5 or fast > 200 or slow < 2 or slow > 100 or slow >= fast:
        raise ValueError("离场周期必须小于突破周期")
    limits = SYMBOLS[symbol]
    if isinstance(qty, bool) or not isinstance(qty, (int, float)):
        raise ValueError("数量无效")
    if qty < limits["minQty"] or qty > limits["maxQty"]:
        raise ValueError(f"数量必须在 {limits['minQty']} 到 {limits['maxQty']}")

