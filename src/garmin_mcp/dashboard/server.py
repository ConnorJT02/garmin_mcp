"""Local-only web dashboard for Garmin trend data.

A separate process/entry point from the main `garmin-mcp` server (used by
Claude Desktop over stdio). This reuses the same Garmin client setup, local
trend cache, and tool logic — it just exposes a fixed, read-only allowlist
of routes over HTTP instead of the MCP protocol, and serves a small static
frontend that renders the same charts as the MCP Apps widgets.

Security note: a localhost HTTP server is reachable by fetch() from *any*
webpage open in the same browser (the request fires even though the browser
blocks the response body from a foreign origin — classic local-CSRF
surface). Every plain data route registered here is GET-only and calls
exactly one specific, known-safe, read-only tool — there is deliberately no
generic "call any tool by name" passthrough. The one deliberate exception is
/api/training_plan/chat, which can create and schedule real Garmin
workouts; see the tool-allowlist and confirmation-first system prompt in
plan_chat.py for how that's constrained.
"""
import asyncio
import json
import os
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response

import garmin_mcp
from garmin_mcp import activity_management, cache, challenges, health_wellness, training, workout_builders, workouts
from garmin_mcp.dashboard import chat, insights, plan_chat

_STATIC_DIR = Path(__file__).parent / "static"
_SHARED_JS_DIR = Path(__file__).parent.parent / "ui" / "charts-src" / "_shared"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8420

# route suffix -> underlying tool name. All seven take (start_date, end_date).
_TREND_TOOLS = {
    "hrv_trend": "get_hrv_trend",
    "sleep_trend": "get_sleep_trend",
    "heart_rate_trend": "get_heart_rate_trend",
    "vo2max_trend": "get_vo2max_trend",
    "respiration_trend": "get_respiration_trend",
    "training_load_trend": "get_training_load_trend",
    "body_composition_trend": "get_body_composition_trend",
}

# route suffix -> (display label, series key/label list). Mirrors the
# TREND_METRICS config in dashboard/static/dashboard.js — kept in sync
# manually since one is JS (chart series/colors) and one is Python (insight
# prompts), and colors aren't needed here.
_TREND_SERIES = {
    "hrv_trend": ("HRV", [{"key": "last_night_avg_hrv_ms"}]),
    "sleep_trend": ("Sleep", [{"key": "sleep_score"}]),
    "heart_rate_trend": ("Heart Rate", [{"key": "resting_heart_rate_bpm"}]),
    "vo2max_trend": ("VO2 Max", [{"key": "vo2_max"}]),
    "respiration_trend": ("Respiration", [{"key": "avg_sleep_breaths_per_min"}]),
    "training_load_trend": (
        "Training Load",
        [{"key": "ctl", "label": "Fitness (CTL)"}, {"key": "atl", "label": "Fatigue (ATL)"}],
    ),
    "body_composition_trend": ("Body Composition", [{"key": "weight_kg"}]),
}

# static path -> (file on disk, media type)
_STATIC_FILES = {
    "/": (_STATIC_DIR / "index.html", "text/html"),
    "/dashboard.css": (_STATIC_DIR / "dashboard.css", "text/css"),
    "/dashboard.js": (_STATIC_DIR / "dashboard.js", "application/javascript"),
    "/shared/chart-draw.js": (_SHARED_JS_DIR / "chart-draw.js", "application/javascript"),
}


async def _call_tool(app: FastMCP, tool_name: str, **params) -> dict:
    """Call an already-registered MCP tool in-process and unwrap its JSON result.

    Tools in this codebase return a JSON string on success and either a JSON
    string or a plain human-readable error string on failure — both are
    handled here so the dashboard always gets back a dict.
    """
    try:
        content, _structured = await app.call_tool(tool_name, params)
    except Exception as exc:
        return {"error": str(exc)}

    if not content:
        return {"error": f"{tool_name} returned no content"}

    text = getattr(content[0], "text", None)
    if text is None:
        return {"error": f"{tool_name} returned non-text content"}

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"error": text}


def _register_trend_route(app: FastMCP, route_name: str, tool_name: str) -> None:
    @app.custom_route(f"/api/{route_name}", methods=["GET"])
    async def _handler(request: Request) -> Response:
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        if not start_date or not end_date:
            return JSONResponse(
                {"error": "start_date and end_date query params are required"},
                status_code=400,
            )
        payload = await _call_tool(app, tool_name, start_date=start_date, end_date=end_date)
        return JSONResponse(payload)


