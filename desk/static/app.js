const quotes = new Map();

function useSymbolQty(symbol) {
  const item = quotes.get(symbol);
  if (!item) return;
  const input = document.getElementById("qty");
  input.value = item.defaultQty;
  input.step = String(item.qtyStep);
  input.min = String(item.minQty);
}

function fmtQty(value) {
  const number = Number(value);
  if (Math.abs(number) < 100) {
    return number.toLocaleString("en-US", { maximumFractionDigits: 6 });
  }
  return number.toLocaleString("en-US");
}

const state = {
  symbol: "BTCUSDT",
  interval: "240",
  side: "both",
  tool: "cursor",
  digits: 2,
  live: false,
  backtestNote: "",
  trendStart: null,
  trendCount: 0,
  busy: false,
};

const chartEl = document.getElementById("chart");
const chart = LightweightCharts.createChart(chartEl, {
  autoSize: true,
  layout: {
    background: { type: "solid", color: "#131722" },
    textColor: "#d1d4dc",
    fontSize: 12,
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Trebuchet MS', Roboto, Ubuntu, sans-serif",
  },
  grid: {
    vertLines: { color: "#1f2430" },
    horzLines: { color: "#1f2430" },
  },
  crosshair: {
    mode: LightweightCharts.CrosshairMode.Normal,
    vertLine: { color: "#758696", width: 1, labelBackgroundColor: "#2a2e39" },
    horzLine: { color: "#758696", width: 1, labelBackgroundColor: "#2a2e39" },
  },
  rightPriceScale: { borderColor: "#2a2e39" },
  timeScale: { borderColor: "#2a2e39", timeVisible: true, secondsVisible: false, rightOffset: 6 },
});

