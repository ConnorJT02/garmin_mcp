// Pure, transport-agnostic chart-drawing helpers for multi-day trend line
// charts. No MCP/App imports here on purpose — this file is loaded both by
// the Vite-bundled MCP chart widgets (via mcp-line-chart.js) and directly as
// a plain ES module by the local dashboard (src/garmin_mcp/dashboard), which
// has no bundler and can't resolve bare npm specifiers like
// "@modelcontextprotocol/ext-apps".

let _gradientCounter = 0;

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
 *
 * When `interactive` is true, adds a full-width hover layer (crosshair +
 * one "active dot" per series, shown regardless of `maxDots` since long
 * ranges draw no static dots at all) and calls `onHover({ point, index,
 * clientX, clientY })` on mousemove/touchmove, or `onHover(null)` on
 * mouseleave/touchend. Also calls `onClick({ point, index, clientX,
 * clientY })` on click/touchend, if provided, so callers can let a click on
 * the chart select that date elsewhere on the page. Defaults to false/
 * undefined so existing callers (the MCP Apps chart widgets) are completely
 * unaffected.
 */
export function buildLineChartSVG(trend, series, { maxDots = 60, interactive = false, onHover, onClick } = {}) {
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

  // Gradient area fill under the line, single-series charts only — with two
  // or more series (e.g. training load's CTL/ATL) overlapping fills just
  // look muddy, so those stay stroke-only with a legend instead.
  const fillArea = series.length === 1;
  let defs = null;

  const activeDots = new Map();

  for (const s of series) {
    const coords = [];
    points.forEach((p, i) => {
      if (s.key in p.values) coords.push({ x: xFor(i), y: yFor(p.values[s.key]) });
    });
    if (!coords.length) continue;

    const d = coords.map((c, i) => `${i === 0 ? "M" : "L"} ${c.x.toFixed(1)} ${c.y.toFixed(1)}`).join(" ");

    if (fillArea) {
      if (!defs) {
        defs = document.createElementNS(svgNS, "defs");
        svg.appendChild(defs);
      }
      const gradientId = `chart-fill-${++_gradientCounter}`;
      const gradient = document.createElementNS(svgNS, "linearGradient");
      gradient.setAttribute("id", gradientId);
      gradient.setAttribute("x1", "0");
      gradient.setAttribute("y1", "0");
      gradient.setAttribute("x2", "0");
      gradient.setAttribute("y2", "1");
      const stopTop = document.createElementNS(svgNS, "stop");
      stopTop.setAttribute("offset", "0%");
      stopTop.setAttribute("stop-color", s.color);
      stopTop.setAttribute("stop-opacity", "0.30");
      const stopBottom = document.createElementNS(svgNS, "stop");
      stopBottom.setAttribute("offset", "100%");
      stopBottom.setAttribute("stop-color", s.color);
      stopBottom.setAttribute("stop-opacity", "0");
      gradient.appendChild(stopTop);
      gradient.appendChild(stopBottom);
      defs.appendChild(gradient);

      const baseline = (H - padY).toFixed(1);
      const areaD = `${d} L ${coords[coords.length - 1].x.toFixed(1)} ${baseline} `
        + `L ${coords[0].x.toFixed(1)} ${baseline} Z`;
      const areaPath = document.createElementNS(svgNS, "path");
      areaPath.setAttribute("d", areaD);
      areaPath.setAttribute("fill", `url(#${gradientId})`);
      areaPath.setAttribute("stroke", "none");
      svg.appendChild(areaPath);
    }

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

    if (interactive) {
      const activeDot = document.createElementNS(svgNS, "circle");
      activeDot.setAttribute("r", "3.5");
      activeDot.setAttribute("class", "line-active-dot");
      activeDot.style.fill = s.color;
      activeDot.style.opacity = "0";
      svg.appendChild(activeDot);
      activeDots.set(s.key, activeDot);
    }
  }

  let crosshair = null;
  if (interactive) {
    crosshair = document.createElementNS(svgNS, "line");
    crosshair.setAttribute("class", "chart-crosshair");
    crosshair.setAttribute("y1", "0");
    crosshair.setAttribute("y2", String(H));
    crosshair.style.opacity = "0";
    svg.appendChild(crosshair);
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

  if (interactive) {
    const hitRect = document.createElementNS(svgNS, "rect");
    hitRect.setAttribute("x", "0");
    hitRect.setAttribute("y", "0");
    hitRect.setAttribute("width", String(W));
    hitRect.setAttribute("height", String(H));
    hitRect.setAttribute("class", "chart-hit-rect");
    hitRect.style.fill = "transparent";
    hitRect.style.pointerEvents = "all";
    svg.appendChild(hitRect);

    const showAt = (clientX, clientY) => {
      const rect = svg.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      const fracX = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
      const svgX = fracX * W;
      const rawIdx = points.length === 1 ? 0 : ((svgX - padX) / innerW) * (points.length - 1);
      const idx = Math.min(points.length - 1, Math.max(0, Math.round(rawIdx)));
      const point = points[idx];
      const px = xFor(idx);

      crosshair.setAttribute("x1", px.toFixed(1));
      crosshair.setAttribute("x2", px.toFixed(1));
      crosshair.style.opacity = "1";

      for (const s of series) {
        const dot = activeDots.get(s.key);
        if (!dot) continue;
        if (s.key in point.values) {
          dot.setAttribute("cx", px.toFixed(1));
          dot.setAttribute("cy", yFor(point.values[s.key]).toFixed(1));
          dot.style.opacity = "1";
        } else {
          dot.style.opacity = "0";
        }
      }

      if (onHover) onHover({ point, index: idx, clientX, clientY });
      return { point, index: idx, clientX, clientY };
    };

    const hide = () => {
      crosshair.style.opacity = "0";
      activeDots.forEach((dot) => {
        dot.style.opacity = "0";
      });
      if (onHover) onHover(null);
    };

    hitRect.addEventListener("mousemove", (evt) => showAt(evt.clientX, evt.clientY));
    hitRect.addEventListener("mouseleave", hide);
    hitRect.addEventListener(
      "touchmove",
      (evt) => {
        if (evt.touches && evt.touches[0]) {
          showAt(evt.touches[0].clientX, evt.touches[0].clientY);
          evt.preventDefault();
        }
      },
      { passive: false }
    );
    hitRect.addEventListener("touchend", hide);

    if (onClick) {
      hitRect.addEventListener("click", (evt) => {
        const hit = showAt(evt.clientX, evt.clientY);
        if (hit) onClick(hit);
      });
    }
  }

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
 * Generic hover tooltip: appends one floating `<div class="chart-tooltip">`
 * to `container` (which must be `position: relative`) and returns
 * `{ el, show(xPx, yPx, html), hide() }`. `xPx`/`yPx` are pixel coordinates
 * relative to `container`'s own bounding box. Position is clamped so the
 * tooltip never overflows the container's edges. Not chart-type-specific —
 * used by both the line-chart hover layer and the dashboard's activity
 * splits bar chart, so tooltip positioning logic isn't duplicated.
 */
export function createHoverTooltip(container) {
  const el = document.createElement("div");
  el.className = "chart-tooltip";
  el.style.display = "none";
  container.appendChild(el);

  function show(xPx, yPx, html) {
    el.innerHTML = html;
    el.style.display = "block";

    const containerRect = container.getBoundingClientRect();
    const tooltipRect = el.getBoundingClientRect();

    let left = xPx + 12;
    if (left + tooltipRect.width > containerRect.width) {
      left = xPx - tooltipRect.width - 12;
    }
    left = Math.max(0, Math.min(left, Math.max(0, containerRect.width - tooltipRect.width)));

    let top = yPx - tooltipRect.height - 12;
    if (top < 0) top = yPx + 12;
    top = Math.max(0, Math.min(top, Math.max(0, containerRect.height - tooltipRect.height)));

    el.style.left = `${left}px`;
    el.style.top = `${top}px`;
  }

  function hide() {
    el.style.display = "none";
  }

  return { el, show, hide };
}
