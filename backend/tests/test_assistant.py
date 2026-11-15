"""Q&A assistant: settings, request validation, read-only tools and the tool-use loop.

The Claude API is never called. A scripted fake client stands in for
``anthropic.Anthropic`` so tests are deterministic and cost nothing.
"""

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.assistant import (
    DEFAULT_MODEL,
    MAX_TOOL_RESULT_CHARS,
    SYSTEM_PROMPT,
    TRUNCATION_MARKER,
    AssistantFailed,
    AssistantQuestion,
    AssistantSettings,
    AssistantUnavailable,
    Source,
    ToolBox,
    answer_question,
    register_assistant_routes,
    settings_from_env,
    summarize_analysis,
    summarize_recommendation,
    truncate,
)
from app.doc_search import DocSection


# ---------------------------------------------------------------- fixtures


def view(delay=0, latitude=-86, visible=1800):
    """Minimal saved analysis result, same shape as the comparison tests use."""
    start = datetime(2026, 11, 4, tzinfo=timezone.utc) + timedelta(hours=delay)

    def stamp(seconds):
        return (start + timedelta(seconds=seconds)).isoformat()

    metrics = dict(
        visible_duration_s=visible,
        visible_percent_of_interval=visible / 36,
        longest_blocked_s=3600 - visible,
        longest_visible_s=visible,
        unknown_duration_s=0,
    )
    intervals = [
        dict(
            start_utc=stamp(0), end_utc=stamp(visible), state="full", duration_s=visible
        ),
        dict(
            start_utc=stamp(visible),
            end_utc=stamp(3600),
            state="dark",
            duration_s=3600 - visible,
        ),
    ]
    return dict(
        analysis=dict(
            analysis_id=f"{delay}-{latitude}-{visible}",
            model_version="v1",
            request=dict(
                site=dict(
                    site_id="malapert",
                    latitude_deg=latitude,
                    longitude_deg_east=0,
                    altitude_km=1,
                    reference_frame="MOON_ME",
                ),
                start_utc=stamp(0),
                end_utc=stamp(3600),
                step_s=1800,
            ),
            refinement=dict(status="completed"),
            spatial_sensitivity=dict(offset_deg=0.75),
            sun=dict(metrics=metrics, intervals=intervals),
            earth=dict(metrics=deepcopy(metrics), intervals=deepcopy(intervals)),
        ),
        horizon=dict(
            model_version="h1",
            settings=dict(search_distance_m=100000),
            observer_height_above_terrain_m=2,
        ),
        frames=[dict(utc=stamp(0)), dict(utc=stamp(1800))],
    )


def job(job_id, status="ready", result=None):
    return dict(id=job_id, status=status, submission={}, result=result, error=None)


class FakeStore:
    def __init__(self, items):
        self.items = items

    def get(self, key):
        return self.items.get(key)


RECOMMENDATION = dict(
    id="rec-1",
    status="ready",
    request=dict(weights=dict(safety=0.4, sunlight=0.3, communication=0.3)),
    candidates=[
        dict(
            candidate_id="c1",
            site=dict(site_id="malapert"),
            state="evaluated",
            error=None,
        ),
        dict(
            candidate_id="c2",
            site=dict(site_id="shackleton"),
            state="failed",
            error="boom",
        ),
    ],
    ranking=dict(
        ranked=[dict(candidate_id="c1", total=0.81)], insufficient_data=[], failed=[]
    ),
)

SECTIONS = [
    DocSection(
        "docs/terrain-horizon.md", "Horizon", "terrain horizon ray casting over LOLA"
    ),
    DocSection("docs/risk.md", "Monte Carlo", "seeded Monte Carlo landing risk"),
]

PRESETS = [
    dict(site_id="malapert", latitude_deg=-85.99),
    dict(site_id="shackleton", latitude_deg=-89.67),
]


@pytest.fixture
def toolbox():
    store = FakeStore(
        {
            "a1": job("a1", result=view()),
            "a2": job("a2", result=view(delay=48, visible=900)),
            "pending": job("pending", status="analyzing"),
        }
    )
    recs = FakeStore({"rec-1": RECOMMENDATION})
    return ToolBox(store, recs, SECTIONS, PRESETS)


def usage(inp=10, out=5, read=0, write=0):
    return SimpleNamespace(
        input_tokens=inp,
        output_tokens=out,
        cache_read_input_tokens=read,
        cache_creation_input_tokens=write,
    )


def text_response(text, stop="end_turn", **kw):
    return SimpleNamespace(
        stop_reason=stop,
        content=[SimpleNamespace(type="text", text=text)],
        usage=usage(**kw),
    )


