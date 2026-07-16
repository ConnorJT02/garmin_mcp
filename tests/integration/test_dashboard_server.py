"""
Integration tests for the local dashboard server (garmin_mcp.dashboard.server).

Covers HTTP routing and the fixed read-only allowlist only — per-metric
curation correctness is already covered by each underlying tool's own
integration tests (test_health_wellness_tools.py, test_training_tools.py,
test_activity_management_tools.py).
"""
import asyncio
import datetime
import os
import tempfile
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from garmin_mcp import cache
from garmin_mcp.dashboard import chat, insights, plan_chat
from garmin_mcp.dashboard.server import create_app
from tests.fixtures.garmin_responses import (
    MOCK_ACTIVITY_SPLITS,
    MOCK_HEART_RATES,
    MOCK_HRV_DATA,
    MOCK_SLEEP_DATA,
    MOCK_TRAINING_STATUS,
)

MOCK_RESPIRATION_DAY = {
    "calendarDate": "2026-01-01",
    "avgWakingRespirationValue": 15.0,
    "avgSleepRespirationValue": 13.5,
    "highestRespirationValue": 20.0,
    "lowestRespirationValue": 11.0,
}

MOCK_BODY_COMPOSITION_RANGE = {
    "startDate": "2026-07-01",
    "endDate": "2026-07-03",
    "dateWeightList": [
        {"calendarDate": "2026-07-01", "weight": 70000, "bmi": 22.5},
    ],
}


def _stable_date_range(days: int = 2):
    """A date range old enough to fall outside the cache's freshness window."""
    end = datetime.date.today() - datetime.timedelta(days=10)
    start = end - datetime.timedelta(days=days - 1)
    return start.isoformat(), end.isoformat()


