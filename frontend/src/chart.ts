import { paddedRange, initialRange } from "./ranges";
import { availableIndicators } from "./indicator-values";
import {
  createChart,
  CandlestickSeries,
  LineSeries,
  HistogramSeries,
  createSeriesMarkers,
  ColorType,
  LineStyle,
  CrosshairMode,
  type UTCTimestamp,
  type IChartApi,
  type ISeriesApi,
  type Time,
  type SeriesMarker,
} from "lightweight-charts";

type Range = { from: number; to: number };
type Candle = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};
type Trade = {
  id: string;
  number: number;
  entry_time: string;
  exit_time: string | null;
  direction: string;
};
type Marker = {
  time: number;
  trade_id: string;
  label: string;
  timestamp: string;
  price: number;
  quantity: number;
};
type Payload = {
  version: number;
  run_id: string;
  symbol: string;
  mode: "price" | "performance";
  timezone: string;
  candles: Candle[];
  indicators: ({ time: number } & Record<string, number | undefined>)[];
  indicator_definitions?: {
    key: string;
    label: string;
    pane: "price" | "atr";
  }[];
  markers: Marker[];
  trades: Trade[];
  stops: {
    start: string;
    end: string;
    price: number;
    entry_order_id: string;
  }[];
  performance: {
    time: number;
    equity: number;
    pnl: number;
    drawdown: number;
  }[];
  events: { timestamp: string; reason: string }[];
  halt_limit: number;
  selected: string | null;
  command?: { id: string; kind: string; trade_id?: string };
  linked_range?: Range | null;
};
type Settings = {
  volume: boolean;
  atr: boolean;
  sma: boolean;
  stops: boolean;
  markers: boolean;
  auto: boolean;
  line: boolean;
  drawdown: boolean;
  metric: "equity" | "pnl";
};
type Stored = { range?: Range; settings?: Settings; command?: string };
type Args = {
  data: Payload;
  parentElement: HTMLElement | ShadowRoot;
  setStateValue: (key: string, value: unknown) => void;
};
const timestamp = (value: string) => Date.parse(value) / 1000;
const time = (value: number) => value as UTCTimestamp;
const green = "#089981",
  red = "#ef5350",
  blue = "#2962ff";

