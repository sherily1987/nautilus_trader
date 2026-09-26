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
  strategy: "breakout",
  tool: "cursor",
  digits: 2,
  live: false,
  backtestNote: "",
  trendStart: null,
  trendCount: 0,
  busy: false,
  bars: [],
  ind: { volma: [], vwap: [], obv: [] },
  timeIndex: new Map(),
  indicators: loadIndicatorPrefs(),
};

function loadIndicatorPrefs() {
  const fallback = { split: true, volma: true, vwap: true, obv: true };
  try {
    const saved = JSON.parse(localStorage.getItem("desk.indicators") || "null");
    return saved ? { ...fallback, ...saved } : fallback;
  } catch (error) {
    return fallback;
  }
}

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
const buyVolume = chart.addHistogramSeries({
  priceFormat: { type: "volume" },
  priceScaleId: "volume",
  priceLineVisible: false,
  lastValueVisible: false,
});
chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.88, bottom: 0 } });

const BUY_COLOR = "rgba(8,153,129,0.8)";
const SELL_COLOR = "rgba(242,54,69,0.8)";

function hasSplit(bar) {
  return bar.buyVol != null && bar.sellVol != null && bar.buyVol + bar.sellVol > 0;
}

function volumePoint(bar) {
  const split = state.indicators.split && hasSplit(bar);
  const color = split ? SELL_COLOR : bar.close >= bar.open ? "rgba(8,153,129,0.45)" : "rgba(242,54,69,0.45)";
  return { time: bar.time, value: bar.volume, color };
}

function buyPoint(bar) {
  if (!state.indicators.split || !hasSplit(bar)) return { time: bar.time };
  const share = bar.buyVol / (bar.buyVol + bar.sellVol);
  return { time: bar.time, value: bar.volume * share, color: BUY_COLOR };
}

function repaintVolume() {
  volume.setData(state.bars.map(volumePoint));
  buyVolume.setData(state.bars.map(buyPoint));
  scheduleSplitLabels();
}

const SPLIT_LABEL_MIN_SPACING = 30;
const SPLIT_LABEL_MIN_HEIGHT = 12;
let splitLabelFrame = 0;

function scheduleSplitLabels() {
  if (splitLabelFrame) return;
  splitLabelFrame = requestAnimationFrame(() => {
    splitLabelFrame = 0;
    renderSplitLabels();
  });
}

function renderSplitLabels() {
  const box = document.getElementById("split-labels");
  const range = chart.timeScale().getVisibleLogicalRange();
  if (!state.indicators.split || !range || state.bars.length < 2) {
    box.innerHTML = "";
    return;
  }
  const timeScale = chart.timeScale();
  const from = Math.max(0, Math.floor(range.from));
  const to = Math.min(state.bars.length - 1, Math.ceil(range.to));
  const a = timeScale.timeToCoordinate(state.bars[Math.max(from, 1) - 1].time);
  const b = timeScale.timeToCoordinate(state.bars[Math.max(from, 1)].time);
  if (a === null || b === null || Math.abs(b - a) < SPLIT_LABEL_MIN_SPACING) {
    box.innerHTML = "";
    return;
  }
  const base = volume.priceToCoordinate(0);
  const parts = [];
  for (let index = from; index <= to; index += 1) {
    const bar = state.bars[index];
    if (!hasSplit(bar)) continue;
    const x = timeScale.timeToCoordinate(bar.time);
    const top = volume.priceToCoordinate(bar.volume);
    if (x === null || top === null || base === null) continue;
    const buyShare = bar.buyVol / (bar.buyVol + bar.sellVol);
    const middle = buyVolume.priceToCoordinate(bar.volume * buyShare);
    if (middle === null) continue;
    const buyPct = Math.round(buyShare * 100);
    const sellPct = 100 - buyPct;
    const inside = base - middle >= SPLIT_LABEL_MIN_HEIGHT && middle - top >= SPLIT_LABEL_MIN_HEIGHT;
    if (inside) {
      parts.push(`<span style="left:${x}px;top:${(middle + base) / 2}px">${buyPct}%</span>`);
      parts.push(`<span style="left:${x}px;top:${(top + middle) / 2}px">${sellPct}%</span>`);
    } else {
      parts.push(`<span class="above down" style="left:${x}px;top:${top - 2}px">${sellPct}%</span>`);
      parts.push(`<span class="above up" style="left:${x}px;top:${top - 13}px">${buyPct}%</span>`);
    }
  }
  box.innerHTML = parts.join("");
}

