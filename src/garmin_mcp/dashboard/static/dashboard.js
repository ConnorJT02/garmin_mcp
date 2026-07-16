import { buildLineChartSVG, buildLegend, formatDateLabel, createHoverTooltip } from "/shared/chart-draw.js";

function formatSeriesValue(v) {
  return Number.isInteger(v) ? String(v) : v.toFixed(1);
}

// Per-metric config: series/score definitions mirror each metric's existing
// MCP Apps chart (ui/charts-src/<id>_chart.html) so the dashboard reads the
// same JSON shape the same way.
const TREND_METRICS = [
  {
    id: "hrv_trend",
    label: "HRV",
    icon: "💓",
    api: "/api/hrv_trend",
    series: [{ key: "last_night_avg_hrv_ms", color: "#4263eb" }],
    score: (p) => (p.period_avg_hrv_ms != null ? `Avg ${p.period_avg_hrv_ms}ms` : ""),
    emptyMessage: "No HRV trend data available for this range.",
  },
  {
    id: "sleep_trend",
    label: "Sleep",
    icon: "😴",
    api: "/api/sleep_trend",
    series: [{ key: "sleep_score", color: "#7048e8" }],
    score: (p) => {
      const parts = [];
      if (p.period_avg_sleep_score != null) parts.push(`Avg score ${p.period_avg_sleep_score}`);
      if (p.period_avg_sleep_hours != null) parts.push(`Avg ${p.period_avg_sleep_hours}h`);
      return parts.join(" · ");
    },
    emptyMessage: "No sleep trend data available for this range.",
    hasStages: true,
  },
  {
    id: "heart_rate_trend",
    label: "Heart Rate",
    icon: "❤️",
    api: "/api/heart_rate_trend",
    series: [{ key: "resting_heart_rate_bpm", color: "#fa5252" }],
    score: (p) =>
      p.period_avg_resting_heart_rate_bpm != null
        ? `Avg resting ${p.period_avg_resting_heart_rate_bpm} bpm`
        : "",
    emptyMessage: "No heart rate trend data available for this range.",
  },
  {
    id: "vo2max_trend",
    label: "VO2 Max",
    icon: "🏃",
    api: "/api/vo2max_trend",
    series: [{ key: "vo2_max", color: "#40c057" }],
    score: (p) => {
      const parts = [];
      if (p.first_vo2_max != null) parts.push(`First ${p.first_vo2_max}`);
      if (p.latest_vo2_max != null) parts.push(`Latest ${p.latest_vo2_max}`);
      if (p.change != null) parts.push(`Change ${p.change > 0 ? "+" : ""}${p.change}`);
      return parts.join(" · ");
    },
    emptyMessage: "No VO2 max trend data available for this range.",
  },
  {
    id: "respiration_trend",
    label: "Respiration",
    icon: "🫁",
    api: "/api/respiration_trend",
    series: [{ key: "avg_sleep_breaths_per_min", color: "#15aabf" }],
    score: (p) =>
      p.period_avg_sleep_breaths_per_min != null
        ? `Avg sleep ${p.period_avg_sleep_breaths_per_min} br/min`
        : "",
    emptyMessage: "No respiration trend data available for this range.",
  },
  {
    id: "training_load_trend",
    label: "Training Load",
    icon: "💪",
    api: "/api/training_load_trend",
    series: [
      { key: "ctl", color: "#4263eb", label: "Fitness (CTL)" },
      { key: "atl", color: "#fd7e14", label: "Fatigue (ATL)" },
    ],
    score: (p) => {
      const trend = p.trend;
      if (!trend || !trend.length) return "";
      const latest = trend[trend.length - 1];
      return latest.tsb != null ? `Latest TSB ${latest.tsb}` : "";
    },
    emptyMessage: "No training load trend data available for this range.",
  },
  {
    id: "body_composition_trend",
    label: "Body Composition",
    icon: "⚖️",
    api: "/api/body_composition_trend",
    series: [{ key: "weight_kg", color: "#be4bdb" }],
    score: (p) => (p.period_avg_weight_kg != null ? `Avg ${p.period_avg_weight_kg} kg` : ""),
    emptyMessage: "No body composition measurements available for this range.",
  },
];

const navList = document.getElementById("nav-list");
const panelOverview = document.getElementById("panel-overview");
const panelTrend = document.getElementById("panel-trend");
const panelSplits = document.getElementById("panel-splits");
const panelChat = document.getElementById("panel-chat");
const panelPlan = document.getElementById("panel-plan");
const panelChallenges = document.getElementById("panel-challenges");
const trendTitle = document.getElementById("trend-title");
const trendScore = document.getElementById("trend-score");
const trendForm = document.getElementById("trend-form");
const trendStart = document.getElementById("trend-start");
const trendEnd = document.getElementById("trend-end");
const trendLegend = document.getElementById("trend-legend");
const trendContent = document.getElementById("trend-content");
const trendInsight = document.getElementById("trend-insight");

