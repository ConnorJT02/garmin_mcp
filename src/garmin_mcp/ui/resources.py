"""
UI resource registration for Garmin MCP Server

Each chart is a self-contained, single-file HTML/JS bundle (built from
ui/charts-src/<id>_chart.html via `npm run build` in that directory, using
Vite + vite-plugin-singlefile) served from ui/charts/<id>_chart.html.
Per the MCP Apps spec (SEP-1865), each resource:
  - Uses the ui:// URI scheme
  - Is served with mimeType "text/html;profile=mcp-app"
A tool links to one of these by setting meta={"ui": {"resourceUri": <uri>}}
on its @app.tool() registration (see CHART_URIS below for the values to use).
"""
import sys
from pathlib import Path

MCP_APP_MIME_TYPE = "text/html;profile=mcp-app"

_CHARTS_DIR = Path(__file__).parent / "charts"

# chart id -> (uri, display name, description)
_CHARTS = {
    "sleep": (
        "ui://garmin-mcp/sleep_chart.html",
        "sleep_chart",
        "Bar chart of sleep stages (deep/light/REM/awake)",
    ),
    "heart_rate": (
        "ui://garmin-mcp/heart_rate_chart.html",
        "heart_rate_chart",
        "Area chart of heart rate over the day",
    ),
    "stress": (
        "ui://garmin-mcp/stress_chart.html",
        "stress_chart",
        "Area chart of stress levels with reference line",
    ),
    "steps": (
        "ui://garmin-mcp/steps_chart.html",
        "steps_chart",
        "Area chart of steps with goal reference line",
    ),
    "hrv_trend": (
        "ui://garmin-mcp/hrv_trend_chart.html",
        "hrv_trend_chart",
        "Line chart of HRV trend over a date range",
    ),
    "sleep_trend": (
        "ui://garmin-mcp/sleep_trend_chart.html",
        "sleep_trend_chart",
        "Line chart of sleep score trend over a date range",
    ),
    "heart_rate_trend": (
        "ui://garmin-mcp/heart_rate_trend_chart.html",
        "heart_rate_trend_chart",
        "Line chart of resting heart rate trend over a date range",
    ),
    "vo2max_trend": (
        "ui://garmin-mcp/vo2max_trend_chart.html",
        "vo2max_trend_chart",
        "Line chart of VO2 max trend over a date range",
    ),
    "respiration_trend": (
        "ui://garmin-mcp/respiration_trend_chart.html",
        "respiration_trend_chart",
        "Line chart of sleep respiration trend over a date range",
    ),
    "training_load_trend": (
        "ui://garmin-mcp/training_load_trend_chart.html",
        "training_load_trend_chart",
        "Multi-line chart of training load (CTL/ATL) trend over a date range",
    ),
}

# Exposed so other modules can do:
#   from garmin_mcp.ui.resources import CHART_URIS
#   @app.tool(meta={"ui": {"resourceUri": CHART_URIS["sleep"]}})
CHART_URIS = {chart_id: uri for chart_id, (uri, _, _) in _CHARTS.items()}


def register_resources(app):
    """Register ui:// chart resources with the MCP server app.

    Charts whose HTML bundle hasn't been built yet (ui/charts/<id>.html
    missing) are skipped with a warning rather than failing server startup,
    since Priority 1-3 charts are built incrementally.
    """
    for chart_id, (uri, name, description) in _CHARTS.items():
        html_path = _CHARTS_DIR / f"{chart_id}_chart.html"
        if not html_path.exists():
            print(
                f"UI resource skipped: {html_path.name} not built yet (tool linking to {uri} will have no chart)",
                file=sys.stderr,
            )
            continue

        def _make_resource(path: Path):
            async def _read() -> str:
                return path.read_text(encoding="utf-8")
            return _read

        reader = _make_resource(html_path)
        reader.__name__ = f"get_{chart_id}_chart_resource"
        reader.__doc__ = description
        app.resource(
            uri,
            name=name,
            description=description,
            mime_type=MCP_APP_MIME_TYPE,
        )(reader)

    return app