chart.timeScale().subscribeVisibleLogicalRangeChange(scheduleSplitLabels);
new ResizeObserver(scheduleSplitLabels).observe(chartEl);
const emaFast = chart.addLineSeries({ color: "#f5c451", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
const emaSlow = chart.addLineSeries({ color: "#2962ff", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });

const VOL_MA_PERIOD = 20;
const IND_COLORS = { volma: "#ff9800", vwap: "#e040fb", obv: "#26c6da" };
const indicatorLine = { lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false };
const indicatorSeries = {
  volma: chart.addLineSeries({ ...indicatorLine, color: IND_COLORS.volma, priceScaleId: "volume" }),
  vwap: chart.addLineSeries({ ...indicatorLine, color: IND_COLORS.vwap }),
  obv: chart.addLineSeries({ ...indicatorLine, color: IND_COLORS.obv, priceScaleId: "obv", priceFormat: { type: "volume" } }),
};
chart.priceScale("obv").applyOptions({ scaleMargins: { top: 0.7, bottom: 0.14 } });

function volumeMa(bars, period = VOL_MA_PERIOD) {
  const out = [];
  let sum = 0;
  bars.forEach((bar, index) => {
    sum += bar.volume;
    if (index >= period) sum -= bars[index - period].volume;
    if (index >= period - 1) out.push({ time: bar.time, value: sum / period });
  });
  return out;
}

function dayKey(unix) {
  const date = new Date(unix * 1000);
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
}

function vwap(bars) {
  const out = [];
  let day = null;
  let priceVolume = 0;
  let totalVolume = 0;
  bars.forEach((bar) => {
    const key = dayKey(bar.time);
    if (key !== day) {
      day = key;
      priceVolume = 0;
      totalVolume = 0;
    }
    const typical = (bar.high + bar.low + bar.close) / 3;
    priceVolume += typical * bar.volume;
    totalVolume += bar.volume;
    out.push({ time: bar.time, value: totalVolume > 0 ? priceVolume / totalVolume : typical });
  });
  return out;
}

function obv(bars) {
  const out = [];
  let total = 0;
  bars.forEach((bar, index) => {
    if (index > 0) {
      const previous = bars[index - 1].close;
      if (bar.close > previous) total += bar.volume;
      else if (bar.close < previous) total -= bar.volume;
    }
    out.push({ time: bar.time, value: total });
  });
  return out;
}

function computeIndicators() {
  state.ind = { volma: volumeMa(state.bars), vwap: vwap(state.bars), obv: obv(state.bars) };
  state.timeIndex = new Map(state.bars.map((bar, index) => [bar.time, index]));
}

function layoutIndicators() {
  const on = state.indicators;
  Object.entries(indicatorSeries).forEach(([key, series]) => series.applyOptions({ visible: on[key] }));
  const volumeTop = on.split ? 0.8 : 0.88;
  chart.priceScale("volume").applyOptions({ scaleMargins: { top: volumeTop, bottom: 0 } });
  chart.priceScale("obv").applyOptions({ scaleMargins: { top: volumeTop - 0.18, bottom: 1 - volumeTop + 0.02 } });
  chart.priceScale("right").applyOptions({
    scaleMargins: on.obv ? { top: 0.06, bottom: 1 - volumeTop + 0.22 } : { top: 0.08, bottom: 1 - volumeTop + 0.04 },
  });
  document.querySelectorAll("#indicators button").forEach((button) => {
    button.classList.toggle("active", Boolean(on[button.dataset.ind]));
  });
  scheduleSplitLabels();
}

function paintIndicators() {
  Object.entries(indicatorSeries).forEach(([key, series]) => series.setData(state.ind[key]));
  layoutIndicators();
}

function upsertBar(bar) {
  const last = state.bars[state.bars.length - 1];
  if (last && last.time === bar.time) {
    state.bars[state.bars.length - 1] = bar;
  } else if (!last || bar.time > last.time) {
    state.bars.push(bar);
  } else {
    return;
  }
  computeIndicators();
  Object.entries(indicatorSeries).forEach(([key, series]) => {
    const points = state.ind[key];
    if (points.length) series.update(points[points.length - 1]);
  });
}

function compact(value) {
  const number = Number(value);
  const abs = Math.abs(number);
  if (abs >= 1e9) return `${(number / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${(number / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${(number / 1e3).toFixed(2)}K`;
  return number.toFixed(2);
}

function paintIndicatorLegend(time) {
  const box = document.getElementById("legend-ind");
  if (!state.bars.length) {
    box.innerHTML = "";
    return;
  }
  const found = state.timeIndex.get(time);
  const index = found === undefined ? state.bars.length - 1 : found;
  const bar = state.bars[index];
  const parts = [`<span>VOL ${compact(bar.volume)}</span>`];
  const on = state.indicators;
  if (on.split && hasSplit(bar)) {
    const net = bar.buyVol - bar.sellVol;
    const share = (bar.buyVol / (bar.buyVol + bar.sellVol)) * 100;
    parts.push(
      `<span class="up">多 ${compact(bar.buyVol)}</span>`
      + `<span class="down">空 ${compact(bar.sellVol)}</span>`
      + `<span class="${net >= 0 ? "up" : "down"}">净 ${net >= 0 ? "+" : ""}${compact(net)} · 多占 ${share.toFixed(1)}%</span>`,
    );
  } else if (on.split && state.live) {
    parts.push("<span>多空量：此周期无数据</span>");
  }
  const ma = state.ind.volma[index - (VOL_MA_PERIOD - 1)];
  if (on.volma && ma) parts.push(`<span style="color:${IND_COLORS.volma}">MA${VOL_MA_PERIOD} ${compact(ma.value)}</span>`);
  if (on.vwap && state.ind.vwap[index]) parts.push(`<span style="color:${IND_COLORS.vwap}">VWAP ${fmt(state.ind.vwap[index].value)}</span>`);
  if (on.obv && state.ind.obv[index]) parts.push(`<span style="color:${IND_COLORS.obv}">OBV ${compact(state.ind.obv[index].value)}</span>`);
  box.innerHTML = parts.join("");
}

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
  volume.setData(payload.bars.map(volumePoint));
  buyVolume.setData(payload.bars.map(buyPoint));
  emaFast.setData(payload.emaFast.map((point) => ({ time: point.time, value: point.value })));
  emaSlow.setData(payload.emaSlow.map((point) => ({ time: point.time, value: point.value })));
  candles.setMarkers(payload.markers || []);
  state.bars = payload.bars.slice();
  computeIndicators();
  paintIndicators();
  const last = payload.bars[payload.bars.length - 1];
  const first = payload.bars[Math.max(0, payload.bars.length - 2)];
  paintLegend(last);
  paintIndicatorLegend(last ? last.time : undefined);
  if (payload.live) paintQuote(payload.last, payload.change, payload.changePct);
  else if (last && first) paintQuote(last.close, last.close - first.close, ((last.close - first.close) / first.close) * 100);
  document.getElementById("symbol-code").textContent = payload.symbol;
  document.getElementById("symbol-name").textContent = payload.name;
  document.getElementById("legend-symbol").textContent = `${payload.symbol} · ${document.querySelector(`[data-interval="${state.interval}"]`).textContent}`;
  chart.timeScale().fitContent();
  scheduleSplitLabels();
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
  renderPerformance(payload);
}

let equityChart = null;
let equitySeries = null;

function ensureEquityChart() {
  if (equityChart) return;
  equityChart = LightweightCharts.createChart(document.getElementById("equity-chart"), {
    autoSize: true,
    layout: { background: { type: "solid", color: "#131722" }, textColor: "#787b86", fontSize: 11 },
    grid: { vertLines: { visible: false }, horzLines: { color: "#1f2430" } },
    rightPriceScale: { borderColor: "#2a2e39" },
    timeScale: { borderColor: "#2a2e39", timeVisible: true, secondsVisible: false },
    handleScroll: false,
    handleScale: false,
  });
  equitySeries = equityChart.addBaselineSeries({
    baseValue: { type: "price", price: 0 },
    topLineColor: "#089981",
    topFillColor1: "rgba(8,153,129,0.28)",
    topFillColor2: "rgba(8,153,129,0.02)",
    bottomLineColor: "#f23645",
    bottomFillColor1: "rgba(242,54,69,0.02)",
    bottomFillColor2: "rgba(242,54,69,0.28)",
    lineWidth: 1,
    priceLineVisible: false,
  });
}

function metric(label, value, note = "", tone = "") {
  return `<div><dt>${label}</dt><dd class="${tone}">${value}${note ? ` <small>${note}</small>` : ""}</dd></div>`;
}

function renderPerformance(payload) {
  const perf = payload.performance;
  const grid = document.getElementById("perf-grid");
  if (!perf) {
    grid.innerHTML = metric("绩效", "运行回测后显示");
    return;
  }
  const unit = payload.stats && payload.stats.currency ? payload.stats.currency : "";
  const bars = (payload.bars || []).filter((bar) => bar.closed !== false);
  const days = bars.length > 1 ? (bars[bars.length - 1].time - bars[0].time) / 86400 : 0;
  const tone = (value) => (value == null ? "" : value >= 0 ? "up" : "down");
  const num = (value, digits = 2) => (value == null ? "—" : Number(value).toFixed(digits));
  grid.innerHTML = [
    metric("总盈亏", `${money(perf.curvePnl)} ${unit}`, "", tone(perf.curvePnl)),
    metric("夏普（年化）", num(perf.sharpe), days < 90 ? `样本 ${days.toFixed(0)} 天，偏少` : `样本 ${days.toFixed(0)} 天`),
    metric("最大回撤", `-${fmtQty(Math.round(perf.maxDrawdown))}`, `账户 ${num(perf.maxDrawdownPct)}%`, "down"),
    metric("收益回撤比", num(perf.returnOverDrawdown)),
    metric("盈亏比", num(perf.profitFactor), "总盈利/总亏损"),
    metric("期望值", perf.expectancy == null ? "—" : money(perf.expectancy), "每次平仓", tone(perf.expectancy)),
    metric("平均盈利", perf.avgWin == null ? "—" : money(perf.avgWin), "", "up"),
    metric("平均亏损", perf.avgLoss == null ? "—" : money(perf.avgLoss), "", "down"),
    metric("最长连亏", `${perf.worstStreak} 次`),
    metric(
      "资金费",
      perf.funding == null ? "未计入" : money(-perf.funding),
      perf.funding == null
        ? ""
        : `已计入 · ${perf.fundingActualSettlements}/${perf.fundingSettlements} 次为实际费率${perf.fundingSettlements > perf.fundingActualSettlements ? `，其余按均值估算（${money(-perf.fundingEstimated)}）` : ""}`,
      tone(perf.funding == null ? null : -perf.funding),
    ),
  ].join("");
  ensureEquityChart();
  equitySeries.setData(perf.equityCurve || []);
  equityChart.timeScale().fitContent();
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
    strategy: state.strategy,
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
    volume.update(volumePoint(bar));
    buyVolume.update(buyPoint(bar));
    scheduleSplitLabels();
    if (payload.emaFast) emaFast.update(payload.emaFast);
    if (payload.emaSlow) emaSlow.update(payload.emaSlow);
    upsertBar(bar);
    paintLegend(bar);
    paintIndicatorLegend(bar.time);
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
  paintIndicatorLegend(param.time);
});