const sleepStagesSection = document.getElementById("sleep-stages-section");
const sleepStagesDate = document.getElementById("sleep-stages-date");
const sleepStagesContent = document.getElementById("sleep-stages-content");

const overviewForm = document.getElementById("overview-form");
const overviewStart = document.getElementById("overview-start");
const overviewEnd = document.getElementById("overview-end");
const overviewGrid = document.getElementById("overview-grid");
const overviewInsight = document.getElementById("overview-insight");

const splitsScore = document.getElementById("splits-score");
const splitsActivitySelect = document.getElementById("splits-activity");
const splitsContent = document.getElementById("splits-content");

let currentMetric = null;
let splitsLoaded = false;
let overviewBuilt = false;
const overviewCards = new Map(); // metric.id -> { contentEl, scoreEl, legendEl }
const navButtonsByMetricId = new Map(); // metric.id -> nav <button>

function goToMetric(metric) {
  const button = navButtonsByMetricId.get(metric.id);
  if (button) setActiveNavButton(button);
  showTrend(metric);
}

function todayISO() {
  return new Date().toISOString().slice(0, 10);
}

function daysAgoISO(days) {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

function renderError(el, message) {
  el.className = "error";
  el.textContent = message;
}

// --- Claude-generated insights -----------------------------------------------
// Fired in parallel with the chart-loading calls (not chained after them) so
// the chart isn't held up waiting on the LLM response.

// Appends `text` to `container` as text nodes, promoting **bold** spans to
// real <strong> elements. Built from DOM nodes rather than innerHTML so
// LLM-generated text is never parsed as markup, even accidentally.
function appendInlineFormatted(container, text) {
  const parts = text.split(/\*\*(.+?)\*\*/g);
  parts.forEach((part, i) => {
    if (!part) return;
    if (i % 2 === 1) {
      const strong = document.createElement("strong");
      strong.textContent = part;
      container.appendChild(strong);
    } else {
      container.appendChild(document.createTextNode(part));
    }
  });
}

// Shared by the insight boxes and the chat bubbles: splits `text` on blank
// lines into paragraphs, promoting a block of consecutive "- " lines into a
// <ul>. Appends real DOM nodes (via appendInlineFormatted) rather than using
// innerHTML, so LLM-generated text is never parsed as markup.
function appendFormattedBlocks(container, text) {
  const blocks = text
    .split(/\n\s*\n/)
    .map((b) => b.trim())
    .filter(Boolean);

  for (const block of blocks) {
    const lines = block.split("\n").map((l) => l.trim()).filter(Boolean);
    if (lines.length && lines.every((l) => l.startsWith("- "))) {
      const ul = document.createElement("ul");
      lines.forEach((line) => {
        const li = document.createElement("li");
        appendInlineFormatted(li, line.slice(2));
        ul.appendChild(li);
      });
      container.appendChild(ul);
    } else {
      const p = document.createElement("p");
      appendInlineFormatted(p, lines.join(" "));
      container.appendChild(p);
    }
  }
}

function renderInsightText(el, text) {
  el.className = "insight-box";
  el.replaceChildren();

  const textWrapper = document.createElement("div");
  textWrapper.className = "insight-text";
  appendFormattedBlocks(textWrapper, text);
  el.appendChild(textWrapper);
}

async function loadInsight(url, el) {
  el.className = "insight-box empty";
  el.textContent = "Generating insights…";
  try {
    const res = await fetch(url);
    const payload = await res.json();
    if (payload.error) {
      el.className = "insight-box error";
      el.textContent = payload.error;
      return;
    }
    if (!payload.insight) {
      el.className = "insight-box error";
      el.textContent = "No insight returned.";
      return;
    }
    renderInsightText(el, payload.insight);
  } catch (err) {
    el.className = "insight-box error";
    el.textContent = `Failed to load insight: ${err?.message || err}`;
  }
}

function loadMetricInsight(metric, startDate, endDate) {
  const params = new URLSearchParams({ metric: metric.id, start_date: startDate, end_date: endDate });
  return loadInsight(`/api/insights/metric?${params}`, trendInsight);
}

function loadOverviewInsight(startDate, endDate) {
  const params = new URLSearchParams({ start_date: startDate, end_date: endDate });
  return loadInsight(`/api/insights/overview?${params}`, overviewInsight);
}

// --- Trend metrics -----------------------------------------------------------
// renderTrendCard/loadTrendCard take explicit target elements so the same
// chart-building + tooltip wiring serves both the single-metric detail view
// and the "All Metrics" overview grid, instead of duplicating it per view.

function renderTrendCard(metric, payload, { contentEl, scoreEl, legendEl }, onPointClick) {
  legendEl.replaceChildren();
  if (payload && payload.error) {
    renderError(contentEl, payload.error);
    return;
  }
  const trend = payload && Array.isArray(payload.trend) ? payload.trend : null;
  if (!trend || !trend.length) {
    renderError(contentEl, metric.emptyMessage);
    return;
  }
  let tooltip = null;
  const chart = buildLineChartSVG(trend, metric.series, {
    interactive: true,
    onHover: (hover) => {
      if (!tooltip) return;
      if (!hover) {
        tooltip.hide();
        return;
      }
      const chartRect = chart.getBoundingClientRect();
      const xPx = hover.clientX - chartRect.left;
      const yPx = hover.clientY - chartRect.top;
      const lines = metric.series
        .filter((s) => s.key in hover.point.values)
        .map(
          (s) =>
            `<div>${s.label || metric.label}: <strong>${formatSeriesValue(hover.point.values[s.key])}</strong></div>`
        )
        .join("");
      tooltip.show(xPx, yPx, `<div class="tooltip-date">${formatDateLabel(hover.point.date)}</div>${lines}`);
    },
    onClick: onPointClick ? (hit) => onPointClick(hit.point.date) : undefined,
  });
  if (!chart) {
    renderError(contentEl, metric.emptyMessage);
    return;
  }
  tooltip = createHoverTooltip(chart);

  if (metric.series.length > 1) {
    legendEl.appendChild(buildLegend(metric.series));
  }
  scoreEl.textContent = metric.score(payload) || "";
  contentEl.className = "chart-content";
  if (onPointClick) contentEl.title = "Click a point to view that night's sleep stages below";
  contentEl.replaceChildren(chart);
}

async function loadTrendCard(metric, startDate, endDate, els, onPointClick) {
  els.contentEl.className = "chart-content empty";
  els.contentEl.textContent = "Loading…";
  els.scoreEl.textContent = "";
  const params = new URLSearchParams({ start_date: startDate, end_date: endDate });
  try {
    const res = await fetch(`${metric.api}?${params}`);
    const payload = await res.json();
    renderTrendCard(metric, payload, els, onPointClick);
  } catch (err) {
    renderError(els.contentEl, `Failed to load: ${err?.message || err}`);
  }
}

// --- Sleep stages (single-night breakdown, shown under the Sleep trend chart) --

const SLEEP_STAGES = [
  { key: "deep_sleep_seconds", label: "Deep", color: "#3b5bdb" },
  { key: "light_sleep_seconds", label: "Light", color: "#748ffc" },
  { key: "rem_sleep_seconds", label: "REM", color: "#a5b4fc" },
  { key: "awake_seconds", label: "Awake", color: "#f08c00" },
];

function formatStageDuration(seconds) {
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  return mins === 0 ? `${hours}h` : `${hours}h ${mins}m`;
}

function renderSleepStages(payload) {
  if (payload && payload.error) {
    renderError(sleepStagesContent, payload.error);
    return;
  }
  const values = SLEEP_STAGES.map((s) => Math.max(0, Number(payload && payload[s.key]) || 0));
  if (values.every((v) => v === 0)) {
    renderError(sleepStagesContent, "No sleep stage data available for this date.");
    return;
  }
  const maxSeconds = Math.max(...values, 1);

  const bars = document.createElement("div");
  bars.className = "bars";
  const tooltip = createHoverTooltip(bars);

  SLEEP_STAGES.forEach((stage, i) => {
    const seconds = values[i];
    const heightPct = Math.max(4, Math.round((seconds / maxSeconds) * 100));

    const col = document.createElement("div");
    col.className = "bar-col";

    const valueEl = document.createElement("div");
    valueEl.className = "bar-value";
    valueEl.textContent = seconds > 0 ? formatStageDuration(seconds) : "-";

    const bar = document.createElement("div");
    bar.className = "bar";
    bar.style.height = `${heightPct}%`;
    bar.style.background = stage.color;

    const label = document.createElement("div");
    label.className = "bar-label";
    label.textContent = stage.label;

    col.appendChild(valueEl);
    col.appendChild(bar);
    col.appendChild(label);

    col.addEventListener("mousemove", (evt) => {
      const barsRect = bars.getBoundingClientRect();
      tooltip.show(
        evt.clientX - barsRect.left,
        evt.clientY - barsRect.top,
        `<div>${stage.label}: <strong>${formatStageDuration(seconds)}</strong></div>`
      );
    });
    col.addEventListener("mouseleave", () => tooltip.hide());

    bars.appendChild(col);
  });

  sleepStagesContent.className = "chart-content";
  sleepStagesContent.replaceChildren(bars);
}

async function loadSleepStages(date) {
  sleepStagesContent.className = "chart-content empty";
  sleepStagesContent.textContent = "Loading…";
  try {
    const res = await fetch(`/api/sleep_stages?date=${encodeURIComponent(date)}`);
    const payload = await res.json();
    renderSleepStages(payload);
  } catch (err) {
    renderError(sleepStagesContent, `Failed to load: ${err?.message || err}`);
  }
}

sleepStagesDate.addEventListener("change", () => {
  if (sleepStagesDate.value) loadSleepStages(sleepStagesDate.value);
});

function loadTrend(metric) {
  loadMetricInsight(metric, trendStart.value, trendEnd.value);

  if (metric.hasStages) {
    sleepStagesSection.hidden = false;
    if (!sleepStagesDate.value) sleepStagesDate.value = trendEnd.value;
    loadSleepStages(sleepStagesDate.value);
  } else {
    sleepStagesSection.hidden = true;
  }

  const onPointClick = metric.hasStages
    ? (date) => {
        sleepStagesDate.value = date;
        loadSleepStages(date);
      }
    : undefined;

  return loadTrendCard(
    metric,
    trendStart.value,
    trendEnd.value,
    { contentEl: trendContent, scoreEl: trendScore, legendEl: trendLegend },
    onPointClick
  );
}

function showTrend(metric) {
  currentMetric = metric;
  panelOverview.hidden = true;
  panelSplits.hidden = true;
  panelChat.hidden = true;
  panelPlan.hidden = true;
  panelChallenges.hidden = true;
  panelTrend.hidden = false;
  panelTrend.style.setProperty("--accent", metric.series[0].color);
  trendTitle.textContent = `${metric.icon} ${metric.label}`;
  if (!trendStart.value) trendStart.value = daysAgoISO(90);
  if (!trendEnd.value) trendEnd.value = todayISO();
  loadTrend(metric);
}

trendForm.addEventListener("submit", (e) => {
  e.preventDefault();
  if (currentMetric) loadTrend(currentMetric);
});

// --- Overview (all metrics, shared date range, 2-column grid) ---------------

function buildOverviewGrid() {
  TREND_METRICS.forEach((metric) => {
    const card = document.createElement("article");
    card.className = "chart-card";
    card.title = "Double-click to open this chart full-size";
    card.style.setProperty("--accent", metric.series[0].color);
    card.addEventListener("dblclick", () => goToMetric(metric));

    const header = document.createElement("div");
    header.className = "card-header";
    const title = document.createElement("h3");
    title.textContent = `${metric.icon} ${metric.label}`;
    const scoreEl = document.createElement("span");
    scoreEl.className = "score";
    header.appendChild(title);
    header.appendChild(scoreEl);

    const legendEl = document.createElement("div");
    legendEl.className = "card-legend";

    const contentEl = document.createElement("div");
    contentEl.className = "chart-content empty";
    contentEl.textContent = "Loading…";

    card.appendChild(header);
    card.appendChild(legendEl);
    card.appendChild(contentEl);
    overviewGrid.appendChild(card);

    overviewCards.set(metric.id, { contentEl, scoreEl, legendEl });
  });
}

function loadOverview() {
  const startDate = overviewStart.value;
  const endDate = overviewEnd.value;
  loadOverviewInsight(startDate, endDate);
  TREND_METRICS.forEach((metric) => {
    loadTrendCard(metric, startDate, endDate, overviewCards.get(metric.id));
  });
}

overviewForm.addEventListener("submit", (e) => {
  e.preventDefault();
  loadOverview();
});

function showOverview() {
  currentMetric = null;
  panelTrend.hidden = true;
  panelSplits.hidden = true;
  panelChat.hidden = true;
  panelPlan.hidden = true;
  panelChallenges.hidden = true;
  panelOverview.hidden = false;
  if (!overviewStart.value) overviewStart.value = daysAgoISO(90);
  if (!overviewEnd.value) overviewEnd.value = todayISO();
  if (!overviewBuilt) {
    overviewBuilt = true;
    buildOverviewGrid();
  }
  loadOverview();
}

// --- Activity splits ---------------------------------------------------------

function formatPace(secPerKm) {
  if (secPerKm == null || !isFinite(secPerKm) || secPerKm <= 0) return null;
  const m = Math.floor(secPerKm / 60);
  const s = Math.round(secPerKm % 60);
  return `${m}:${String(s).padStart(2, "0")}/km`;
}

function formatDuration(sec) {
  if (sec == null || !isFinite(sec) || sec <= 0) return null;
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function barTooltipHtml(entry, usePace) {
  const parts = [`<div class="tooltip-date">Lap ${entry.lapNumber}</div>`];
  const primary = usePace ? formatPace(entry.pace) : formatDuration(entry.duration);
  parts.push(`<div>${usePace ? "Pace" : "Duration"}: <strong>${primary}</strong></div>`);
  if (entry.hr != null) parts.push(`<div>Avg HR: <strong>${Math.round(entry.hr)} bpm</strong></div>`);
  return parts.join("");
}

function renderSplitsBars(payload) {
  if (payload && payload.error) {
    renderError(splitsContent, payload.error);
    return;
  }
  const laps = payload && Array.isArray(payload.laps) ? payload.laps : null;
  if (!laps || !laps.length) {
    renderError(splitsContent, "No split data available for this activity.");
    return;
  }

  const entries = laps
    .map((lap, i) => {
      const distance = Number(lap.distance_meters) || null;
      const duration = Number(lap.duration_seconds) || null;
      const pace = distance && duration ? duration / (distance / 1000) : null;
      return {
        lapNumber: lap.lap_number ?? i + 1,
        pace,
        duration,
        hr: lap.avg_hr_bpm != null ? Number(lap.avg_hr_bpm) : null,
      };
    })
    .filter((e) => e.pace != null || e.duration != null);

  if (!entries.length) {
    renderError(splitsContent, "No usable split data (missing distance/duration) for this activity.");
    return;
  }

  const usePace = entries.every((e) => e.pace != null);
  const values = entries.map((e) => (usePace ? e.pace : e.duration));
  const maxValue = Math.max(...values, 1);
  const avgValue = values.reduce((a, b) => a + b, 0) / values.length;
  splitsScore.textContent = usePace
    ? `Avg ${formatPace(avgValue)}`
    : `Avg ${formatDuration(avgValue)}/lap`;

  const bars = document.createElement("div");
  bars.className = "bars";
  const tooltip = createHoverTooltip(bars);

  entries.forEach((entry) => {
    const value = usePace ? entry.pace : entry.duration;
    const heightPct = Math.max(4, Math.round((value / maxValue) * 100));

    const col = document.createElement("div");
    col.className = "bar-col";

    const valueEl = document.createElement("div");
    valueEl.className = "bar-value";
    valueEl.textContent = usePace ? formatPace(value) : formatDuration(value);

    const bar = document.createElement("div");
    bar.className = "bar";
    bar.style.height = `${heightPct}%`;

    const label = document.createElement("div");
    label.className = "bar-label";
    label.textContent = `Lap ${entry.lapNumber}`;

    col.appendChild(valueEl);
    col.appendChild(bar);
    col.appendChild(label);

    if (entry.hr != null) {
      const hrEl = document.createElement("div");
      hrEl.className = "bar-hr";
      hrEl.textContent = `♥ ${Math.round(entry.hr)}`;
      col.appendChild(hrEl);
    }

    col.addEventListener("mousemove", (evt) => {
      const barsRect = bars.getBoundingClientRect();
      tooltip.show(evt.clientX - barsRect.left, evt.clientY - barsRect.top, barTooltipHtml(entry, usePace));
    });
    col.addEventListener("mouseleave", () => tooltip.hide());

    bars.appendChild(col);
  });

  splitsContent.className = "chart-content";
  splitsContent.replaceChildren(bars);
}

async function loadSplits(activityId) {
  splitsContent.className = "chart-content empty";
  splitsContent.textContent = "Loading…";
  splitsScore.textContent = "";
  try {
    const res = await fetch(`/api/activity_splits?activity_id=${encodeURIComponent(activityId)}`);
    const payload = await res.json();
    renderSplitsBars(payload);
  } catch (err) {
    renderError(splitsContent, `Failed to load: ${err?.message || err}`);
  }
}

async function loadActivityOptions() {
  try {
    const res = await fetch("/api/activities?limit=20");
    const payload = await res.json();
    const activities = payload && Array.isArray(payload.activities) ? payload.activities : [];
    splitsActivitySelect.replaceChildren();

    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = activities.length ? "Select an activity…" : "No recent activities found";
    splitsActivitySelect.appendChild(placeholder);

    activities.forEach((a) => {
      const opt = document.createElement("option");
      opt.value = a.id;
      const dateLabel = a.start_time ? String(a.start_time).slice(0, 10) : "";
      opt.textContent = `${a.name || "Activity"} — ${dateLabel}`;
      splitsActivitySelect.appendChild(opt);
    });
  } catch (err) {
    splitsActivitySelect.replaceChildren();
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "Failed to load activities";
    splitsActivitySelect.appendChild(opt);
  }
}

splitsActivitySelect.addEventListener("change", () => {
  if (splitsActivitySelect.value) loadSplits(splitsActivitySelect.value);
});

function showSplits() {
  currentMetric = null;
  panelOverview.hidden = true;
  panelTrend.hidden = true;
  panelChat.hidden = true;
  panelPlan.hidden = true;
  panelChallenges.hidden = true;
  panelSplits.hidden = false;
  if (!splitsLoaded) {
    splitsLoaded = true;
    loadActivityOptions();
  }
}

// --- Challenges & badges ------------------------------------------------------

const challengesAvailableEl = document.getElementById("challenges-available");
const challengesInProgressEl = document.getElementById("challenges-in-progress");
const challengesBadgesEl = document.getElementById("challenges-badges");

function challengeCard(c) {
  const card = document.createElement("article");
  card.className = "challenge-card";

  const header = document.createElement("div");
  header.className = "challenge-card-header";
  const title = document.createElement("h4");
  title.textContent = c.name || "Challenge";
  header.appendChild(title);
  if (c.points != null) {
    const pill = document.createElement("span");
    pill.className = "pill pill-points";
    pill.textContent = `${c.points} pts`;
    header.appendChild(pill);
  }
  card.appendChild(header);

  const meta = document.createElement("div");
  meta.className = "challenge-card-meta";
  const metaParts = [c.category, c.start_date && c.end_date ? `${c.start_date} → ${c.end_date}` : null].filter(Boolean);
  meta.textContent = metaParts.join(" · ");
  card.appendChild(meta);

  if (c.progress != null && c.target != null) {
    const percentNum = parseFloat(c.progress_percent) || 0;
    const bar = document.createElement("div");
    bar.className = "progress-bar";
    const fill = document.createElement("div");
    fill.className = "progress-bar-fill";
    fill.style.width = `${Math.min(percentNum, 100)}%`;
    bar.appendChild(fill);
    card.appendChild(bar);

    const label = document.createElement("div");
    label.className = "progress-label";
    const left = document.createElement("span");
    left.textContent = `${c.progress} / ${c.target}`;
    const right = document.createElement("span");
    right.textContent = c.progress_percent || "";
    label.appendChild(left);
    label.appendChild(right);
    card.appendChild(label);
  } else {
    const statusPill = document.createElement("span");
    statusPill.className = "pill";
    statusPill.textContent = c.joinable ? "Joinable" : c.status || "";
    if (statusPill.textContent) card.appendChild(statusPill);
  }

  return card;
}

function badgeCard(b) {
  const card = document.createElement("article");
  card.className = "badge-card";

  const header = document.createElement("div");
  header.className = "badge-card-header";
  const title = document.createElement("h4");
  title.textContent = b.name || "Badge";
  header.appendChild(title);
  if (b.points != null) {
    const pill = document.createElement("span");
    pill.className = "pill pill-points";
    pill.textContent = `${b.points} pts`;
    header.appendChild(pill);
  }
  card.appendChild(header);

  const meta = document.createElement("div");
  meta.className = "badge-card-meta";
  const metaParts = [b.category, b.difficulty, b.earned_date ? `Earned ${b.earned_date}` : null].filter(Boolean);
  meta.textContent = metaParts.join(" · ");
  card.appendChild(meta);

  return card;
}

async function loadChallengeList(el, url, itemsKey, buildCard, emptyMessage, gridClass) {
  el.className = `${gridClass} empty`;
  el.textContent = "Loading…";
  try {
    const res = await fetch(url);
    const payload = await res.json();
    if (payload && payload.error) {
      renderError(el, payload.error);
      return;
    }
    const items = payload && Array.isArray(payload[itemsKey]) ? payload[itemsKey] : [];
    if (!items.length) {
      renderError(el, emptyMessage);
      return;
    }
    el.className = gridClass;
    el.replaceChildren(...items.map(buildCard));
  } catch (err) {
    renderError(el, `Failed to load: ${err?.message || err}`);
  }
}

function loadAvailableChallenges() {
  return loadChallengeList(
    challengesAvailableEl,
    "/api/challenges/available",
    "challenges",
    challengeCard,
    "No available challenges right now.",
    "challenge-grid"
  );
}

function loadInProgressChallenges() {
  return loadChallengeList(
    challengesInProgressEl,
    "/api/challenges/in_progress",
    "challenges",
    challengeCard,
    "No challenges in progress.",
    "challenge-grid"
  );
}

function loadEarnedBadges() {
  return loadChallengeList(
    challengesBadgesEl,
    "/api/challenges/badges",
    "badges",
    badgeCard,
    "No earned badges yet.",
    "badge-grid"
  );
}

let challengesLoaded = false;

function showChallenges() {
  currentMetric = null;
  panelOverview.hidden = true;
  panelTrend.hidden = true;
  panelSplits.hidden = true;
  panelChat.hidden = true;
  panelPlan.hidden = true;
  panelChallenges.hidden = false;
  if (!challengesLoaded) {
    challengesLoaded = true;
    loadAvailableChallenges();
    loadInProgressChallenges();
    loadEarnedBadges();
  }
}

// --- Chat (shared controller: general Q&A chat + training-plan chat) --------
// Both chat surfaces (the read-only "Chat" tab and the plan-building chat
// inside "Training Plan") are visually/behaviorally identical — only the
// API endpoints, empty-state copy, and post-reply side effect differ.

function createChatController({ messagesEl, formEl, inputEl, resetBtn, apiUrl, resetUrl, emptyText, onReplySuccess }) {
  const submitBtn = formEl.querySelector('button[type="submit"]');

  function emptyState() {
    messagesEl.replaceChildren();
    const empty = document.createElement("div");
    empty.className = "chat-empty";
    empty.textContent = emptyText;
    messagesEl.appendChild(empty);
  }

  function appendBubble(role, text) {
    const existingEmpty = messagesEl.querySelector(".chat-empty");
    if (existingEmpty) existingEmpty.remove();

    const bubble = document.createElement("div");
    bubble.className = `chat-bubble chat-bubble-${role}`;
    const textEl = document.createElement("div");
    textEl.className = "chat-bubble-text";
    appendFormattedBlocks(textEl, text);
    bubble.appendChild(textEl);
    messagesEl.appendChild(bubble);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return bubble;
  }

  function appendPending() {
    const existingEmpty = messagesEl.querySelector(".chat-empty");
    if (existingEmpty) existingEmpty.remove();

    const bubble = document.createElement("div");
    bubble.className = "chat-bubble chat-bubble-assistant chat-bubble-pending";
    bubble.textContent = "Thinking…";
    messagesEl.appendChild(bubble);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return bubble;
  }

  async function send(message) {
    appendBubble("user", message);
    const pending = appendPending();
    inputEl.disabled = true;
    submitBtn.disabled = true;

    try {
      const res = await fetch(apiUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      });
      const payload = await res.json();
      pending.remove();
      if (payload.error) {
        appendBubble("error", payload.error);
      } else {
        appendBubble("assistant", payload.reply || "No reply.");
        if (onReplySuccess) onReplySuccess();
      }
    } catch (err) {
      pending.remove();
      appendBubble("error", `Failed to reach the dashboard server: ${err?.message || err}`);
    } finally {
      inputEl.disabled = false;
      submitBtn.disabled = false;
      inputEl.focus();
    }
  }

  // Auto-grow the textarea to fit what's typed (up to the CSS max-height,
  // beyond which it scrolls) instead of the single-line "text scrolls
  // sideways" behavior of a plain <input>.
  function autoGrow() {
    inputEl.style.height = "auto";
    inputEl.style.height = `${inputEl.scrollHeight}px`;
  }
  inputEl.addEventListener("input", autoGrow);

  // Enter sends the message; Shift+Enter inserts a newline (a plain
  // <textarea> doesn't submit its form on Enter the way <input> did).
  inputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      formEl.requestSubmit();
    }
  });

  formEl.addEventListener("submit", (e) => {
    e.preventDefault();
    const message = inputEl.value.trim();
    if (!message) return;
    inputEl.value = "";
    autoGrow();
    send(message);
  });

  resetBtn.addEventListener("click", async () => {
    resetBtn.disabled = true;
    try {
      await fetch(resetUrl, { method: "POST" });
    } catch (err) {
      // best-effort — local server, a failed reset just leaves old context
    }
    emptyState();
    resetBtn.disabled = false;
  });

  return { focus: () => inputEl.focus() };
}

