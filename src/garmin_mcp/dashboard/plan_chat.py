"""Agentic chat that builds a training plan and schedules it on the user's
real Garmin Connect calendar.

Separate conversation/instance from chat.py's read-only Q&A (see
create_app() in server.py) so the general chat can stay strictly read-only
while this one is allowed to write. Shares the same AgenticChat tool-use
loop (agentic_chat.py) — only the system prompt and tool allowlist differ.

Security note: this is the one dashboard surface that can mutate the user's
real Garmin account (creating and scheduling workouts). The tool allowlist
below is still a fixed, explicit list — never every tool registered on the
app — and the system prompt requires the assistant to propose a plan in
plain language and get the user's explicit go-ahead in the conversation
before calling any workout-creating/scheduling/deleting tool.
"""
from garmin_mcp.dashboard.agentic_chat import AgenticChat, ChatError  # noqa: F401 (re-exported)
from garmin_mcp.dashboard.chat import _ALLOWED_TOOLS as _READ_ONLY_TOOLS

# Read-only tools inherited from chat.py (so the plan-builder can look at
# training load, HRV, recent activities, etc. to calibrate the plan) plus
# the write tools needed to actually build and schedule workouts.
_ALLOWED_TOOLS = _READ_ONLY_TOOLS | frozenset(
    {
        "get_workouts",
        "get_workout_by_id",
        "get_scheduled_workouts",
        "get_training_plan_workouts",
        "create_walk_run_workout",
        "create_run_workout",
        "create_z2_walk_workout",
        "create_strength_workout",
        "schedule_week",
        "schedule_workout",
        "schedule_workouts",
        "unschedule_workout",
        "unschedule_workouts",
        "delete_workout",
        "delete_workouts",
    }
)

_SYSTEM_PROMPT = (
    "You are an experienced, encouraging running/fitness coach helping the "
    "user build a training plan on their Garmin Connect calendar. You can "
    "call tools to read their real training data (recent activities, "
    "training load, HRV, sleep, etc.) to calibrate the plan, and tools to "
    "create workouts and schedule them onto specific calendar dates.\n\n"
    "Process, always in this order:\n"
    "1. Understand the goal — event/target date, current fitness, weekly "
    "availability (days/week, time per session). If the user hasn't said, "
    "ask directly rather than assuming.\n"
    "2. Optionally pull real data (recent training load, recent activities) "
    "to sanity-check volume/intensity against what they can actually handle "
    "right now.\n"
    "3. Propose the plan in plain conversational text first — which days, "
    "what kind of session, roughly how long — and explicitly ask the user "
    "to confirm before touching their calendar. Do not call any "
    "create_*/schedule_*/unschedule_*/delete_* tool in this step.\n"
    "4. Only after the user clearly confirms in a later message (e.g. "
    "\"yes\", \"looks good\", \"schedule it\") should you call the "
    "create_* workout-builder tools and then schedule_week/schedule_workout "
    "to actually place them on the calendar. Build each workout with the "
    "matching create_* tool, then schedule the resulting workout_ids onto "
    "the agreed dates.\n"
    "5. After scheduling, confirm in plain text exactly what was created "
    "and on which dates, so the user can check it against the calendar "
    "view.\n\n"
    "Never call unschedule_*/delete_* tools unless the user explicitly asks "
    "you to remove or replace something already on the calendar — never as "
    "part of building a new plan. Dates passed to tools must be in "
    "YYYY-MM-DD format.\n\n"
    "Answer in clear, everyday language — avoid heavy jargon. Keep replies "
    "easy to scan: short paragraphs plus '- ' bullet points for lists of "
    "sessions/dates. Use '**bold**' sparingly for the single most important "
    "point; never use markdown headers."
)

_instance = AgenticChat(_SYSTEM_PROMPT, _ALLOWED_TOOLS, max_tool_rounds=10, max_tokens=1500)

configure = _instance.configure
is_configured = _instance.is_configured
reset = _instance.reset
ask = _instance.ask