document.getElementById("indicators").addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  const key = button.dataset.ind;
  state.indicators[key] = !state.indicators[key];
  localStorage.setItem("desk.indicators", JSON.stringify(state.indicators));
  if (key === "split") repaintVolume();
  layoutIndicators();
  paintIndicatorLegend(undefined);
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

document.getElementById("strategy-toggle").addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  state.strategy = button.dataset.strategy;
  const carverOn = state.strategy === "carver";
  document.querySelectorAll("#strategy-toggle button").forEach((item) => item.classList.toggle("active", item === button));
  document.getElementById("fast").disabled = carverOn;
  document.getElementById("slow").disabled = carverOn;
  document.getElementById("strategy-note").classList.toggle("hidden", !carverOn);
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
  if (button.dataset.tab === "forward") loadForward();
  ["fills", "positions", "perf", "robust", "forward", "logs"].forEach((tab) => {
    document.getElementById(`panel-${tab}`).classList.toggle("hidden", button.dataset.tab !== tab);
  });
});

function day(unix) {
  return stamp(unix).slice(0, 10);
}

function sharpeCell(value) {
  if (value == null) return "background:transparent";
  const strength = Math.min(1, Math.abs(value) / 2);
  const rgb = value >= 0 ? "8,153,129" : "242,54,69";
  return `background:rgba(${rgb},${(0.15 + strength * 0.6).toFixed(2)})`;
}