export default function render({ data, parentElement, setStateValue }: Args) {
  if (![1, 2].includes(data.version))
    throw new Error("Unsupported chart data version");
  const indicatorDefinitions = data.indicator_definitions || [
    { key: "fast", label: "Fast SMA", pane: "price" },
    { key: "slow", label: "Slow SMA", pane: "price" },
    { key: "atr", label: "ATR", pane: "atr" },
  ];
  type ChartRoot = HTMLElement & { dispose?: () => void };
  const previous = parentElement.querySelector<ChartRoot>(".workspace");
  previous?.dispose?.();
  previous?.remove();
  const root = document.createElement("section") as ChartRoot;
  root.className = "workspace";
  parentElement.append(root);
  const storageKey = `mnq:${data.run_id}:${data.mode}`;
  let stored: Stored = {};
  try {
    stored = JSON.parse(sessionStorage.getItem(storageKey) || "{}");
  } catch {
    /* New session. */
  }
  const settings: Settings = {
    volume: false,
    atr: false,
    sma: true,
    stops: true,
    markers: true,
    auto: true,
    line: false,
    drawdown: true,
    metric: "equity",
    ...stored.settings,
  };
  let chart: IChartApi;
  let priceSeries: ISeriesApi<"Candlestick"> | ISeriesApi<"Line">;
  let ready = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let resize: ResizeObserver;
  let seriesToClean: (() => void)[] = [];
  let currentRange: Range | undefined = data.linked_range || stored.range;
  const dt = new Intl.DateTimeFormat("en-GB", {
    timeZone: data.timezone,
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: "short",
    hour12: false,
  });
  const format = (t: number) => dt.format(new Date(t * 1000));
  function element<K extends keyof HTMLElementTagNameMap>(
    tag: K,
    text = "",
    owner: HTMLElement = root,
  ) {
    const el = document.createElement(tag);
    el.textContent = text;
    owner.append(el);
    return el;
  }
  const heading = element(
    "div",
    `${data.symbol} · 1h · ${data.timezone} · ${data.mode === "price" ? "Price & strategy" : "Performance"}`,
  );
  heading.className = "chart-heading";
  const toolbar = element("div");
  toolbar.className = "toolbar";
  const controls = element("div");
  controls.className = "toolbar";
  const tooltip = element("div", "Move the crosshair to inspect a bar.");
  tooltip.className = "readout";
  const canvas = element("div");
  canvas.className = "chart";
  const rangeLabel = element("div");
  rangeLabel.className = "range";
  rangeLabel.setAttribute("aria-live", "polite");
  const tradePicker = element("div");
  tradePicker.className = "toolbar";
  const exportPreview = element("details");
  exportPreview.hidden = true;
  const footer = element("div");
  footer.className = "footer";
  const attribution = element("a", "Charts by TradingView", footer);
  attribution.href = "https://www.tradingview.com/";
  attribution.target = "_blank";
  attribution.rel = "noopener";
  element(
    "span",
    " · TradingView Lightweight Charts™ · Copyright (с) 2025 TradingView, Inc. · Hourly simulation.",
    footer,
  );
  function save() {
    stored = { range: currentRange, settings, command: stored.command };
    sessionStorage.setItem(storageKey, JSON.stringify(stored));
  }
  function button(label: string, action: () => void, owner = toolbar) {
    const b = element("button", label, owner);
    b.type = "button";
    b.onclick = action;
    return b;
  }
  function toggle(label: string, key: keyof Settings) {
    const labelEl = element("label", "", controls);
    const input = element("input", "", labelEl);
    input.dataset.setting = key;
    input.type = "checkbox";
    input.checked = Boolean(settings[key]);
    input.setAttribute("aria-label", label);
    element("span", label, labelEl);
    input.onchange = () => {
      (settings as unknown as Record<string, unknown>)[key] = input.checked;
      save();
      build();
    };
  }
  const candles = data.candles;
  let lastReported = currentRange ? JSON.stringify(currentRange) : "";
  const first = candles[0].time,
    last = candles[candles.length - 1].time;
  function padded(start: number, end: number): Range {
    return paddedRange(
      candles.map((c) => c.time),
      start,
      end,
    );
  }
  function initial(): Range {
    if (data.mode === "performance")
      return {
        from: data.performance[0].time,
        to: data.performance.at(-1)!.time,
      };
    return initialRange(
      candles.map((c) => c.time),
      data.markers.map((m) => m.time),
    );
  }

  function setRange(range: Range) {
    currentRange = range;
    chart.timeScale().setVisibleRange({
      from: time(range.from),
      to: time(Math.max(range.from + 1, range.to)),
    });
    save();
  }
  function focusTrade(id: string, notify = true) {
    const t = data.trades.find((t) => t.id === id);
    if (!t) return;
    setRange(
      padded(
        timestamp(t.entry_time),
        t.exit_time ? timestamp(t.exit_time) : last,
      ),
    );
    if (notify) setStateValue("selection", id);
  }
  for (const [label, days] of [
    ["1D", 1],
    ["1W", 7],
    ["1M", 30],
    ["3M", 90],
    ["6M", 180],
    ["1Y", 365],
  ] as const) {
    button(label, () =>
      setRange({ from: Math.max(first, last - days * 86400), to: last }),
    );
  }
  button("All", () => {
    chart.timeScale().fitContent();
  });
  button("Activity", () => {
    const old = data.mode;
    data.mode = "price";
    const r = initial();
    data.mode = old;
    setRange(r);
  });
  button("−", () => zoom(1.4));
  button("+", () => zoom(1 / 1.4));
  button("Reset", () => {
    settings.auto = true;
    save();
    build();
    setRange(initial());
  });
  button("Selected trade", () => {
    if (data.selected) focusTrade(data.selected, false);
  }).disabled = !data.selected;
  const halt = data.events.find(
    (e) => e.reason === "drawdown_halt" || e.reason === "order_rejected",
  );
  button("Go to halt", () => {
    if (halt)
      setRange(padded(timestamp(halt.timestamp), timestamp(halt.timestamp)));
  }).disabled = !halt;
  const date = element("input", "", toolbar);
  date.type = "date";
  date.setAttribute("aria-label", "Go to date");
  button("Go", () => {
    const localDate = new Intl.DateTimeFormat("en-CA", {
      timeZone: data.timezone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
    const found = candles.find(
      (c) => localDate.format(new Date(c.time * 1000)) >= date.value,
    );
    if (found && date.value) setRange(padded(found.time, found.time));
  });
  button("Fullscreen", () => {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void root.requestFullscreen();
  });
  button("PNG", () => {
    const source = chart.takeScreenshot();
    const output = document.createElement("canvas");
    output.width = source.width;
    output.height = source.height + 55;
    const ctx = output.getContext("2d")!;
    ctx.fillStyle = getComputedStyle(root).backgroundColor;
    ctx.fillRect(0, 0, output.width, output.height);
    ctx.fillStyle = getComputedStyle(root).color;
    ctx.font = "14px sans-serif";
    ctx.fillText(
      `${data.symbol} · 1h · ${data.timezone} · ${data.run_id}`,
      12,
      22,
    );
    ctx.fillText("Charts by TradingView · Research simulation", 12, 44);
    ctx.drawImage(source, 0, 55);
    // Keep a visible fallback for embedded browsers that do not start downloads.
    exportPreview.replaceChildren();
    exportPreview.hidden = false;
    element("summary", "PNG ready — preview or save image", exportPreview);
    const a = element("a", "Download chart PNG", exportPreview);
    a.download = `${data.run_id}-${data.mode}.png`;
    a.href = output.toDataURL();
    const preview = element("img", "", exportPreview);
    preview.src = a.href;
    preview.alt = `Exported ${data.symbol} chart · ${data.run_id} · ${data.timezone}`;
    preview.style.cssText = "display:block;max-width:100%;margin-top:8px";
    exportPreview.open = true;
    a.click();
  });
  if (data.mode === "price") {
    toggle("Close line", "line");
    toggle("Strategy averages", "sma");
    toggle("Stops", "stops");
    toggle("Executions", "markers");
    toggle("Volume", "volume");
    toggle("ATR", "atr");
  } else {
    const select = element("select", "", controls);
    select.setAttribute("aria-label", "Performance metric");
    for (const [value, label] of [
      ["equity", "Account equity"],
      ["pnl", "Net P&L"],
    ]) {
      const o = element("option", label, select);
      o.value = value;
    }
    select.value = settings.metric;
    select.onchange = () => {
      settings.metric = select.value as Settings["metric"];
      save();
      build();
    };
    toggle("Drawdown", "drawdown");
  }
  toggle("Auto price scale", "auto");
  function zoom(factor: number) {
    const r = chart.timeScale().getVisibleLogicalRange();
    if (!r) return;
    const center = (r.from + r.to) / 2,
      half = ((r.to - r.from) * factor) / 2;
    chart
      .timeScale()
      .setVisibleLogicalRange({ from: center - half, to: center + half });
  }
  function build() {
    controls
      .querySelectorAll<HTMLInputElement>("input[data-setting]")
      .forEach((input) => {
        input.checked = Boolean(
          settings[input.dataset.setting as keyof Settings],
        );
      });
    ready = false;
    clearTimeout(timer);
    resize?.disconnect();
    seriesToClean.forEach((fn) => fn());
    seriesToClean = [];
    chart?.remove();
    canvas.replaceChildren();
    const style = getComputedStyle(root),
      background = style.backgroundColor,
      foreground = style.color;
    const height =
      data.mode === "price"
        ? 420 + (settings.volume ? 110 : 0) + (settings.atr ? 110 : 0)
        : 300 + (settings.drawdown ? 120 : 0);
    canvas.style.height = `${height}px`;
    chart = createChart(canvas, {
      height,
      width: Math.max(280, canvas.clientWidth),
      layout: {
        background: { type: ColorType.Solid, color: background },
        textColor: foreground,
        attributionLogo: true,
        panes: { enableResize: true },
      },
      grid: {
        vertLines: { color: "rgba(128,128,128,.12)" },
        horzLines: { color: "rgba(128,128,128,.12)" },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { autoScale: settings.auto },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 3,
        minBarSpacing: 0.01,
        tickMarkFormatter: (t: Time, kind: number) =>
          new Intl.DateTimeFormat("en-GB", {
            timeZone: data.timezone,
            ...(kind >= 3
              ? { hour: "2-digit", minute: "2-digit", hour12: false }
              : { day: "2-digit", month: "short" }),
          }).format(new Date(Number(t) * 1000)),
      },
      localization: { timeFormatter: (t: Time) => format(Number(t)) },
    });
    if (data.mode === "price") {
      if (settings.line) {
        priceSeries = chart.addSeries(LineSeries, {
          color: blue,
          priceFormat: { type: "price", precision: 2, minMove: 0.25 },
        });
        priceSeries.setData(
          candles.map((c) => ({ time: time(c.time), value: c.close })),
        );
      } else {
        priceSeries = chart.addSeries(CandlestickSeries, {
          upColor: green,
          downColor: red,
          borderVisible: false,
          wickUpColor: green,
          wickDownColor: red,
          priceFormat: { type: "price", precision: 2, minMove: 0.25 },
        });
        priceSeries.setData(candles.map((c) => ({ ...c, time: time(c.time) })));
      }
      if (settings.sma) {
        const palette = ["#00bcd4", "#ab47bc", "#2962ff", "#ff9800"];
        for (const [index, definition] of indicatorDefinitions
          .filter((d) => d.pane === "price")
          .entries()) {
          const key = definition.key;
          const series = chart.addSeries(LineSeries, {
            color: palette[index % palette.length],
            lineWidth: 2,
            priceLineVisible: false,
            lastValueVisible: false,
            title: definition.label,
          });
          series.setData(
            data.indicators
              .filter((p) => p[key] !== undefined)
              .map((p) => ({ time: time(p.time), value: p[key]! })),
          );
        }
      }
      if (settings.stops) {
        for (const stop of data.stops) {
          const start = timestamp(stop.start),
            end = timestamp(stop.end);
          const series = chart.addSeries(LineSeries, {
            color: "#ff9800",
            lineStyle: LineStyle.Dashed,
            lineWidth: 1,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
            pointMarkersVisible: true,
          });
          series.setData(
            (start === end ? [start] : [start, end]).map((t) => ({
              time: time(t),
              value: stop.price,
            })),
          );
        }
      }
      let pane = 1;
      if (settings.volume) {
        const v = chart.addSeries(
          HistogramSeries,
          { priceFormat: { type: "volume" }, priceLineVisible: false },
          pane++,
        );
        v.setData(
          candles.map((c) => ({
            time: time(c.time),
            value: c.volume,
            color: c.close >= c.open ? green + "80" : red + "80",
          })),
        );
        chart.panes()[pane - 1].setHeight(110);
      }
      if (settings.atr) {
        const a = chart.addSeries(
          LineSeries,
          { color: "#ff9800", title: "ATR", priceLineVisible: false },
          pane++,
        );
        a.setData(
          data.indicators
            .filter((p) => p.atr !== undefined)
            .map((p) => ({ time: time(p.time), value: p.atr! })),
        );
        chart.panes()[pane - 1].setHeight(110);
      }
      const markers: SeriesMarker<UTCTimestamp>[] = settings.markers
        ? data.markers.map((m, i) => ({
            time: time(m.time),
            id: `fill:${i}`,
            text: `${data.trades.find((t) => t.id === m.trade_id)?.number} ${m.label}`,
            position: m.label === "Long entry" ? "belowBar" : "aboveBar",
            shape:
              m.label === "Exit"
                ? "circle"
                : m.label === "Long entry"
                  ? "arrowUp"
                  : "arrowDown",
            color:
              m.trade_id === data.selected
                ? "#ff9800"
                : m.label === "Exit"
                  ? "#8e24aa"
                  : m.label === "Long entry"
                    ? green
                    : red,
          }))
        : [];
      if (halt) {
        const index = candles.findIndex(
          (c) => c.time >= timestamp(halt.timestamp),
        );
        markers.push({
          time: time(candles[index < 0 ? candles.length - 1 : index].time),
          text: "HALT",
          position: "aboveBar",
          shape: "square",
          color: "#ff9800",
          id: "halt",
        });
      }
      markers.sort((a, b) => Number(a.time) - Number(b.time));
      const plugin = createSeriesMarkers(priceSeries, markers);
      seriesToClean.push(() => plugin.detach());
    } else {
      const s = chart.addSeries(LineSeries, {
        color: settings.metric === "pnl" ? green : blue,
        title: settings.metric === "pnl" ? "Net P&L (USD)" : "Equity (USD)",
        priceFormat: { type: "price", precision: 2, minMove: 0.01 },
      });
      s.setData(
        data.performance.map((p) => ({
          time: time(p.time),
          value: p[settings.metric],
        })),
      );
      if (settings.drawdown) {
        const dd = chart.addSeries(
          LineSeries,
          { color: red, title: "Drawdown %", priceLineVisible: false },
          1,
        );
        dd.setData(
          data.performance.map((p) => ({
            time: time(p.time),
            value: p.drawdown,
          })),
        );
        dd.createPriceLine({
          price: data.halt_limit,
          color: "#ff9800",
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          title: "Halt limit",
        });
        chart.panes()[1].setHeight(120);
      }
      if (halt) {
        const marker = createSeriesMarkers(s, [
          {
            time: time(timestamp(halt.timestamp)),
            position: "aboveBar",
            shape: "square",
            color: "#ff9800",
            text: "HALT",
          },
        ]);
        seriesToClean.push(() => marker.detach());
      }
    }
    chart.subscribeCrosshairMove((param) => {
      if (!param.time) return;
      const t = Number(param.time),
        c = candles.find((c) => c.time === t);
      const indicator = availableIndicators(data.indicators, t);
      const matches = data.markers.filter((m) => m.time === t);
      let message = format(t);
      if (c)
        message += ` · O ${c.open.toFixed(2)} H ${c.high.toFixed(2)} L ${c.low.toFixed(2)} C ${c.close.toFixed(2)} · Vol ${c.volume.toLocaleString()}`;
      if (indicator)
        message +=
          " · " +
          indicatorDefinitions
            .map(
              (definition) =>
                `${definition.label} ${indicator[definition.key]?.toFixed(2) ?? "—"}`,
            )
            .join(" · ") +
          " (last completed bar)";
      if (data.mode === "performance") {
        const p = data.performance.find((p) => p.time === t);
        if (p)
          message += ` · Equity $${p.equity.toFixed(2)} · P&L $${p.pnl.toFixed(2)} · DD ${p.drawdown.toFixed(2)}%`;
      }
      tooltip.textContent =
        message +
        matches
          .map(
            (m) => ` | ${m.label} ${m.quantity} @ ${m.price} (${m.timestamp})`,
          )
          .join("");
    });
    chart.subscribeClick((param) => {
      if (data.mode !== "price" || !param.time) return;
      const object = String(param.hoveredObjectId || "");
      if (object.startsWith("fill:")) {
        const m = data.markers[Number(object.slice(5))];
        if (m) focusTrade(m.trade_id);
        return;
      }
      const matches = data.markers.filter((m) => m.time === Number(param.time));
      tradePicker.replaceChildren();
      const ids = [...new Set(matches.map((m) => m.trade_id))];
      if (ids.length === 1) focusTrade(ids[0]);
      else
        for (const id of ids) {
          const trade = data.trades.find((t) => t.id === id)!;
          button(
            `Trade ${trade.number} · ${trade.direction}`,
            () => focusTrade(id),
            tradePicker,
          );
        }
    });
    chart.timeScale().setVisibleRange({
      from: time((currentRange || initial()).from),
      to: time((currentRange || initial()).to),
    });
    const updateRange = () => {
      const r = chart.timeScale().getVisibleRange();
      if (!r) return;
      currentRange = { from: Number(r.from), to: Number(r.to) };
      rangeLabel.textContent = `Visible: ${format(currentRange.from)} → ${format(currentRange.to)}`;
      save();
      if (!ready) return;
      clearTimeout(timer);
      timer = setTimeout(() => {
        const signature = JSON.stringify(currentRange);
        if (signature !== lastReported) {
          lastReported = signature;
          setStateValue("viewport", currentRange);
        }
      }, 450);
    };
    chart.timeScale().subscribeVisibleTimeRangeChange(updateRange);
    updateRange();
    ready = true;
    resize = new ResizeObserver(() => {
      if (canvas.clientWidth < 1) return;
      const range = currentRange;
      ready = false;
      chart.applyOptions({ width: Math.max(280, canvas.clientWidth) });
      if (range)
        chart
          .timeScale()
          .setVisibleRange({ from: time(range.from), to: time(range.to) });
      ready = true;
    });
    resize.observe(canvas);
  }
  build();
  if (data.command && stored.command !== data.command.id) {
    stored.command = data.command.id;
    if (data.command.kind === "trade" && data.command.trade_id)
      focusTrade(data.command.trade_id, false);
    if (data.command.kind === "halt" && halt)
      setRange(padded(timestamp(halt.timestamp), timestamp(halt.timestamp)));
    save();
  }
  let disposed = false;
  root.dispose = () => {
    if (disposed) return;
    disposed = true;
    clearTimeout(timer);
    resize?.disconnect();
    seriesToClean.forEach((fn) => fn());
    chart.remove();
    root.remove();
  };
  return root.dispose;
}
