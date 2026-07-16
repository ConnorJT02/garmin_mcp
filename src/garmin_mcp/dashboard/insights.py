"""Claude-generated plain-language insights for the dashboard.

Optional feature: degrades gracefully (via InsightsError) when no Anthropic
API key is configured, rather than breaking the rest of the dashboard.
Follows the same `configure(client)` seam every other garmin_mcp module
uses, so tests can inject a fake client instead of hitting the real API.
"""
import os
from typing import Any, Dict, List, Optional

import anthropic

MODEL = os.getenv("GARMIN_DASHBOARD_INSIGHTS_MODEL", "claude-sonnet-5")

NOT_CONFIGURED_MESSAGE = (
    "Anthropic API key not configured. Set the ANTHROPIC_API_KEY "
    "environment variable and restart the dashboard to enable insights."
)

_MAX_POINTS = 120

_SYSTEM_PROMPT = (
    "You are an elite, highly encouraging sports coach and fitness mentor. "
    "Your job is to translate complex Garmin data into clear, "
    "conversational, user-friendly insights. Avoid heavy medical jargon and "
    "academic terms — speak directly to the user like a supportive personal "
    "trainer who wants them to succeed.\n\n"
    "Tone: friendly, direct, clear, and actionable. Use simple, everyday "
    "language — instead of 'sympathetic drive,' say 'your body is stuck in "
    "fight-or-flight mode'; instead of 'glycogen resynthesis,' say 'muscle "
    "recovery.' Avoid generic filler text. Keep it punchy.\n\n"
    "Structure every response in exactly three parts, under 120 words "
    "total:\n"
    "1. The Headline — one bolded (**like this**), friendly sentence "
    "summarizing the main takeaway.\n"
    "2. The Why — a short paragraph explaining what happened in simple "
    "terms. Always connect the dots between their habits (e.g. late "
    "workouts, stress) and their numbers.\n"
    "3. The Action Item — one or two simple, practical things to do today, "
    "written as '- ' bullet points so they're easy to scan on a phone "
    "screen.\n\n"
    "Never guess at data you don't have. If a correlation is ambiguous, "
    "frame it as something worth trying/testing rather than a stated fact. "
    "Keep the focus entirely on fitness/performance — do not pad the "
    "response with a generic medical disclaimer. If a value falls "
    "genuinely outside normal training-related variance (i.e. plausibly "
    "illness/injury rather than training load), say so plainly in simple "
    "terms as part of the analysis itself, and don't diagnose a specific "
    "condition.\n\n"
    "Formatting: plain, conversational prose with '**bold**' only for the "
    "Headline; '- ' bullet points for the Action Item section; never use "
    "markdown headers."
)

_client: Optional[anthropic.AsyncAnthropic] = None


class InsightsError(Exception):
    """Raised when an insight can't be generated (missing key, API error)."""


def configure(client: Optional[anthropic.AsyncAnthropic] = None) -> None:
    """Configure the module with an AsyncAnthropic client.

    Called with no arguments in normal operation: constructs a real client
    if ANTHROPIC_API_KEY is set in the environment, or leaves the module
    unconfigured (is_configured() -> False) otherwise. Tests pass a fake
    client directly to avoid hitting the real API.
    """
    global _client
    if client is not None:
        _client = client
        return
    if os.getenv("ANTHROPIC_API_KEY"):
        _client = anthropic.AsyncAnthropic()
    else:
        _client = None


def is_configured() -> bool:
    return _client is not None


def _downsample(trend: List[Dict[str, Any]], max_points: int = _MAX_POINTS) -> List[Dict[str, Any]]:
    """Evenly sample a trend down to at most max_points.

    Keeps prompt size (and therefore cost/latency) bounded regardless of
    the selected date range — irrelevant at the default 90-day range,
    which is well under the cap.
    """
    if len(trend) <= max_points:
        return trend
    step = len(trend) / max_points
    return [trend[int(i * step)] for i in range(max_points)]


async def _call_claude(user_content: str, max_tokens: int) -> str:
    if _client is None:
        raise InsightsError(NOT_CONFIGURED_MESSAGE)
    try:
        response = await _client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=_SYSTEM_PROMPT,
            # This is a short, structured writing task, not a reasoning
            # problem — extended thinking is on by default for this model
            # and otherwise silently spends the entire max_tokens budget on
            # internal reasoning, leaving nothing for the actual answer.
            thinking={"type": "disabled"},
            messages=[{"role": "user", "content": user_content}],
        )
    except anthropic.APIError as exc:
        raise InsightsError(f"Insight generation failed: {exc}") from exc

    text_blocks = [block.text for block in response.content if block.type == "text"]
    text = "".join(text_blocks).strip()
    if not text:
        raise InsightsError("Insight generation returned no text.")
    return text


def _format_metric_data(label: str, series: List[Dict[str, Any]], payload: Dict[str, Any]) -> str:
    """`payload` is a tool's raw JSON response: {"trend": [...], <summary
    fields>...}, the same shape returned by e.g. get_hrv_trend/
    get_sleep_trend/etc. The per-day trend entries are flat dicts like
    {"date": ..., "<series_key>": ...}."""
    trend = payload.get("trend") or []
    sampled = _downsample(trend)
    series_desc = ", ".join(s.get("label") or s["key"] for s in series)
    lines = [
        f"{p.get('date')}: " + ", ".join(f"{s['key']}={p.get(s['key'])}" for s in series if s["key"] in p)
        for p in sampled
    ]
    summary = {k: v for k, v in payload.items() if k not in ("trend", "cache_hits", "live_fetches")}
    return (
        f"Metric: {label} ({series_desc})\n"
        f"Summary stats: {summary or 'n/a'}\n"
        f"Data points ({len(sampled)} of {len(trend)}):\n" + "\n".join(lines)
    )


async def generate_metric_insight(label: str, series: List[Dict[str, Any]], payload: Dict[str, Any]) -> str:
    """Headline/Why/Action Item analysis for a single metric (see _SYSTEM_PROMPT
    for structure/tone/length, which is the single source of truth so it
    isn't duplicated and can't drift out of sync with the user-turn prompt)."""
    data_desc = _format_metric_data(label, series, payload)
    prompt = (
        f"Here is an athlete's {label} data from their Garmin device.\n\n"
        f"{data_desc}\n\n"
        f"Apply your Headline/Why/Action Item analysis to this metric."
    )
    return await _call_claude(prompt, max_tokens=600)


async def generate_overview_insight(metrics: List[Dict[str, Any]]) -> str:
    """Holistic, cross-metric analysis for the Overview page.

    `metrics` is a list of {label, series, payload} dicts, one per trend
    metric currently shown on the Overview grid.
    """
    sections = [
        _format_metric_data(m["label"], m["series"], m["payload"])
        for m in metrics
        if m["payload"].get("trend")
    ]
    if not sections:
        raise InsightsError("No metric data available to summarize.")
    prompt = (
        "Here is an athlete's Garmin biometric data across multiple metrics "
        "for the same date range:\n\n" + "\n\n".join(sections) + "\n\n"
        "Apply your Headline/Why/Action Item analysis holistically across these "
        "metrics — focus on the single most significant cross-metric "
        "pattern (e.g. sleep vs. HRV vs. resting heart rate), not an "
        "exhaustive rundown of every number."
    )
    return await _call_claude(prompt, max_tokens=600)