const qaChat = createChatController({
  messagesEl: document.getElementById("chat-messages"),
  formEl: document.getElementById("chat-form"),
  inputEl: document.getElementById("chat-input"),
  resetBtn: document.getElementById("chat-reset"),
  apiUrl: "/api/chat",
  resetUrl: "/api/chat/reset",
  emptyText: 'Ask anything about your Garmin data — e.g. "How did my sleep this week compare to last week?"',
});

let chatLoaded = false;

function showChat() {
  currentMetric = null;
  panelOverview.hidden = true;
  panelTrend.hidden = true;
  panelSplits.hidden = true;
  panelPlan.hidden = true;
  panelChallenges.hidden = true;
  panelChat.hidden = false;
  if (!chatLoaded) {
    chatLoaded = true;
    qaChat.focus();
  }
}

// --- Training plan (calendar + plan-building chat) ---------------------------

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

const calendarGrid = document.getElementById("calendar-grid");
const calendarMonthLabel = document.getElementById("calendar-month-label");
const calendarPrevBtn = document.getElementById("calendar-prev");
const calendarNextBtn = document.getElementById("calendar-next");

let calendarCursor = new Date();
calendarCursor.setDate(1);

function pad2(n) {
  return String(n).padStart(2, "0");
}

function isoDateFor(year, month, day) {
  return `${year}-${pad2(month + 1)}-${pad2(day)}`;
}

