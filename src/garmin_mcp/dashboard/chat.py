"""Agentic chat over the user's Garmin data, for the dashboard's chat window.

Unlike insights.py (one-shot, non-agentic text generation from data the
caller already fetched), this module lets Claude decide which Garmin tools
to call — via the standard Anthropic tool-use loop — so it can answer
free-form questions ("how does my sleep this month compare to last month")
that go beyond whatever happens to be on screen.

Security note: Claude only ever sees the fixed, read-only tool allowlist
below, never every tool registered on the dashboard's FastMCP app. Several
tools in the wider garmin_mcp surface (set_activity_name, create_manual_
activity, the workout-writing tools used by plan_chat.py, etc.) can mutate
Garmin data and must never be reachable from here — see the equivalent note
in server.py.
"""
from garmin_mcp.dashboard.agentic_chat import AgenticChat, ChatError  # noqa: F401 (re-exported)

# Read-only tools from the three modules the dashboard already registers
# (activity_management, health_wellness, training) — deliberately excludes
# every set_*/create_*/request_reload tool in those same modules.
_ALLOWED_TOOLS = frozenset(
    {
        # activity_management
        "get_activities_by_date",
        "get_activities_fordate",
        "get_activity",
        "get_activity_splits",
        "get_activity_typed_splits",
        "get_activity_split_summaries",
        "get_activity_weather",
        "get_activity_hr_in_timezones",
        "get_activity_power_in_timezones",
        "get_activity_gear",
        "get_activity_exercise_sets",
        "count_activities",
        "get_activities",
        "get_activity_types",
        # health_wellness
        "get_stats",
        "get_user_summary",
        "get_body_composition",
        "get_body_composition_trend",
        "get_stats_and_body",
        "get_steps_data",
        "get_daily_steps",
        "get_training_readiness",
        "get_body_battery",
        "get_body_battery_events",
        "get_blood_pressure",
        "get_floors",
        "get_rhr_day",
        "get_heart_rates",
        "get_heart_rates_summary",
        "get_heart_rate_trend",
        "get_hydration_data",
        "get_sleep_data",
        "get_sleep_summary",
        "get_sleep_trend",
        "get_stress_data",
        "get_stress_summary",
        "get_respiration_data",
        "get_respiration_summary",
        "get_spo2_data",
        "get_all_day_stress",
        "get_all_day_events",
        "get_lifestyle_logging_data",
        "get_weekly_steps",
        "get_weekly_stress",
        "get_weekly_intensity_minutes",
        "get_morning_training_readiness",
        # training
        "get_progress_summary_between_dates",
        "get_hill_score",
        "get_endurance_score",
        "get_training_effect",
        "get_hrv_data",
        "get_fitnessage_data",
        "get_training_status",
        "get_cycling_ftp",
        "get_lactate_threshold",
        "get_training_load_trend",
        "get_hrv_trend",
        "get_vo2max_trend",
        "get_respiration_trend",
    }
)

_SYSTEM_PROMPT = (
    "You are a friendly, knowledgeable assistant embedded in the user's "
    "personal Garmin fitness dashboard. You can call tools to fetch the "
    "user's real Garmin Connect data (activities, sleep, HRV, heart rate, "
    "training load, body composition, stress, and more) — always call a "
    "tool to fetch real data rather than guessing, estimating, or relying "
    "on what an earlier reply in this conversation said. Dates passed to "
    "tools must be in YYYY-MM-DD format.\n\n"
    "Answer in clear, conversational, everyday language — avoid heavy "
    "medical or academic jargon. Keep replies focused and easy to scan on "
    "a phone screen: a short paragraph or two, plus '- ' bullet points "
    "where a list is clearer. Use '**bold**' sparingly for the single most "
    "important takeaway; never use markdown headers.\n\n"
    "If a question needs data you have no tool for, say so plainly instead "
    "of guessing. Never diagnose a specific medical condition — if a value "
    "looks genuinely outside normal training-related variance, say so "
    "factually and suggest it's worth keeping an eye on, without alarmism."
)

_instance = AgenticChat(_SYSTEM_PROMPT, _ALLOWED_TOOLS)

configure = _instance.configure
is_configured = _instance.is_configured
reset = _instance.reset
ask = _instance.ask