function renderRobust(result) {
  const chosen = result.chosen;
  const near = result.neighbours;
  const walk = result.walkForward;
  const num = (value) => (value == null ? "—" : Number(value).toFixed(2));
  const oos = walk.oos;
  const fixed = walk.fixed;
  document.getElementById("robust-summary").innerHTML = [
    `样本 <b>${day(result.from)} ~ ${day(result.to)}</b>，${result.bars} 根，方向 ${result.side === "both" ? "多空" : result.side === "long" ? "只做多" : "只做空"}`,
    `当前 ${chosen.fast}/${chosen.slow}：盈亏 <b class="${chosen.pnl >= 0 ? "up" : "down"}">${money(chosen.pnl)}</b>，夏普 <b>${num(chosen.sharpe)}</b>，最大回撤 <b class="down">-${fmtQty(Math.round(chosen.maxDrawdown))}</b>`,
    `邻域：<b>${near.profitable}/${near.count}</b> 组赚钱，中位夏普 <b>${num(near.medianSharpe)}</b>`,
    `滚动检验（训练 ${walk.trainBars} 根 → 检验 ${walk.testBars} 根）：每段重选参数 盈亏 <b class="${oos && oos.pnl >= 0 ? "up" : "down"}">${oos ? money(oos.pnl) : "—"}</b>、夏普 <b>${num(oos && oos.sharpe)}</b>，${walk.positiveWindows}/${walk.windows.length} 段赚钱`,
    `同一时段固定 ${chosen.fast}/${chosen.slow} 不换：盈亏 <b class="${fixed && fixed.pnl >= 0 ? "up" : "down"}">${fixed ? money(fixed.pnl) : "—"}</b>、夏普 <b>${num(fixed && fixed.sharpe)}</b>`,
  ].map((line) => `<div>${line}</div>`).join("");

  const head = `<tr><th>突破\\离场</th>${result.slows.map((slow) => `<th>${slow}</th>`).join("")}</tr>`;
  const rows = result.fasts.map((fast) => {
    const cells = result.slows.map((slow) => {
      const cell = result.cells.find((item) => item.fast === fast && item.slow === slow);
      if (!cell) return "<td>—</td>";
      const mark = fast === chosen.fast && slow === chosen.slow ? "chosen" : "";
      return `<td class="${mark}" style="${sharpeCell(cell.sharpe)}" title="盈亏 ${money(cell.pnl)} · 回撤 ${Math.round(cell.maxDrawdown)}">${num(cell.sharpe)}</td>`;
    }).join("");
    return `<tr><th>${fast}</th>${cells}</tr>`;
  }).join("");
  document.getElementById("robust-grid").innerHTML = `<div class="muted-note">夏普（年化），白框为当前参数</div><table>${head}${rows}</table>`;

  const windowRows = walk.windows.map((item) => `
    <tr>
      <td>${day(item.from)} ~ ${day(item.to)}</td>
      <td>${item.fast}/${item.slow}</td>
      <td>${num(item.trainSharpe)}</td>
      <td class="${item.testPnl >= 0 ? "up" : "down"}">${num(item.testSharpe)}</td>
      <td class="${item.testPnl >= 0 ? "up" : "down"}">${money(item.testPnl)}</td>
    </tr>`).join("");
  document.getElementById("robust-walk").innerHTML = `
    <table>
      <thead><tr><th>检验时段</th><th>选中参数</th><th>训练夏普</th><th>检验夏普</th><th>检验盈亏</th></tr></thead>
      <tbody>${windowRows || `<tr><td class="empty" colspan="5">数据不足</td></tr>`}</tbody>
    </table>`;
}