function daysInMonth(year, month) {
  return new Date(year, month + 1, 0).getDate();
}

function mondayIndex(date) {
  return (date.getDay() + 6) % 7; // JS getDay(): 0=Sun..6=Sat -> 0=Mon..6=Sun
}

async function loadCalendar() {
  const year = calendarCursor.getFullYear();
  const month = calendarCursor.getMonth();
  calendarMonthLabel.textContent = `${MONTH_NAMES[month]} ${year}`;

  const numDays = daysInMonth(year, month);
  const leadingBlanks = mondayIndex(new Date(year, month, 1));

  calendarGrid.replaceChildren();
  for (let i = 0; i < leadingBlanks; i++) {
    const blank = document.createElement("div");
    blank.className = "calendar-cell calendar-cell-empty";
    calendarGrid.appendChild(blank);
  }

  const cellsByDate = new Map();
  for (let day = 1; day <= numDays; day++) {
    const cell = document.createElement("div");
    cell.className = "calendar-cell";
    const dateLabel = document.createElement("div");
    dateLabel.className = "calendar-date";
    dateLabel.textContent = String(day);
    cell.appendChild(dateLabel);
    calendarGrid.appendChild(cell);
    cellsByDate.set(isoDateFor(year, month, day), cell);
  }

  const tooltip = createHoverTooltip(calendarGrid);
  const startDate = isoDateFor(year, month, 1);
  const endDate = isoDateFor(year, month, numDays);

  try {
    const res = await fetch(`/api/training_plan/calendar?start_date=${startDate}&end_date=${endDate}`);
    const payload = await res.json();
    const scheduled = payload && Array.isArray(payload.scheduled_workouts) ? payload.scheduled_workouts : [];
    scheduled.forEach((w) => {
      const cell = cellsByDate.get(w.date);
      if (!cell) return;

      const entry = document.createElement("div");
      entry.className = `calendar-entry${w.completed ? " calendar-entry-done" : ""}`;
      entry.textContent = w.name || "Workout";

      entry.addEventListener("mousemove", (evt) => {
        const gridRect = calendarGrid.getBoundingClientRect();
        const lines = [`<div class="tooltip-date">${w.date}</div>`, `<div>${w.name || "Workout"}</div>`];
        if (w.sport) lines.push(`<div>${w.sport}</div>`);
        if (w.completed) lines.push("<div>✓ Completed</div>");
        tooltip.show(evt.clientX - gridRect.left, evt.clientY - gridRect.top, lines.join(""));
      });
      entry.addEventListener("mouseleave", () => tooltip.hide());

      cell.appendChild(entry);
    });
  } catch (err) {
    // Non-fatal: calendar just shows empty day cells.
  }
}

