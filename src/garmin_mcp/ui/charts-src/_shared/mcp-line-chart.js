// Shared logic for the multi-day trend line charts (HRV, sleep, heart rate,
// VO2 max, respiration, training load). Each chart HTML file imports this
// and supplies only its own title/series/header formatting — everything
// generic about parsing the tool result, wiring the App lifecycle, and
// drawing an SVG line chart lives here so it isn't duplicated per file.
import { App, PostMessageTransport, applyDocumentTheme, applyHostStyleVariables, applyHostFonts } from "@modelcontextprotocol/ext-apps";

function extractPayload(result) {
  if (!result) return null;
  // FastMCP wraps a plain-string tool return as { result: "<json>" } in
  // structuredContent, since structuredContent must itself be an object.
  if (typeof result === "object" && !Array.isArray(result) && typeof result.result === "string") {
    try {
      return JSON.parse(result.result);
    } catch {
      return null;
    }
  }
  return result;
}

export function parseToolResult(result) {
  if (!result) return null;
  if (result.structuredContent) {
    const parsed = extractPayload(result.structuredContent);
    if (parsed) return parsed;
  }
  const textBlock = (result.content || []).find((c) => c.type === "text");
  if (!textBlock) return null;
  try {
    return JSON.parse(textBlock.text);
  } catch {
    return null;
  }
}

export function formatDateLabel(dateStr) {
  const d = new Date(`${dateStr}T00:00:00`);
  if (Number.isNaN(d.getTime())) return dateStr;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/**
 * Build an SVG line chart from a trend array for one or more series.
 * `series` is [{ key, color }]. Returns null if no series has any usable
 * data points. Only draws individual dots when there are few enough points
 * to avoid clutter on long (e.g. 2-year) ranges.
 */
export function buildLineChartSVG(trend, series, { maxDots = 60 } = {}) {
  const points = trend
    .map((e) => {
      const values = {};
      for (const s of series) {
        const v = Number(e[s.key]);
        if (Number.isFinite(v)) values[s.key] = v;
      }
      return { date: e.date, values };
    })
    .filter((p) => p.date && Object.keys(p.values).length);

  const allValues = points.flatMap((p) => Object.values(p.values));
  if (!points.length || !allValues.length) return null;

  const minV = Math.min(...allValues);
  const maxV = Math.max(...allValues, minV + 1);
  const W = 600, H = 160, padX = 6, padY = 10;
  const innerW = W - padX * 2;
  const innerH = H - padY * 2;

  const xFor = (i) => (points.length === 1 ? padX + innerW / 2 : padX + (i / (points.length - 1)) * innerW);
  const yFor = (v) => padY + innerH - ((v - minV) / (maxV - minV)) * innerH;

  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("class", "line-chart");
  svg.setAttribute("preserveAspectRatio", "none");

  for (const s of series) {
    const coords = [];
    points.forEach((p, i) => {
      if (s.key in p.values) coords.push({ x: xFor(i), y: yFor(p.values[s.key]) });
    });
    if (!coords.length) continue;

    const d = coords.map((c, i) => `${i === 0 ? "M" : "L"} ${c.x.toFixed(1)} ${c.y.toFixed(1)}`).join(" ");
    const path = document.createElementNS(svgNS, "path");
    path.setAttribute("d", d);
    path.setAttribute("class", "line-path");
    path.style.stroke = s.color;
    svg.appendChild(path);

    if (coords.length <= maxDots) {
      coords.forEach((c) => {
        const dot = document.createElementNS(svgNS, "circle");
        dot.setAttribute("cx", c.x.toFixed(1));
        dot.setAttribute("cy", c.y.toFixed(1));
        dot.setAttribute("r", "2.5");
        dot.setAttribute("class", "line-dot");
        dot.style.fill = s.color;
        svg.appendChild(dot);
      });
    }
  }

  const wrapper = document.createElement("div");
  wrapper.className = "chart-wrapper";
  wrapper.appendChild(svg);

  const labelRow = document.createElement("div");
  labelRow.className = "x-labels";
  const labelCount = Math.min(5, points.length);
  for (let i = 0; i < labelCount; i++) {
    const idx = labelCount === 1 ? 0 : Math.round((i / (labelCount - 1)) * (points.length - 1));
    const label = document.createElement("span");
    label.textContent = formatDateLabel(points[idx].date);
    labelRow.appendChild(label);
  }
  wrapper.appendChild(labelRow);

  return wrapper;
}

/**
 * Build a small color-dot + label legend row, for multi-series charts where
 * it's not obvious from color alone which line is which.
 */
export function buildLegend(series) {
  const row = document.createElement("div");
  row.className = "legend";
  for (const s of series) {
    const item = document.createElement("span");
    item.className = "legend-item";
    const dot = document.createElement("span");
    dot.className = "legend-dot";
    dot.style.background = s.color;
    item.appendChild(dot);
    item.appendChild(document.createTextNode(s.label));
    row.appendChild(item);
  }
  return row;
}

/**
 * Wire up the standard App lifecycle (input/result/host-context/teardown)
 * for a trend chart. `render(payload)` should parse the tool result and
 * update the DOM; errors/empty states are the chart's own responsibility.
 */
export function createTrendChartApp({ name, contentEl, render }) {
  const app = new App({ name, version: "0.1.0" });

  app.ontoolinput = () => {
    contentEl.className = "empty";
    contentEl.textContent = "Loading…";
  };

  app.ontoolresult = (result) => {
    render(parseToolResult(result));
  };

  app.onhostcontextchanged = (ctx) => {
    if (ctx.theme) applyDocumentTheme(ctx.theme);
    if (ctx.styles?.variables) applyHostStyleVariables(ctx.styles.variables);
    if (ctx.styles?.css?.fonts) applyHostFonts(ctx.styles.css.fonts);
    if (ctx.safeAreaInsets) {
      const { top, right, bottom, left } = ctx.safeAreaInsets;
      document.body.style.padding = `${top ?? 16}px ${right ?? 16}px ${bottom ?? 16}px ${left ?? 16}px`;
    }
  };

  app.onteardown = async () => ({});

  return app;
}

export async function connectTrendChartApp(app, renderError) {
  try {
    await app.connect(new PostMessageTransport());
  } catch (err) {
    renderError(`Failed to connect to host: ${err?.message || err}`);
  }
}