const candles = chart.addCandlestickSeries({
  upColor: "#089981",
  downColor: "#f23645",
  borderUpColor: "#089981",
  borderDownColor: "#f23645",
  wickUpColor: "#089981",
  wickDownColor: "#f23645",
});
const volume = chart.addHistogramSeries({
  priceFormat: { type: "volume" },
  priceScaleId: "volume",
});
chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.88, bottom: 0 } });
const emaFast = chart.addLineSeries({ color: "#f5c451", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
const emaSlow = chart.addLineSeries({ color: "#2962ff", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });

const priceLines = [];
const trendSeries = [];

function fmt(value, digits = state.digits) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return Number(value).toFixed(digits);
}

function money(value) {
  const number = Number(value);
  const sign = number > 0 ? "+" : "";
  return `${sign}${number.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function positionText(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  const number = Number(value);
  if (Math.abs(number) < 1e-8) return "空仓";
  return `${number > 0 ? "多" : "空"} ${fmtQty(Math.abs(number))}`;
}

function stamp(unix) {
  const date = new Date(unix * 1000);
  const pad = (part) => String(part).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function setStatus(text) {
  document.getElementById("status").textContent = text;
}

function paintQuote(last, change, changePct) {
  const price = document.getElementById("last-price");
  const delta = document.getElementById("last-change");
  const up = change >= 0;
  price.textContent = fmt(last);
  price.className = up ? "up" : "down";
  delta.textContent = `${up ? "+" : ""}${fmt(change)}   ${up ? "+" : ""}${Number(changePct).toFixed(2)}%`;
  delta.className = up ? "up" : "down";
}

function paintLegend(bar) {
  if (!bar) return;
  const up = bar.close >= bar.open;
  document.getElementById("legend-ohlc").innerHTML =
    `<span class="${up ? "up" : "down"}">开 ${fmt(bar.open)}  高 ${fmt(bar.high)}  低 ${fmt(bar.low)}  收 ${fmt(bar.close)}</span>`;
}

function applyChart(payload) {
  state.digits = payload.digits;
  candles.setData(payload.bars);
  volume.setData(payload.bars.map((bar) => ({
    time: bar.time,
    value: bar.volume,
    color: bar.close >= bar.open ? "rgba(8,153,129,0.45)" : "rgba(242,54,69,0.45)",
  })));
  emaFast.setData(payload.emaFast.map((point) => ({ time: point.time, value: point.value })));
  emaSlow.setData(payload.emaSlow.map((point) => ({ time: point.time, value: point.value })));
  candles.setMarkers(payload.markers || []);
  const last = payload.bars[payload.bars.length - 1];
  const first = payload.bars[Math.max(0, payload.bars.length - 2)];
  paintLegend(last);
  if (payload.live) paintQuote(payload.last, payload.change, payload.changePct);
  else if (last && first) paintQuote(last.close, last.close - first.close, ((last.close - first.close) / first.close) * 100);
  document.getElementById("symbol-code").textContent = payload.symbol;
  document.getElementById("symbol-name").textContent = payload.name;
  document.getElementById("legend-symbol").textContent = `${payload.symbol} · ${document.querySelector(`[data-interval="${state.interval}"]`).textContent}`;
  chart.timeScale().fitContent();
}

function renderTables(payload) {
  const fills = document.getElementById("fills-body");
  const positions = document.getElementById("positions-body");
  const logs = document.getElementById("log-list");
  fills.innerHTML = (payload.fills || []).slice().reverse().map((fill) => `
    <tr>
      <td>${stamp(fill.time)}</td>
      <td class="${fill.side === "BUY" ? "up" : "down"}">${fill.action || (fill.side === "BUY" ? "买入" : "卖出")}</td>
      <td>${fill.reason || "—"}</td>
      <td>${fill.orderType || "市价"} · ${fill.status || "已成交"}</td>
      <td>${fill.price == null ? "下一根开盘" : fmt(fill.price)}</td>
      <td>${fmtQty(fill.qty)}</td>
      <td>${fill.notional == null ? "—" : fmt(fill.notional)}</td>
      <td>${fill.commission == null ? "—" : Number(fill.commission).toFixed(2)}</td>
      <td class="${fill.pnl == null ? "" : fill.pnl >= 0 ? "up" : "down"}">${fill.pnl == null ? "—" : money(fill.pnl)}</td>
      <td>${positionText(fill.position)}</td>
    </tr>`).join("") || `<tr><td class="empty" colspan="10">还没有模拟订单</td></tr>`;
  positions.innerHTML = (payload.positions || []).slice().reverse().map((row) => `
    <tr>
      <td class="${row.side === "多" ? "up" : "down"}">${row.side}</td>
      <td>${fmtQty(row.qty)}</td>
      <td>${fmt(row.open)}</td>
      <td>${row.close === null ? "—" : fmt(row.close)}</td>
      <td class="${row.pnl >= 0 ? "up" : "down"}">${money(row.pnl)}</td>
      <td>${row.status}</td>
    </tr>`).join("") || `<tr><td class="empty" colspan="6">还没有持仓</td></tr>`;
  logs.innerHTML = (payload.logs || []).slice().reverse().map((line) => `<li>${line}</li>`).join("") || "<li>等待回测</li>";
  if (!payload.stats) return;
  const unit = payload.stats.currency && payload.stats.currency !== "USD" ? ` ${payload.stats.currency}` : "";
  document.getElementById("stat-equity").textContent = money(payload.stats.equity) + unit;
  const pnl = document.getElementById("stat-pnl");
  pnl.textContent = money(payload.stats.pnl) + unit;
  pnl.className = payload.stats.pnl >= 0 ? "up" : "down";
  document.getElementById("stat-win").textContent = payload.stats.winRate === null
    ? "—"
    : `${payload.stats.winRate.toFixed(1)}% (${payload.stats.wins}/${payload.stats.closed})`;
  document.getElementById("stat-trades").textContent = String(payload.stats.trades);
}

async function loadWatchlist() {
  const response = await fetch("/api/watchlist");
  const payload = await response.json();
  quotes.clear();
  payload.symbols.forEach((item) => quotes.set(item.symbol, item));
  const box = document.getElementById("watchlist");
  box.innerHTML = payload.symbols.map((item) => `
    <button class="watch-row ${item.symbol === state.symbol ? "active" : ""}" type="button" data-symbol="${item.symbol}">
      <b>${item.symbol}</b>
      <small>${item.name}${item.live ? " · 实时" : ""}</small>
      <em class="${item.change >= 0 ? "up" : "down"}">${item.change >= 0 ? "+" : ""}${item.changePct.toFixed(2)}%</em>
    </button>`).join("");
  box.querySelectorAll(".watch-row").forEach((button) => {
    button.addEventListener("click", () => {
      state.symbol = button.dataset.symbol;
      useSymbolQty(state.symbol);
      loadChart().then(followChart);
    });
  });
}

function params() {
  return {
    symbol: state.symbol,
    interval: state.interval,
    fast: Number(document.getElementById("fast").value),
    slow: Number(document.getElementById("slow").value),
    qty: Number(document.getElementById("qty").value),
    side: state.side,
  };
}

async function loadChart() {
  const query = new URLSearchParams({
    symbol: state.symbol,
    interval: state.interval,
    fast: document.getElementById("fast").value,
    slow: document.getElementById("slow").value,
  });
  state.backtestNote = "";
  setStatus("加载行情");
  const response = await fetch(`/api/bars?${query}`);
  const payload = await response.json();
  if (!response.ok) {
    setStatus(payload.error || "行情加载失败");
    return;
  }
  applyChart(payload);
  state.live = Boolean(payload.live);
  document.querySelectorAll(".watch-row").forEach((row) => {
    row.classList.toggle("active", row.dataset.symbol === state.symbol);
  });
  if (state.live) {
    setStatus(`实时 · ${payload.venue || "OKX"} · 仅看盘`);
    startLive();
  } else {
    stopLive();
    setStatus("本地模拟");
  }
}

function followChart() {
  if (!state.live) return runBacktest();
  return undefined;
}

let liveTimer = null;

function stopLive() {
  if (liveTimer) {
    clearInterval(liveTimer);
    liveTimer = null;
  }
}

async function pollLive() {
  if (!state.live) return;
  const query = new URLSearchParams({
    symbol: state.symbol,
    interval: state.interval,
    fast: document.getElementById("fast").value,
    slow: document.getElementById("slow").value,
  });
  try {
    const response = await fetch(`/api/live?${query}`);
    const payload = await response.json();
    if (!response.ok || !payload.bar) {
      setStatus(payload.error || "实时行情中断");
      return;
    }
    const bar = payload.bar;
    candles.update(bar);
    volume.update({
      time: bar.time,
      value: bar.volume,
      color: bar.close >= bar.open ? "rgba(8,153,129,0.45)" : "rgba(242,54,69,0.45)",
    });
    if (payload.emaFast) emaFast.update(payload.emaFast);
    if (payload.emaSlow) emaSlow.update(payload.emaSlow);
    paintLegend(bar);
    paintQuote(payload.last, payload.change, payload.changePct);
    const row = document.querySelector(`.watch-row[data-symbol="${state.symbol}"] em`);
    if (row) {
      const up = payload.change >= 0;
      row.textContent = `${up ? "+" : ""}${Number(payload.changePct).toFixed(2)}%`;
      row.className = up ? "up" : "down";
    }
    setStatus(state.backtestNote || `实时 · ${payload.venue || "OKX"} · 仅看盘`);
  } catch (error) {
    setStatus("实时行情中断");
  }
}

function startLive() {
  stopLive();
  liveTimer = setInterval(pollLive, 1000);
}

async function runBacktest() {
  if (state.busy) return;
  state.busy = true;
  const button = document.getElementById("run");
  button.disabled = true;
  setStatus("回测运行中");
  try {
    const response = await fetch("/api/backtest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params()),
    });
    const payload = await response.json();
    if (!response.ok) {
      setStatus(payload.error || "回测失败");
      return;
    }
    applyChart(payload);
    renderTables(payload);
    const count = payload.stats ? payload.stats.trades : 0;
    if (payload.live) {
      state.live = true;
      startLive();
      state.backtestNote = `回测完成 · ${count} 笔成交 · 行情仍为实时`;
      setStatus(state.backtestNote);
    } else {
      setStatus(`回测完成 · ${count} 笔成交`);
    }
  } catch (error) {
    setStatus("无法连接桌面服务");
  } finally {
    state.busy = false;
    button.disabled = false;
  }
}

chart.subscribeCrosshairMove((param) => {
  if (!param.time) return;
  const bar = param.seriesData.get(candles);
  if (bar) paintLegend(bar);
});

chart.subscribeClick((param) => {
  if (!param.point || state.tool === "cursor" || state.tool === "cross") return;
  const price = candles.coordinateToPrice(param.point.y);
  if (price === null || param.time === undefined) return;
  if (state.tool === "hline") {
    priceLines.push(candles.createPriceLine({
      price,
      color: "#787b86",
      lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true,
      title: fmt(price),
    }));
    return;
  }
  if (state.tool === "trend") {
    if (!state.trendStart) {
      state.trendStart = { time: param.time, value: price };
      return;
    }
    const series = chart.addLineSeries({
      color: "#d1d4dc",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    const start = state.trendStart;
    const end = { time: param.time, value: price };
    series.setData(start.time <= end.time ? [start, end] : [end, start]);
    trendSeries.push(series);
    state.trendStart = null;
    state.trendCount += 1;
  }
});

document.getElementById("intervals").addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  state.interval = button.dataset.interval;
  document.querySelectorAll("#intervals button").forEach((item) => item.classList.toggle("active", item === button));
  loadChart().then(followChart);
});

document.getElementById("tools").addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  if (button.dataset.tool === "clear") {
    priceLines.splice(0).forEach((line) => candles.removePriceLine(line));
    trendSeries.splice(0).forEach((series) => chart.removeSeries(series));
    state.trendStart = null;
    return;
  }
  state.tool = button.dataset.tool;
  document.querySelectorAll("#tools button").forEach((item) => item.classList.toggle("active", item === button));
  chart.applyOptions({
    crosshair: {
      mode: state.tool === "cross"
        ? LightweightCharts.CrosshairMode.Normal
        : LightweightCharts.CrosshairMode.Magnet,
    },
  });
});

document.getElementById("side-toggle").addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  state.side = button.dataset.side;
  document.querySelectorAll("#side-toggle button").forEach((item) => item.classList.toggle("active", item === button));
});

document.getElementById("tabs").addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  document.querySelectorAll("#tabs button").forEach((item) => item.classList.toggle("active", item === button));
  document.getElementById("panel-fills").classList.toggle("hidden", button.dataset.tab !== "fills");
  document.getElementById("panel-positions").classList.toggle("hidden", button.dataset.tab !== "positions");
  document.getElementById("panel-logs").classList.toggle("hidden", button.dataset.tab !== "logs");
});

document.getElementById("run").addEventListener("click", runBacktest);
document.getElementById("symbol-btn").addEventListener("click", () => {
  document.querySelector(".watch-row.active")?.scrollIntoView({ block: "nearest" });
});

async function boot() {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    try {
      const response = await fetch("/api/health");
      const payload = await response.json();
      if (payload.ready) break;
      setStatus(payload.error || "引擎启动中");
    } catch (error) {
      setStatus("等待桌面服务");
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  state.symbol = "BTCUSDT";
  await loadWatchlist();
  useSymbolQty(state.symbol);
  await loadChart();
  await followChart();
}

boot();