def _register_activity_routes(app: FastMCP) -> None:
    @app.custom_route("/api/activity_splits", methods=["GET"])
    async def _splits(request: Request) -> Response:
        activity_id = request.query_params.get("activity_id")
        if not activity_id:
            return JSONResponse({"error": "activity_id query param is required"}, status_code=400)
        payload = await _call_tool(app, "get_activity_splits", activity_id=activity_id)
        return JSONResponse(payload)

    @app.custom_route("/api/activities", methods=["GET"])
    async def _activities(request: Request) -> Response:
        try:
            limit = int(request.query_params.get("limit", "20"))
        except ValueError:
            limit = 20
        payload = await _call_tool(app, "get_activities", start=0, limit=limit)
        return JSONResponse(payload)

    @app.custom_route("/api/sleep_stages", methods=["GET"])
    async def _sleep_stages(request: Request) -> Response:
        date = request.query_params.get("date")
        if not date:
            return JSONResponse({"error": "date query param is required"}, status_code=400)
        payload = await _call_tool(app, "get_sleep_summary", date=date)
        return JSONResponse(payload)


def _register_challenges_routes(app: FastMCP) -> None:
    @app.custom_route("/api/challenges/available", methods=["GET"])
    async def _available_challenges(_request: Request) -> Response:
        payload = await _call_tool(app, "get_available_badge_challenges", start=1, limit=50)
        return JSONResponse(payload)

    @app.custom_route("/api/challenges/in_progress", methods=["GET"])
    async def _in_progress_challenges(_request: Request) -> Response:
        payload = await _call_tool(app, "get_non_completed_badge_challenges", start=1, limit=50)
        return JSONResponse(payload)

    @app.custom_route("/api/challenges/badges", methods=["GET"])
    async def _earned_badges(_request: Request) -> Response:
        payload = await _call_tool(app, "get_earned_badges")
        return JSONResponse(payload)


def _register_insights_routes(app: FastMCP) -> None:
    @app.custom_route("/api/insights/metric", methods=["GET"])
    async def _metric_insight(request: Request) -> Response:
        route_name = request.query_params.get("metric")
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        if route_name not in _TREND_TOOLS or not start_date or not end_date:
            return JSONResponse(
                {"error": "metric, start_date, and end_date query params are required"},
                status_code=400,
            )
        if not insights.is_configured():
            return JSONResponse({"error": insights.NOT_CONFIGURED_MESSAGE})

        label, series = _TREND_SERIES[route_name]
        payload = await _call_tool(app, _TREND_TOOLS[route_name], start_date=start_date, end_date=end_date)
        try:
            text = await insights.generate_metric_insight(label, series, payload)
        except insights.InsightsError as exc:
            return JSONResponse({"error": str(exc)})
        return JSONResponse({"insight": text})

    @app.custom_route("/api/insights/overview", methods=["GET"])
    async def _overview_insight(request: Request) -> Response:
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        if not start_date or not end_date:
            return JSONResponse(
                {"error": "start_date and end_date query params are required"},
                status_code=400,
            )
        if not insights.is_configured():
            return JSONResponse({"error": insights.NOT_CONFIGURED_MESSAGE})

        payloads = await asyncio.gather(
            *(
                _call_tool(app, tool_name, start_date=start_date, end_date=end_date)
                for tool_name in _TREND_TOOLS.values()
            )
        )
        metrics = [
            {"label": _TREND_SERIES[route_name][0], "series": _TREND_SERIES[route_name][1], "payload": payload}
            for route_name, payload in zip(_TREND_TOOLS.keys(), payloads)
        ]
        try:
            text = await insights.generate_overview_insight(metrics)
        except insights.InsightsError as exc:
            return JSONResponse({"error": str(exc)})
        return JSONResponse({"insight": text})


