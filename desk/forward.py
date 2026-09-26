"""Forward (paper) simulation on live closed bars. No orders reach an exchange.

Each run follows newly closed bars after it starts: a signal is taken at a bar's
close and filled at the next bar's open, with the taker fee and funding, the
same rules as the desk backtest. State and every event are kept on disk.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import carver
import engine
import market


STORE = Path.home() / ".nautilus-desk" / "forward"
POLL_SECONDS = 15
FUNDING_WAIT_SECONDS = 900
RECENT_EVENTS = 200
TAKER_FEE = float(engine.PERP_TAKER_FEE)
SIZE_STEP = 0.001


def _breakout_decision(closed: list[dict], index: int, run: dict, position: float) -> tuple[float, str, str]:
    fast, slow, side, qty = run["fast"], run["slow"], run["side"], run["qty"]
    close = closed[index]["close"]
    if index < fast:
        return position, "无操作", "历史不足"
    prior = closed[index - fast : index]
    upper = max(bar["high"] for bar in prior)
    lower = min(bar["low"] for bar in prior)
    recent = closed[index - slow : index]
    exit_high = max(bar["high"] for bar in recent)
    exit_low = min(bar["low"] for bar in recent)
    detail = f"收盘 {close:.2f}，前高 {upper:.2f}，前低 {lower:.2f}，离场高 {exit_high:.2f}，离场低 {exit_low:.2f}"
    if position > 0 and close < exit_low:
        return 0.0, "平多", f"收盘 {close:.2f} 跌破离场低点 {exit_low:.2f}"
    if position < 0 and close > exit_high:
        return 0.0, "平空", f"收盘 {close:.2f} 升破离场高点 {exit_high:.2f}"
    if position == 0 and close > upper and side != "short":
        return qty, "开多", f"收盘 {close:.2f} 突破前高 {upper:.2f}"
    if position == 0 and close < lower and side != "long":
        return -qty, "开空", f"收盘 {close:.2f} 跌破前低 {lower:.2f}"
    return position, "无操作", detail


def _book_fill(run: dict, signed: float, price: float) -> tuple[float, float | None]:
    """Apply a fill with average-cost accounting. Returns fee and realized PnL for a reduction."""
    fee = abs(signed) * price * TAKER_FEE
    was = run["position"]
    qty = abs(signed)
    run["fees"] += fee
    run["realized"] -= fee
    pnl = None
    if abs(was) < 1e-12 or (was > 0) == (signed > 0):
        held = abs(was)
        run["entryPx"] = (run["entryPx"] * held + price * qty) / (held + qty)
        run["entryFee"] = (run["entryFee"] if held else 0.0) + fee
    else:
        closing = min(abs(was), qty)
        entry_fee = run["entryFee"] * closing / abs(was)
        gross = (price - run["entryPx"]) * closing * (1 if was > 0 else -1)
        run["realized"] += gross
        pnl = gross - entry_fee - fee * closing / qty
        run["entryFee"] -= entry_fee
        run["closedTrades"] += 1
        run["wins"] += 1 if pnl > 0 else 0
        if qty - closing > 1e-12:
            run["entryPx"] = price
            run["entryFee"] = fee * (qty - closing) / qty
    run["position"] = round(was + signed, 6)
    if abs(run["position"]) < 1e-12:
        run["position"] = 0.0
        run["entryPx"] = 0.0
        run["entryFee"] = 0.0
    return fee, pnl


def _action(current: float, wanted: float) -> str:
    return engine._scale_action(current, wanted)


class ForwardManager:
    def __init__(self, root: Path = STORE) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._runs: dict[str, dict] = {}
        self._events: dict[str, deque] = {}
        self._signals: dict[str, carver.CarverSignal] = {}
        self._error = ""
        self._load()

    # Persistence

    def _runs_file(self) -> Path:
        return self.root / "runs.json"

    def _events_file(self, run_id: str) -> Path:
        return self.root / f"events-{run_id}.jsonl"

    def _load(self) -> None:
        if self._runs_file().exists():
            for run in json.loads(self._runs_file().read_text(encoding="utf-8")):
                self._runs[run["id"]] = run
        for run_id in self._runs:
            recent: deque = deque(maxlen=RECENT_EVENTS)
            path = self._events_file(run_id)
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        recent.append(json.loads(line))
            self._events[run_id] = recent

    def _save(self) -> None:
        temp = self._runs_file().with_suffix(".tmp")
        temp.write_text(json.dumps(list(self._runs.values()), ensure_ascii=False, indent=1), encoding="utf-8")
        temp.replace(self._runs_file())

    def _emit(self, run: dict, event: dict) -> None:
        event = {"run": run["id"], **event}
        with self._events_file(run["id"]).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._events.setdefault(run["id"], deque(maxlen=RECENT_EVENTS)).append(event)

    # Commands

    def start(self, config: dict) -> dict:
        strategy = config["strategy"]
        interval = config["interval"]
        symbol = config["symbol"]
        if not market.is_live(symbol):
            raise ValueError("前向模拟只支持 BTC 永续的实时行情")
        if strategy not in engine.STRATEGIES:
            raise ValueError("未知策略")
        if strategy == "carver" and interval not in engine.CARVER_INTERVALS:
            raise ValueError("Carver 策略只支持 1H、4H")
        if config["side"] not in {"both", "long", "short"}:
            raise ValueError("方向无效")
        engine._validate(symbol, interval, config["fast"], config["slow"], config["qty"])
        bars = market.load_bars(symbol, interval)
        closed = [bar for bar in bars if bar["closed"]]
        if not closed:
            raise ValueError("还没有收盘的 K 线")
        now = int(time.time())
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{interval}-{strategy}"
        run = {
            "id": run_id,
            "symbol": symbol,
            "strategy": strategy,
            "interval": interval,
            "fast": config["fast"],
            "slow": config["slow"],
            "qty": config["qty"],
            "side": config["side"],
            "status": "running",
            "startedAt": now,
            "startPrice": bars[-1]["close"],
            "lastBarTime": closed[-1]["time"],
            "lastFundingTime": now,
            "position": 0.0,
            "entryPx": 0.0,
            "entryFee": 0.0,
            "realized": 0.0,
            "fees": 0.0,
            "funding": 0.0,
            "signals": 0,
            "fills": 0,
            "closedTrades": 0,
            "wins": 0,
            "lastPrice": bars[-1]["close"],
            "lastForecast": None,
        }
        with self._lock:
            self._runs[run_id] = run
            self._events[run_id] = deque(maxlen=RECENT_EVENTS)
            self._emit(run, {"type": "start", "time": now, "price": run["startPrice"], "text": "开始前向模拟，从下一根收盘的 K 线起记录信号"})
            self._save()
        return run

    def stop(self, run_id: str) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise ValueError("没有这个前向模拟")
            if run["status"] == "running":
                run["status"] = "stopped"
                run["stoppedAt"] = int(time.time())
                self._emit(run, {"type": "stop", "time": run["stoppedAt"], "text": "已停止", "position": run["position"]})
                self._signals.pop(run_id, None)
                self._save()

    def snapshot(self) -> dict:
        with self._lock:
            runs = []
            for run in sorted(self._runs.values(), key=lambda item: item["startedAt"], reverse=True):
                unrealized = (run["lastPrice"] - run["entryPx"]) * run["position"] if run["position"] else 0.0
                runs.append(
                    {
                        **run,
                        "unrealized": unrealized,
                        "pnl": run["realized"] + unrealized,
                        "events": list(self._events.get(run["id"], []))[-60:],
                    },
                )
            return {"runs": runs, "error": self._error, "store": str(self.root)}

    # Processing

    def loop(self) -> None:
        while True:
            try:
                self.step()
                self._error = ""
            except Exception as exc:
                self._error = str(exc)
            time.sleep(POLL_SECONDS)

    def step(self) -> None:
        with self._lock:
            active = [run for run in self._runs.values() if run["status"] == "running"]
        for run in active:
            self._advance(run)

    def _carver_signal(self, run: dict, closed: list[dict]) -> carver.CarverSignal:
        signal = self._signals.get(run["id"])
        if signal is not None:
            return signal
        lookbacks = carver.lookbacks_for(engine.INTERVALS[run["interval"]])
        signal = carver.CarverSignal(run["qty"], SIZE_STEP, run["side"], lookbacks)
        history = market.research_bars(run["symbol"], run["interval"], market.RESEARCH_BARS[run["interval"]])
        closes = {bar["time"]: bar["close"] for bar in history}
        closes.update({bar["time"]: bar["close"] for bar in closed})
        for moment in sorted(closes):
            if moment <= run["lastBarTime"]:
                signal.update(closes[moment])
        self._signals[run["id"]] = signal
        return signal

    def _advance(self, run: dict) -> None:
        bars = market.load_bars(run["symbol"], run["interval"])
        closed = [bar for bar in bars if bar["closed"]]
        by_time = {bar["time"]: bar for bar in bars}
        bar_seconds = engine.INTERVALS[run["interval"]] * 60
        rates = market.funding_history()
        now = int(time.time())
        signal = self._carver_signal(run, closed) if run["strategy"] == "carver" else None

        with self._lock:
            run["lastPrice"] = bars[-1]["close"]
            for index, bar in enumerate(closed):
                if bar["time"] <= run["lastBarTime"]:
                    continue
                following = by_time.get(bar["time"] + bar_seconds)
                if following is None:
                    break
                if not self._settle_funding(run, bar, bar_seconds, rates, now):
                    break

                if signal is not None:
                    signal.update(bar["close"])
                    wanted = signal.next_position(run["position"])
                    run["lastForecast"] = signal.forecast
                    action = "无操作" if abs(wanted - run["position"]) < 1e-12 else _action(run["position"], wanted)
                    reason = f"预测 {signal.forecast:+.1f}，目标 {signal.target():+.3f}，当前 {run['position']:+.3f}"
                else:
                    wanted, action, reason = _breakout_decision(closed, index, run, run["position"])

                run["signals"] += 1
                self._emit(
                    run,
                    {
                        "type": "signal",
                        "time": bar["time"],
                        "close": bar["close"],
                        "action": action,
                        "text": reason,
                        "position": run["position"],
                    },
                )
                delta = round(wanted - run["position"], 6)
                if abs(delta) >= SIZE_STEP:
                    price = following["open"]
                    fee, pnl = _book_fill(run, delta, price)
                    run["fills"] += 1
                    self._emit(
                        run,
                        {
                            "type": "fill",
                            "time": following["time"],
                            "action": action,
                            "side": "BUY" if delta > 0 else "SELL",
                            "qty": abs(delta),
                            "price": price,
                            "fee": fee,
                            "pnl": pnl,
                            "position": run["position"],
                            "text": reason,
                        },
                    )
                run["lastBarTime"] = bar["time"]
            self._save()

    def _settle_funding(self, run: dict, bar: dict, bar_seconds: int, rates: dict[int, float], now: int) -> bool:
        """Pay funding settled during this bar. False means wait for the rate to publish."""
        period = engine.FUNDING_PERIOD_SECONDS
        moment = (run["lastFundingTime"] // period + 1) * period
        while moment <= bar["time"] + bar_seconds:
            rate = rates.get(moment)
            estimated = False
            if rate is None:
                if now - moment < FUNDING_WAIT_SECONDS:
                    return False
                rate = sum(rates.values()) / len(rates) if rates else 0.0
                estimated = True
            payment = run["position"] * bar["close"] * rate
            run["realized"] -= payment
            run["funding"] += payment
            run["lastFundingTime"] = moment
            if run["position"]:
                self._emit(
                    run,
                    {
                        "type": "funding",
                        "time": moment,
                        "rate": rate,
                        "amount": -payment,
                        "estimated": estimated,
                        "position": run["position"],
                        "text": f"资金费率 {rate * 100:.4f}%{'（估算）' if estimated else ''}",
                    },
                )
            moment += period
        return True