@pytest.fixture
def temp_cache():
    """Point the trend cache at an isolated temp DB for the duration of a test."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cache.configure(os.path.join(tmpdir, "test_cache.db"))
        try:
            yield
        finally:
            cache.close()


@pytest.fixture
def client(mock_garmin_client, temp_cache, monkeypatch):
    # Some other test modules (e.g. test_server_e2e.py, test_garmin.py) call
    # load_dotenv() at import time, which — now that a real .env file exists
    # for the dashboard's ANTHROPIC_API_KEY — leaks it into the shared
    # pytest process. Clear it here so these tests are deterministic
    # regardless of test run order or what's in the real environment.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    mock_garmin_client.get_hrv_data.return_value = MOCK_HRV_DATA
    mock_garmin_client.get_training_status.return_value = MOCK_TRAINING_STATUS
    mock_garmin_client.get_sleep_data.return_value = MOCK_SLEEP_DATA
    mock_garmin_client.get_heart_rates.return_value = MOCK_HEART_RATES
    mock_garmin_client.get_respiration_data.return_value = MOCK_RESPIRATION_DAY
    mock_garmin_client.get_body_composition.return_value = MOCK_BODY_COMPOSITION_RANGE
    mock_garmin_client.get_activity_splits.return_value = MOCK_ACTIVITY_SPLITS
    mock_garmin_client.get_activities.return_value = [
        {
            "activityId": 123,
            "activityName": "Morning Run",
            "activityType": {"typeKey": "running"},
            "eventType": {"typeKey": "training"},
            "startTimeLocal": "2026-07-10 08:00:00",
        }
    ]
    mock_garmin_client.query_garmin_graphql.return_value = {
        "data": {
            "workoutScheduleSummariesScalar": [
                {
                    "scheduleDate": "2026-07-20",
                    "scheduledWorkoutId": 999,
                    "workoutId": 111,
                    "workoutName": "Easy Run",
                    "workoutType": "running",
                }
            ]
        }
    }
    mock_garmin_client.get_available_badge_challenges.return_value = [
        {
            "uuid": "ABC123",
            "badgeChallengeName": "Marathon Challenge",
            "challengeCategoryId": 1,
            "badgeChallengeStatusId": 1,
            "startDate": "2026-08-01T00:00:00.0",
            "endDate": "2026-08-31T23:59:59.0",
            "badgePoints": 4,
            "badgeUnitId": 1,
            "badgeProgressValue": None,
            "badgeTargetValue": 42195.0,
            "userJoined": False,
            "joinable": True,
        }
    ]
    mock_garmin_client.get_non_completed_badge_challenges.return_value = [
        {
            "uuid": "DEF456",
            "badgeChallengeName": "Ultra Marathon Challenge",
            "challengeCategoryId": 1,
            "badgeChallengeStatusId": 2,
            "startDate": "2026-07-01T00:00:00.0",
            "endDate": "2026-07-31T23:59:59.0",
            "badgePoints": 4,
            "badgeUnitId": 1,
            "badgeProgressValue": 21000.0,
            "badgeTargetValue": 50000.0,
            "badgeEarnedDate": None,
            "userJoined": True,
        }
    ]
    mock_garmin_client.get_earned_badges.return_value = [
        {
            "badgeName": "10K Steps - 7 Days",
            "badgeCategoryId": 5,
            "badgeDifficultyId": 1,
            "badgePoints": 2,
            "badgeEarnedDate": "2026-06-01T12:00:00.0",
        }
    ]

    app = create_app(mock_garmin_client)
    return TestClient(app.streamable_http_app())


@pytest.mark.parametrize(
    "route_name",
    [
        "hrv_trend",
        "sleep_trend",
        "heart_rate_trend",
        "vo2max_trend",
        "respiration_trend",
        "training_load_trend",
        "body_composition_trend",
    ],
)
def test_trend_route_returns_json(client, route_name):
    start_date, end_date = _stable_date_range(2)
    resp = client.get(f"/api/{route_name}", params={"start_date": start_date, "end_date": end_date})
    assert resp.status_code == 200
    payload = resp.json()
    assert "error" not in payload


def test_trend_route_requires_dates(client):
    resp = client.get("/api/hrv_trend")
    assert resp.status_code == 400
    assert "error" in resp.json()


def test_activity_splits_route(client):
    resp = client.get("/api/activity_splits", params={"activity_id": "123"})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["lap_count"] == 2


def test_activity_splits_requires_id(client):
    resp = client.get("/api/activity_splits")
    assert resp.status_code == 400


def test_activities_route(client):
    resp = client.get("/api/activities")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["count"] == 1
    assert payload["activities"][0]["name"] == "Morning Run"


def test_sleep_stages_route(client):
    resp = client.get("/api/sleep_stages", params={"date": "2024-01-15"})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["deep_sleep_seconds"] == 7200
    assert payload["light_sleep_seconds"] == 14400
    assert payload["rem_sleep_seconds"] == 7200


def test_sleep_stages_requires_date(client):
    resp = client.get("/api/sleep_stages")
    assert resp.status_code == 400


def test_challenges_available_route(client):
    resp = client.get("/api/challenges/available")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 1
    assert payload["challenges"][0]["name"] == "Marathon Challenge"
    assert payload["challenges"][0]["joinable"] is True


def test_challenges_in_progress_route(client):
    resp = client.get("/api/challenges/in_progress")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 1
    assert payload["challenges"][0]["name"] == "Ultra Marathon Challenge"
    assert payload["challenges"][0]["progress_percent"] == "42.0%"


def test_challenges_badges_route(client):
    resp = client.get("/api/challenges/badges")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total_badges"] == 1
    assert payload["badges"][0]["name"] == "10K Steps - 7 Days"


class _FakeMessages:
    def __init__(self, text):
        self.text = text
        self.call_count = 0

    async def create(self, **kwargs):
        self.call_count += 1
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.text)])


class _FakeAsyncAnthropic:
    def __init__(self, text="Fake insight text."):
        self.messages = _FakeMessages(text)


@pytest.fixture(autouse=True)
def _reset_insights():
    """Every test starts with insights unconfigured (no API key in test env);
    tests that configure a fake client restore this afterward so state never
    leaks between tests."""
    yield
    insights.configure()


def test_metric_insight_not_configured(client):
    start_date, end_date = _stable_date_range(2)
    resp = client.get(
        "/api/insights/metric",
        params={"metric": "hrv_trend", "start_date": start_date, "end_date": end_date},
    )
    assert resp.status_code == 200
    assert "ANTHROPIC_API_KEY" in resp.json()["error"]


def test_metric_insight_requires_params(client):
    resp = client.get("/api/insights/metric", params={"metric": "hrv_trend"})
    assert resp.status_code == 400


def test_overview_insight_not_configured(client):
    start_date, end_date = _stable_date_range(2)
    resp = client.get("/api/insights/overview", params={"start_date": start_date, "end_date": end_date})
    assert resp.status_code == 200
    assert "ANTHROPIC_API_KEY" in resp.json()["error"]


def test_metric_insight_with_configured_client(client, mock_garmin_client):
    fake = _FakeAsyncAnthropic("Your HRV looks stable this month.")
    insights.configure(fake)
    start_date, end_date = _stable_date_range(2)

    resp = client.get(
        "/api/insights/metric",
        params={"metric": "hrv_trend", "start_date": start_date, "end_date": end_date},
    )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["insight"] == "Your HRV looks stable this month."
    assert fake.messages.call_count == 1
    mock_garmin_client.get_hrv_data.assert_called()


def test_overview_insight_with_configured_client(client, mock_garmin_client):
    fake = _FakeAsyncAnthropic("Overall your metrics look healthy.")
    insights.configure(fake)
    start_date, end_date = _stable_date_range(2)

    resp = client.get("/api/insights/overview", params={"start_date": start_date, "end_date": end_date})

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["insight"] == "Overall your metrics look healthy."
    assert fake.messages.call_count == 1
    # Overview pulls all 7 trend tools before summarizing.
    mock_garmin_client.get_hrv_data.assert_called()
    mock_garmin_client.get_sleep_data.assert_called()
    mock_garmin_client.get_training_status.assert_called()


class _FakeChatMessages:
    """Returns each of `responses` in order on successive create() calls, so
    a test can script a tool_use round followed by a final text round."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def create(self, **kwargs):
        # Snapshot `messages` — it's the same list object chat.py keeps
        # mutating as the tool-use loop continues, so without a copy here
        # every earlier "call" would retroactively show later turns too.
        kwargs = dict(kwargs, messages=list(kwargs["messages"]))
        self.calls.append(kwargs)
        return self._responses.pop(0)