def _register_chat_routes(app: FastMCP) -> None:
    @app.custom_route("/api/chat", methods=["POST"])
    async def _chat(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Request body must be JSON"}, status_code=400)

        message = (body.get("message") or "").strip()
        if not message:
            return JSONResponse({"error": "message is required"}, status_code=400)

        if not chat.is_configured():
            return JSONResponse({"error": insights.NOT_CONFIGURED_MESSAGE})

        try:
            reply = await chat.ask(app, message)
        except chat.ChatError as exc:
            return JSONResponse({"error": str(exc)})
        return JSONResponse({"reply": reply})

    @app.custom_route("/api/chat/reset", methods=["POST"])
    async def _chat_reset(_request: Request) -> Response:
        await chat.reset()
        return JSONResponse({"ok": True})


def _register_plan_routes(app: FastMCP) -> None:
    @app.custom_route("/api/training_plan/calendar", methods=["GET"])
    async def _calendar(request: Request) -> Response:
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        if not start_date or not end_date:
            return JSONResponse(
                {"error": "start_date and end_date query params are required"},
                status_code=400,
            )
        payload = await _call_tool(app, "get_scheduled_workouts", start_date=start_date, end_date=end_date)
        return JSONResponse(payload)

    @app.custom_route("/api/training_plan/chat", methods=["POST"])
    async def _plan_chat(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Request body must be JSON"}, status_code=400)

        message = (body.get("message") or "").strip()
        if not message:
            return JSONResponse({"error": "message is required"}, status_code=400)

        if not plan_chat.is_configured():
            return JSONResponse({"error": insights.NOT_CONFIGURED_MESSAGE})

        try:
            reply = await plan_chat.ask(app, message)
        except plan_chat.ChatError as exc:
            return JSONResponse({"error": str(exc)})
        return JSONResponse({"reply": reply})

    @app.custom_route("/api/training_plan/chat/reset", methods=["POST"])
    async def _plan_chat_reset(_request: Request) -> Response:
        await plan_chat.reset()
        return JSONResponse({"ok": True})


def _register_static_routes(app: FastMCP) -> None:
    def _make_handler(file_path: Path, media_type: str):
        async def _handler(_request: Request) -> Response:
            if not file_path.exists():
                return Response(f"{file_path.name} not found", status_code=404)
            return FileResponse(file_path, media_type=media_type)
        return _handler

    for path, (file_path, media_type) in _STATIC_FILES.items():
        app.custom_route(path, methods=["GET"])(_make_handler(file_path, media_type))


def create_app(garmin_client, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> FastMCP:
    """Build the dashboard's FastMCP/Starlette app: register the minimal set
    of tool modules the dashboard actually needs, plus the fixed read-only
    API and static routes.

    Assumes `cache.configure()` has already been called by the caller (real
    usage: `main()`; tests: a temp-cache fixture) — this function only wires
    up tools and routes, matching the separation of concerns already used by
    `garmin_mcp.main()`.
    """
    activity_management.configure(garmin_client)
    health_wellness.configure(garmin_client)
    training.configure(garmin_client)
    workouts.configure(garmin_client)
    workout_builders.configure(garmin_client)
    challenges.configure(garmin_client)
    insights.configure()
    chat.configure()
    plan_chat.configure()

    app = FastMCP("Garmin Dashboard", host=host, port=port)
    activity_management.register_tools(app)
    health_wellness.register_tools(app)
    training.register_tools(app)
    workouts.register_tools(app)
    workout_builders.register_tools(app)
    challenges.register_tools(app)

    for route_name, tool_name in _TREND_TOOLS.items():
        _register_trend_route(app, route_name, tool_name)
    _register_activity_routes(app)
    _register_challenges_routes(app)
    _register_insights_routes(app)
    _register_chat_routes(app)
    _register_plan_routes(app)
    _register_static_routes(app)

    return app


def main() -> None:
    # Loads a .env file from the project directory if present (e.g.
    # ANTHROPIC_API_KEY=sk-ant-...) so secrets don't need to be typed into a
    # terminal session every time. Does nothing if no .env file exists, and
    # never overrides a variable already set in the real environment.
    load_dotenv()

    if sys.platform == "win32":
        garmin_mcp._ensure_windows_ca_bundle()

    garmin_client = garmin_mcp.init_api(garmin_mcp.email, garmin_mcp.password)
    if not garmin_client:
        print("Failed to initialize Garmin Connect client. Exiting.", file=sys.stderr)
        sys.exit(1)

    garmin_client = garmin_mcp._GarminProxy(garmin_client)
    cache.configure()

    host = os.getenv("GARMIN_DASHBOARD_HOST", DEFAULT_HOST)
    port = int(os.getenv("GARMIN_DASHBOARD_PORT", str(DEFAULT_PORT)))

    app = create_app(garmin_client, host=host, port=port)

    url = f"http://{host}:{port}/"
    print(f"Garmin dashboard starting at {url}", file=sys.stderr)
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    app.run(transport="streamable-http")


if __name__ == "__main__":
    main()