async function runRobust() {
  const button = document.getElementById("run-robust");
  if (button.disabled) return;
  button.disabled = true;
  const summary = document.getElementById("robust-summary");
  summary.textContent = "正在拉取约一年的历史 K 线并扫描参数，首次约需 20 秒…";
  try {
    const response = await fetch("/api/robust", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params()),
    });
    const payload = await response.json();
    if (!response.ok) {
      summary.textContent = payload.error || "稳健性检查失败";
      return;
    }
    renderRobust(payload);
  } catch (error) {
    summary.textContent = "无法连接桌面服务";
  } finally {
    button.disabled = false;
  }
}

document.getElementById("run-robust").addEventListener("click", runRobust);

const INTERVAL_LABEL = { "1": "1m", "5": "5m", "15": "15m", "60": "1H", "240": "4H" };
const SIDE_LABEL = { both: "多空", long: "只做多", short: "只做空" };
const EVENT_LABEL = { start: "开始", stop: "停止", signal: "信号", fill: "成交", funding: "资金费" };
let forwardSelected = null;
let forwardRuns = [];

function forwardStrategyText(run) {
  return run.strategy === "carver" ? "Carver 趋势" : `突破 ${run.fast}/${run.slow}`;
}

function renderForwardEvents() {
  const run = forwardRuns.find((item) => item.id === forwardSelected) || forwardRuns[0];
  const body = document.getElementById("forward-events");
  const title = document.getElementById("forward-events-title");
  if (!run) {
    title.textContent = "最近事件";
    body.innerHTML = "";
    return;
  }
  title.textContent = `最近事件 · ${forwardStrategyText(run)} · ${INTERVAL_LABEL[run.interval] || run.interval}`;
  body.innerHTML = run.events.slice().reverse().map((event) => {
    let detail = event.text || "";
    if (event.type === "fill") {
      detail = `${fmtQty(event.qty)} @ ${fmt(event.price)}，手续费 ${Number(event.fee).toFixed(2)}${event.pnl == null ? "" : `，本笔 ${money(event.pnl)}`} · ${event.text}`;
    } else if (event.type === "funding") {
      detail = `${event.text}，${money(event.amount)}`;
    } else if (event.type === "signal") {
      detail = `${event.text}`;
    }
    const tone = event.type === "fill" ? (event.side === "BUY" ? "up" : "down") : "";
    return `<tr><td>${stamp(event.time)}</td><td>${EVENT_LABEL[event.type] || event.type}</td><td class="${tone}">${event.action || "—"}</td><td>${detail}</td></tr>`;
  }).join("") || `<tr><td class="empty" colspan="4">等待下一根 K 线收盘</td></tr>`;
}