class _FakeChatAnthropic:
    def __init__(self, responses):
        self.messages = _FakeChatMessages(responses)


def _text_response(text):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn")


def _tool_use_response(tool_id, name, tool_input):
    return SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", id=tool_id, name=name, input=tool_input)],
        stop_reason="tool_use",
    )


@pytest.fixture(autouse=True)
def _reset_chat():
    """Every test starts with chat unconfigured and an empty conversation;
    tests that configure a fake client restore this afterward."""
    yield
    asyncio.run(chat.reset())
    chat.configure()


def test_chat_not_configured(client):
    resp = client.post("/api/chat", json={"message": "How's my sleep?"})
    assert resp.status_code == 200
    assert "ANTHROPIC_API_KEY" in resp.json()["error"]


def test_chat_requires_message(client):
    resp = client.post("/api/chat", json={"message": "  "})
    assert resp.status_code == 400


def test_chat_text_only_reply(client):
    fake = _FakeChatAnthropic([_text_response("Your sleep looks solid this week.")])
    chat.configure(fake)

    resp = client.post("/api/chat", json={"message": "How's my sleep?"})

    assert resp.status_code == 200
    assert resp.json()["reply"] == "Your sleep looks solid this week."
    assert len(fake.messages.calls) == 1


def test_chat_calls_allowed_tool(client, mock_garmin_client):
    fake = _FakeChatAnthropic(
        [
            _tool_use_response("tool_1", "get_hrv_data", {"date": "2026-01-01"}),
            _text_response("Your HRV that day was normal."),
        ]
    )
    chat.configure(fake)

    resp = client.post("/api/chat", json={"message": "What was my HRV on Jan 1st?"})

    assert resp.status_code == 200
    assert resp.json()["reply"] == "Your HRV that day was normal."
    assert len(fake.messages.calls) == 2
    mock_garmin_client.get_hrv_data.assert_called()
    # The tool_result for the first round should be fed back on the second call.
    second_call_messages = fake.messages.calls[1]["messages"]
    assert second_call_messages[-1]["content"][0]["type"] == "tool_result"


def test_chat_conversation_persists_across_requests(client):
    fake = _FakeChatAnthropic(
        [
            _text_response("First answer."),
            _text_response("Second answer, building on the first."),
        ]
    )
    chat.configure(fake)

    client.post("/api/chat", json={"message": "First question"})
    client.post("/api/chat", json={"message": "Second question"})

    # Second call's message history should include the earlier turn.
    second_call_messages = fake.messages.calls[1]["messages"]
    assert any(m.get("content") == "First question" for m in second_call_messages)