calendarPrevBtn.addEventListener("click", () => {
  calendarCursor.setMonth(calendarCursor.getMonth() - 1);
  loadCalendar();
});

calendarNextBtn.addEventListener("click", () => {
  calendarCursor.setMonth(calendarCursor.getMonth() + 1);
  loadCalendar();
});

const planChat = createChatController({
  messagesEl: document.getElementById("plan-chat-messages"),
  formEl: document.getElementById("plan-chat-form"),
  inputEl: document.getElementById("plan-chat-input"),
  resetBtn: document.getElementById("plan-chat-reset"),
  apiUrl: "/api/training_plan/chat",
  resetUrl: "/api/training_plan/chat/reset",
  emptyText: 'Tell me your goal — e.g. "I want to run a 5k in 6 weeks, I can train 3 days a week."',
  onReplySuccess: () => loadCalendar(),
});

let planLoaded = false;

function showPlan() {
  currentMetric = null;
  panelOverview.hidden = true;
  panelTrend.hidden = true;
  panelSplits.hidden = true;
  panelChat.hidden = true;
  panelChallenges.hidden = true;
  panelPlan.hidden = false;
  if (!planLoaded) {
    planLoaded = true;
    loadCalendar();
    planChat.focus();
  }
}

// --- Nav ---------------------------------------------------------------------