function renderForward(payload) {
  forwardRuns = payload.runs || [];
  if (!forwardRuns.some((run) => run.id === forwardSelected)) forwardSelected = forwardRuns.length ? forwardRuns[0].id : null;
  const body = document.getElementById("forward-body");
  body.innerHTML = forwardRuns.map((run) => `
    <tr data-run="${run.id}" class="${run.id === forwardSelected ? "selected" : ""}">
      <td>${stamp(run.startedAt)}</td>
      <td>${forwardStrategyText(run)}</td>
      <td>${INTERVAL_LABEL[run.interval] || run.interval}</td>
      <td>${SIDE_LABEL[run.side] || run.side}</td>
      <td>${positionText(run.position)}</td>
      <td class="${run.pnl >= 0 ? "up" : "down"}">${money(run.pnl)}</td>
      <td>${money(-run.funding)}</td>
      <td>${run.signals}</td>
      <td>${run.fills}</td>
      <td>${run.status === "running" ? "运行中" : "已停止"}</td>
      <td>${run.status === "running" ? `<button class="link-button" type="button" data-stop="${run.id}">停止</button>` : ""}</td>
    </tr>`).join("") || `<tr><td class="empty" colspan="11">还没有前向模拟</td></tr>`;
  if (payload.error) document.getElementById("forward-note").textContent = `后台出错：${payload.error}`;
  renderForwardEvents();
}

async function loadForward() {
  try {
    const response = await fetch("/api/forward");
    if (response.ok) renderForward(await response.json());
  } catch (error) {
    document.getElementById("forward-note").textContent = "无法连接桌面服务";
  }
}

async function postForward(path, body) {
  const note = document.getElementById("forward-note");
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json();
  if (!response.ok) {
    note.textContent = payload.error || "操作失败";
    return;
  }
  note.textContent = "只记录模拟信号和模拟成交，不会向交易所下单。从开始后下一根收盘的 K 线起记录。";
  renderForward(payload);
}

document.getElementById("start-forward").addEventListener("click", () => postForward("/api/forward/start", params()));
document.getElementById("forward-body").addEventListener("click", (event) => {
  const stop = event.target.closest("[data-stop]");
  if (stop) {
    postForward("/api/forward/stop", { id: stop.dataset.stop });
    return;
  }
  const row = event.target.closest("tr[data-run]");
  if (!row) return;
  forwardSelected = row.dataset.run;
  document.querySelectorAll("#forward-body tr").forEach((item) => item.classList.toggle("selected", item === row));
  renderForwardEvents();
});
setInterval(() => {
  if (!document.getElementById("panel-forward").classList.contains("hidden")) loadForward();
}, 10000);
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
