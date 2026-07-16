// Shared logic for the multi-day trend line charts (HRV, sleep, heart rate,
// VO2 max, respiration, training load). Each chart HTML file imports this
// and supplies only its own title/series/header formatting — everything
// generic about parsing the tool result, wiring the App lifecycle, and
// drawing an SVG line chart lives here so it isn't duplicated per file.
//
// The actual SVG-drawing functions live in ./chart-draw.js (re-exported
// below) so they can also be imported directly by the local dashboard
// (src/garmin_mcp/dashboard), which has no bundler and can't resolve the
// "@modelcontextprotocol/ext-apps" specifier this file needs.
import { App, PostMessageTransport, applyDocumentTheme, applyHostStyleVariables, applyHostFonts } from "@modelcontextprotocol/ext-apps";

export { formatDateLabel, buildLineChartSVG, buildLegend } from "./chart-draw.js";

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