function setActiveNavButton(button) {
  navList.querySelectorAll("button").forEach((b) => b.classList.remove("active"));
  button.classList.add("active");
}

function setNavButtonContent(button, icon, label) {
  const iconEl = document.createElement("span");
  iconEl.className = "nav-icon";
  iconEl.textContent = icon;
  button.appendChild(iconEl);
  button.appendChild(document.createTextNode(label));
}

const overviewLi = document.createElement("li");
const overviewButton = document.createElement("button");
overviewButton.type = "button";
setNavButtonContent(overviewButton, "📊", "Overview");
overviewButton.addEventListener("click", () => {
  setActiveNavButton(overviewButton);
  showOverview();
});
overviewLi.appendChild(overviewButton);
navList.appendChild(overviewLi);

TREND_METRICS.forEach((metric) => {
  const li = document.createElement("li");
  const button = document.createElement("button");
  button.type = "button";
  setNavButtonContent(button, metric.icon, metric.label);
  button.addEventListener("click", () => {
    setActiveNavButton(button);
    showTrend(metric);
  });
  navButtonsByMetricId.set(metric.id, button);
  li.appendChild(button);
  navList.appendChild(li);
});

const splitsLi = document.createElement("li");
const splitsButton = document.createElement("button");
splitsButton.type = "button";
setNavButtonContent(splitsButton, "⏱️", "Activity Splits");
splitsButton.addEventListener("click", () => {
  setActiveNavButton(splitsButton);
  showSplits();
});
splitsLi.appendChild(splitsButton);
navList.appendChild(splitsLi);

const challengesLi = document.createElement("li");
const challengesButton = document.createElement("button");
challengesButton.type = "button";
setNavButtonContent(challengesButton, "🏅", "Challenges");
challengesButton.addEventListener("click", () => {
  setActiveNavButton(challengesButton);
  showChallenges();
});
challengesLi.appendChild(challengesButton);
navList.appendChild(challengesLi);

const chatLi = document.createElement("li");
const chatButton = document.createElement("button");
chatButton.type = "button";
setNavButtonContent(chatButton, "💬", "Chat");
chatButton.addEventListener("click", () => {
  setActiveNavButton(chatButton);
  showChat();
});
chatLi.appendChild(chatButton);
navList.appendChild(chatLi);

const planLi = document.createElement("li");
const planButton = document.createElement("button");
planButton.type = "button";
setNavButtonContent(planButton, "🗓️", "Training Plan");
planButton.addEventListener("click", () => {
  setActiveNavButton(planButton);
  showPlan();
});
planLi.appendChild(planButton);
navList.appendChild(planLi);

setActiveNavButton(overviewButton);
showOverview();