def test_chat_reset_clears_history(client):
    fake = _FakeChatAnthropic([_text_response("Answer one.")])
    chat.configure(fake)
    client.post("/api/chat", json={"message": "Question one"})

    resp = client.post("/api/chat/reset")
    assert resp.status_code == 200

    fake2 = _FakeChatAnthropic([_text_response("Answer two.")])
    chat.configure(fake2)
    client.post("/api/chat", json={"message": "Question two"})

    sent_messages = fake2.messages.calls[0]["messages"]
    assert sent_messages == [{"role": "user", "content": "Question two"}]


# --- Training plan (calendar + plan-building chat) ---------------------------


@pytest.fixture(autouse=True)
def _reset_plan_chat():
    """Every test starts with plan_chat unconfigured and an empty
    conversation; tests that configure a fake client restore this after."""
    yield
    asyncio.run(plan_chat.reset())
    plan_chat.configure()


def test_training_plan_calendar_route(client):
    start_date, end_date = _stable_date_range(7)
    resp = client.get(
        "/api/training_plan/calendar", params={"start_date": start_date, "end_date": end_date}
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["count"] == 1
    assert payload["scheduled_workouts"][0]["name"] == "Easy Run"


def test_training_plan_calendar_requires_dates(client):
    resp = client.get("/api/training_plan/calendar")
    assert resp.status_code == 400


def test_plan_chat_not_configured(client):
    resp = client.post("/api/training_plan/chat", json={"message": "Build me a 5k plan"})
    assert resp.status_code == 200
    assert "ANTHROPIC_API_KEY" in resp.json()["error"]


def test_plan_chat_requires_message(client):
    resp = client.post("/api/training_plan/chat", json={"message": "  "})
    assert resp.status_code == 400


def test_plan_chat_text_only_reply(client):
    fake = _FakeChatAnthropic([_text_response("Here's a proposed plan — sound good?")])
    plan_chat.configure(fake)

    resp = client.post("/api/training_plan/chat", json={"message": "Build me a 5k plan"})

    assert resp.status_code == 200
    assert resp.json()["reply"] == "Here's a proposed plan — sound good?"


def test_plan_chat_calls_write_tool_after_confirmation(client, mock_garmin_client):
    mock_garmin_client.upload_workout.return_value = {"workoutId": 555, "workoutName": "Easy Run"}
    fake = _FakeChatAnthropic(
        [
            _tool_use_response(
                "tool_1",
                "create_run_workout",
                {"name": "Easy Run", "run_seconds": 1800, "warmup_min": 5, "cooldown_min": 5},
            ),
            _text_response("Created and ready to schedule."),
        ]
    )
    plan_chat.configure(fake)

    resp = client.post("/api/training_plan/chat", json={"message": "Yes, go ahead and create it"})

    assert resp.status_code == 200
    assert resp.json()["reply"] == "Created and ready to schedule."
    mock_garmin_client.upload_workout.assert_called()


def test_plan_chat_is_independent_from_qa_chat(client):
    qa_fake = _FakeChatAnthropic([_text_response("QA answer.")])
    chat.configure(qa_fake)
    plan_fake = _FakeChatAnthropic([_text_response("Plan answer.")])
    plan_chat.configure(plan_fake)

    client.post("/api/chat", json={"message": "QA question"})
    client.post("/api/training_plan/chat", json={"message": "Plan question"})

    # Each conversation only ever saw its own message, not the other's.
    qa_sent = qa_fake.messages.calls[0]["messages"]
    plan_sent = plan_fake.messages.calls[0]["messages"]
    assert qa_sent == [{"role": "user", "content": "QA question"}]
    assert plan_sent == [{"role": "user", "content": "Plan question"}]


def test_plan_chat_reset_clears_history(client):
    fake = _FakeChatAnthropic([_text_response("Answer one.")])
    plan_chat.configure(fake)
    client.post("/api/training_plan/chat", json={"message": "Question one"})

    resp = client.post("/api/training_plan/chat/reset")
    assert resp.status_code == 200

    fake2 = _FakeChatAnthropic([_text_response("Answer two.")])
    plan_chat.configure(fake2)
    client.post("/api/training_plan/chat", json={"message": "Question two"})

    sent_messages = fake2.messages.calls[0]["messages"]
    assert sent_messages == [{"role": "user", "content": "Question two"}]


@pytest.mark.parametrize(
    "path,content_type_prefix",
    [
        ("/", "text/html"),
        ("/dashboard.css", "text/css"),
        ("/dashboard.js", "application/javascript"),
        ("/shared/chart-draw.js", "application/javascript"),
    ],
)
def test_static_routes_serve_files(client, path, content_type_prefix):
    resp = client.get(path)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(content_type_prefix)