def tool_response(*calls, **kw):
    blocks = [
        SimpleNamespace(type="tool_use", id=f"tu{i}", name=name, input=args)
        for i, (name, args) in enumerate(calls)
    ]
    return SimpleNamespace(stop_reason="tool_use", content=blocks, usage=usage(**kw))


class FakeClient:
    """Scripted stand-in for anthropic.Anthropic; records every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append(
            deepcopy({k: v for k, v in kwargs.items() if k != "messages"})
        )
        self.requests[-1]["messages"] = list(kwargs["messages"])
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


SETTINGS = AssistantSettings(enabled=True)


def ask(text="What is the sunlight duration?", **kw):
    return AssistantQuestion(question=text, **kw)


# ---------------------------------------------------------------- settings


def test_settings_default_to_disabled_haiku():
    settings = settings_from_env({})
    assert settings.enabled is False
    assert settings.model == DEFAULT_MODEL == "claude-haiku-5-5"
    assert settings.effort == "low"


@pytest.mark.parametrize("value", ["1", "true", "TRUE", " yes ", "Yes"])
def test_settings_enable_values(value):
    assert settings_from_env({"ASSISTANT_ENABLED": value}).enabled is True


@pytest.mark.parametrize("value", ["", "0", "false", "No", "  "])
def test_settings_disable_values(value):
    assert settings_from_env({"ASSISTANT_ENABLED": value}).enabled is False


def test_settings_reject_unknown_flag_values():
    with pytest.raises(ValueError):
        settings_from_env({"ASSISTANT_ENABLED": "maybe"})


def test_settings_model_override_and_empty_model_keeps_default():
    assert (
        settings_from_env({"ASSISTANT_MODEL": "claude-sonnet-5-5"}).model
        == "claude-sonnet-5-5"
    )
    assert settings_from_env({"ASSISTANT_MODEL": "  "}).model == DEFAULT_MODEL


def test_settings_bounds():
    with pytest.raises(ValidationError):
        AssistantSettings(max_tool_rounds=0)
    with pytest.raises(ValidationError):
        AssistantSettings(max_tool_rounds=9)
    with pytest.raises(ValidationError):
        AssistantSettings(max_output_tokens=100)


# ---------------------------------------------------------------- request validation


def test_question_is_stripped_and_required():
    assert ask("  Merhaba  ").question == "Merhaba"
    with pytest.raises(ValidationError):
        ask("   ")
    with pytest.raises(ValidationError):
        ask("x" * 2001)


def test_question_limits_ids_and_forbids_extra_fields():
    assert ask(analysis_ids=["a1", "a2"]).analysis_ids == ["a1", "a2"]
    with pytest.raises(ValidationError):
        ask(analysis_ids=["a", "b", "c"])
    with pytest.raises(ValidationError):
        AssistantQuestion(question="q", api_key="nope")


def test_history_must_alternate_starting_with_user_and_ending_with_assistant():
    good = [dict(role="user", text="hi"), dict(role="assistant", text="hello")]
    assert len(ask(history=good).history) == 2
    for bad in (
        [dict(role="assistant", text="hello")],
        [dict(role="user", text="hi")],
        [dict(role="user", text="a"), dict(role="user", text="b")],
        [
            dict(role="user", text="a"),
            dict(role="assistant", text="b"),
            dict(role="assistant", text="c"),
        ],
    ):
        with pytest.raises(ValidationError):
            ask(history=bad)


def test_history_length_is_bounded():
    turns = [dict(role=("user", "assistant")[i % 2], text="t") for i in range(12)]
    with pytest.raises(ValidationError):
        ask(history=turns)


# ---------------------------------------------------------------- helpers


def test_truncate_keeps_short_text_and_cuts_to_exact_limit():
    assert truncate("short", 100) == "short"
    assert truncate("x" * 100, 100) == "x" * 100
    cut = truncate("x" * 500, 100)
    assert len(cut) == 100
    assert cut.endswith(TRUNCATION_MARKER)


def test_analysis_summary_keeps_metrics_and_drops_bulky_series():
    summary = summarize_analysis(job("a1", result=view()))
    assert summary["analysis_id"] == "a1"
    assert summary["status"] == "ready"
    assert summary["site"]["site_id"] == "malapert"
    assert summary["start_utc"].startswith("2026-11-04T00:00:00")
    assert summary["step_s"] == 1800
    assert summary["sun"]["metrics"]["visible_duration_s"] == 1800
    assert summary["sun"]["interval_count"] == 2
    assert summary["earth"]["interval_count"] == 2
    assert summary["refinement_status"] == "completed"
    assert summary["observer_height_above_terrain_m"] == 2
    text = json.dumps(summary)
    assert "frames" not in text and "intervals" not in text


def test_analysis_summary_fails_loudly_on_malformed_result():
    broken = view()
    del broken["analysis"]["sun"]
    with pytest.raises(KeyError):
        summarize_analysis(job("a1", result=broken))


def test_recommendation_summary():
    summary = summarize_recommendation(RECOMMENDATION)
    assert summary["recommendation_id"] == "rec-1"
    assert summary["status"] == "ready"
    assert summary["weights"]["safety"] == 0.4
    assert summary["ranking"]["ranked"][0]["candidate_id"] == "c1"
    assert summary["candidates"] == [
        dict(candidate_id="c1", site_id="malapert", state="evaluated", error=None),
        dict(candidate_id="c2", site_id="shackleton", state="failed", error="boom"),
    ]
    running = dict(RECOMMENDATION, status="running")
    del running["ranking"]
    assert summarize_recommendation(running)["ranking"] is None


# ---------------------------------------------------------------- tools


def test_tool_definitions_are_fixed_and_strict(toolbox):
    defs = toolbox.definitions()
    assert [d["name"] for d in defs] == [
        "search_docs",
        "list_presets",
        "get_analysis",
        "compare_analyses",
        "get_recommendation",
    ]
    for d in defs:
        assert d["description"]
        schema = d["input_schema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) <= set(schema["properties"])
    required = {d["name"]: d["input_schema"]["required"] for d in defs}
    assert required["search_docs"] == ["query"]
    assert required["list_presets"] == []
    assert required["get_analysis"] == ["analysis_id"]
    assert sorted(required["compare_analyses"]) == ["a_id", "b_id"]
    assert required["get_recommendation"] == ["recommendation_id"]
    assert toolbox.definitions() == defs


def test_search_docs_returns_sections_with_doc_sources(toolbox):
    result = toolbox.run("search_docs", {"query": "terrain horizon"})
    assert result.is_error is False
    assert "ray casting" in result.content
    assert result.sources[0] == Source(
        kind="doc", ref="docs/terrain-horizon.md#Horizon"
    )


def test_search_docs_without_hits_is_not_an_error(toolbox):
    result = toolbox.run("search_docs", {"query": "quasar"})
    assert result.is_error is False
    assert result.sources == ()


def test_search_docs_with_empty_query_is_a_tool_error(toolbox):
    assert toolbox.run("search_docs", {"query": "  "}).is_error is True


def test_list_presets(toolbox):
    result = toolbox.run("list_presets", {})
    assert result.is_error is False
    assert "shackleton" in result.content
    assert result.sources == (Source(kind="api", ref="/presets"),)


def test_get_analysis_returns_summary(toolbox):
    result = toolbox.run("get_analysis", {"analysis_id": "a1"})
    assert result.is_error is False
    data = json.loads(result.content)
    assert data["sun"]["metrics"]["visible_duration_s"] == 1800
    assert "frames" not in data
    assert result.sources == (Source(kind="api", ref="/analyses/a1/result"),)


def test_get_analysis_missing_or_unfinished_are_tool_errors(toolbox):
    missing = toolbox.run("get_analysis", {"analysis_id": "nope"})
    assert missing.is_error is True and "nope" in missing.content
    pending = toolbox.run("get_analysis", {"analysis_id": "pending"})
    assert pending.is_error is True and "analyzing" in pending.content


def test_compare_analyses_uses_backend_comparison(toolbox):
    result = toolbox.run("compare_analyses", {"a_id": "a1", "b_id": "a2"})
    assert result.is_error is False
    data = json.loads(result.content)
    assert data["mode"] == "dates"
    assert data["deltas"]["sun"]["visible_duration_s"] == pytest.approx(-900)
    assert result.sources == (Source(kind="api", ref="/comparisons"),)


def test_compare_analyses_errors(toolbox):
    assert toolbox.run("compare_analyses", {"a_id": "a1", "b_id": "nope"}).is_error
    assert toolbox.run("compare_analyses", {"a_id": "a1", "b_id": "pending"}).is_error


def test_get_recommendation(toolbox):
    result = toolbox.run("get_recommendation", {"recommendation_id": "rec-1"})
    assert result.is_error is False
    assert json.loads(result.content)["status"] == "ready"
    assert result.sources == (Source(kind="api", ref="/recommendations/rec-1"),)
    assert toolbox.run("get_recommendation", {"recommendation_id": "x"}).is_error


@pytest.mark.parametrize(
    "name,args",
    [
        ("launch_rocket", {}),
        ("get_analysis", {}),
        ("get_analysis", {"analysis_id": 7}),
        ("get_analysis", {"analysis_id": "a1", "extra": 1}),
        ("list_presets", {"x": 1}),
    ],
)
def test_unknown_tools_and_bad_arguments_are_tool_errors(toolbox, name, args):
    result = toolbox.run(name, args)
    assert result.is_error is True
    assert result.sources == ()


def test_tool_output_is_truncated(toolbox):
    big = [dict(site_id=f"s{i}", note="x" * 200) for i in range(200)]
    box = ToolBox(FakeStore({}), FakeStore({}), SECTIONS, big)
    result = box.run("list_presets", {})
    assert len(result.content) == MAX_TOOL_RESULT_CHARS
    assert result.content.endswith(TRUNCATION_MARKER)


# ---------------------------------------------------------------- tool-use loop


def test_direct_answer_request_shape_and_usage(toolbox):
    client = FakeClient(
        text_response("Sunlight lasts 1800 s.", inp=100, out=20, read=50, write=7)
    )
    answer = answer_question(
        client, SETTINGS, toolbox, ask(analysis_ids=["a1"], recommendation_id="rec-1")
    )
    assert answer.answer == "Sunlight lasts 1800 s."
    assert answer.tool_calls == 0
    assert answer.sources == []
    assert answer.model == SETTINGS.model
    assert answer.input_tokens == 157
    assert answer.output_tokens == 20
    request = client.requests[0]
    assert request["model"] == SETTINGS.model
    assert request["max_tokens"] == SETTINGS.max_output_tokens
    assert request["system"][0]["text"] == SYSTEM_PROMPT
    assert request["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert request["tools"] == toolbox.definitions()
    assert request["output_config"] == {"effort": SETTINGS.effort}
    for removed in ("temperature", "top_p", "top_k"):
        assert removed not in request
    last = request["messages"][-1]
    assert last["role"] == "user" and isinstance(last["content"], str)
    assert "What is the sunlight duration?" in last["content"]
    assert "a1" in last["content"] and "rec-1" in last["content"]


def test_history_is_sent_before_the_question(toolbox):
    client = FakeClient(text_response("ok"))
    history = [dict(role="user", text="first"), dict(role="assistant", text="reply")]
    answer_question(client, SETTINGS, toolbox, ask(history=history))
    messages = client.requests[0]["messages"]
    assert messages[0] == {"role": "user", "content": "first"}
    assert messages[1] == {"role": "assistant", "content": "reply"}
    assert len(messages) == 3


def test_tool_round_trip_returns_results_and_sources(toolbox):
    first = tool_response(("get_analysis", {"analysis_id": "a1"}))
    client = FakeClient(first, text_response("1800 s of sunlight."))
    answer = answer_question(client, SETTINGS, toolbox, ask(analysis_ids=["a1"]))
    assert answer.tool_calls == 1
    assert answer.sources == [Source(kind="api", ref="/analyses/a1/result")]
    second = client.requests[1]["messages"]
    assert second[-2] == {"role": "assistant", "content": first.content}
    results = second[-1]
    assert results["role"] == "user"
    assert len(results["content"]) == 1
    block = results["content"][0]
    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "tu0"
    assert block["is_error"] is False
    assert json.loads(block["content"])["sun"]["metrics"]["visible_duration_s"] == 1800


def test_parallel_tool_calls_answered_in_one_message_and_sources_deduplicated(toolbox):
    first = tool_response(
        ("search_docs", {"query": "terrain horizon"}),
        ("get_analysis", {"analysis_id": "missing"}),
        ("search_docs", {"query": "terrain horizon"}),
    )
    client = FakeClient(first, text_response("done"))
    answer = answer_question(client, SETTINGS, toolbox, ask())
    assert answer.tool_calls == 3
    blocks = client.requests[1]["messages"][-1]["content"]
    assert [b["tool_use_id"] for b in blocks] == ["tu0", "tu1", "tu2"]
    assert [b["is_error"] for b in blocks] == [False, True, False]
    assert answer.sources == [Source(kind="doc", ref="docs/terrain-horizon.md#Horizon")]


def test_pause_turn_is_resumed(toolbox):
    paused = SimpleNamespace(stop_reason="pause_turn", content=[], usage=usage())
    client = FakeClient(paused, text_response("resumed"))
    assert answer_question(client, SETTINGS, toolbox, ask()).answer == "resumed"
    assert len(client.requests) == 2


def test_tool_rounds_are_bounded(toolbox):
    settings = AssistantSettings(enabled=True, max_tool_rounds=1)
    client = FakeClient(
        tool_response(("list_presets", {})),
        tool_response(("list_presets", {})),
        text_response("never"),
    )
    with pytest.raises(AssistantFailed):
        answer_question(client, settings, toolbox, ask())


@pytest.mark.parametrize(
    "response",
    [
        text_response("", stop="end_turn"),
        text_response("partial", stop="max_tokens"),
        text_response("", stop="refusal"),
        text_response("odd", stop="something_new"),
    ],
)
def test_unusable_responses_fail(toolbox, response):
    with pytest.raises(AssistantFailed):
        answer_question(FakeClient(response), SETTINGS, toolbox, ask())


REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def status_error(cls, code, message="error"):
    return cls(message, response=httpx2.Response(code, request=REQUEST), body=None)


@pytest.mark.parametrize(
    "error",
    [
        status_error(anthropic.AuthenticationError, 401),
        status_error(anthropic.PermissionDeniedError, 403),
        status_error(anthropic.RateLimitError, 429),
        status_error(anthropic.InternalServerError, 500),
        anthropic.APIConnectionError(request=REQUEST),
    ],
)
def test_provider_outages_are_unavailable(toolbox, error):
    with pytest.raises(AssistantUnavailable):
        answer_question(FakeClient(error), SETTINGS, toolbox, ask())


def test_rejected_request_such_as_exhausted_credit_fails_with_message(toolbox):
    error = status_error(
        anthropic.BadRequestError, 400, "Your credit balance is too low"
    )
    with pytest.raises(AssistantFailed, match="credit balance"):
        answer_question(FakeClient(error), SETTINGS, toolbox, ask())


# ---------------------------------------------------------------- routes


def make_app(toolbox, settings, client):
    app = FastAPI()
    register_assistant_routes(app, toolbox, settings, client)
    return TestClient(app)


def test_status_reports_disabled_reasons(toolbox):
    off = make_app(toolbox, AssistantSettings(enabled=False), None).get(
        "/assistant/status"
    )
    assert off.status_code == 200
    assert off.json()["enabled"] is False
    assert "ASSISTANT_ENABLED" in off.json()["reason"]
    no_key = make_app(toolbox, SETTINGS, None).get("/assistant/status").json()
    assert no_key["enabled"] is False
    assert "ANTHROPIC_API_KEY" in no_key["reason"]


def test_status_when_enabled(toolbox):
    body = make_app(toolbox, SETTINGS, FakeClient()).get("/assistant/status").json()
    assert body == {
        "enabled": True,
        "model": SETTINGS.model,
        "reason": None,
        "doc_sections": len(SECTIONS),
    }


def test_ask_when_disabled_returns_503(toolbox):
    response = make_app(toolbox, SETTINGS, None).post(
        "/assistant/ask", json={"question": "hi"}
    )
    assert response.status_code == 503


def test_ask_returns_answer(toolbox):
    client = FakeClient(
        tool_response(("list_presets", {})), text_response("Two presets.")
    )
    response = make_app(toolbox, SETTINGS, client).post(
        "/assistant/ask", json={"question": "Which sites exist?"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Two presets."
    assert body["sources"] == [{"kind": "api", "ref": "/presets"}]
    assert body["tool_calls"] == 1


def test_ask_validation_and_error_mapping(toolbox):
    app = make_app(toolbox, SETTINGS, FakeClient())
    assert app.post("/assistant/ask", json={"question": ""}).status_code == 422
    outage = make_app(
        toolbox, SETTINGS, FakeClient(status_error(anthropic.RateLimitError, 429))
    )
    assert outage.post("/assistant/ask", json={"question": "hi"}).status_code == 503
    refused = make_app(toolbox, SETTINGS, FakeClient(text_response("", stop="refusal")))
    assert refused.post("/assistant/ask", json={"question": "hi"}).status_code == 502


def test_application_registers_assistant_disabled_by_default(tmp_path, monkeypatch):
    from app.analysis_api import create_app

    monkeypatch.delenv("ASSISTANT_ENABLED", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = create_app(
        state_dir=tmp_path / "state", data_dir=tmp_path, start_worker=False
    )
    body = TestClient(app).get("/assistant/status").json()
    assert body["enabled"] is False
    assert (
        TestClient(app).post("/assistant/ask", json={"question": "hi"}).status_code
        == 503
    )
