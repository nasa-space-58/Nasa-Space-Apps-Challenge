"""Optional natural-language Q&A assistant backed by the Claude API.

The assistant never computes physics. It answers by calling read-only tools that
return results the backend has already computed (saved analyses, comparisons,
recommendations, presets) and by searching the project documentation. Every
answer lists the sources it used. The feature is off unless
``ASSISTANT_ENABLED`` is true and ``ANTHROPIC_API_KEY`` is set; the rest of the
application never depends on it.
"""

import json
from collections.abc import Mapping
from typing import Any, Literal, NamedTuple

import anthropic
from fastapi import HTTPException
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    ValidationError,
    model_validator,
)

from app.comparison import compare_results
from app.doc_search import DocSection, search

DEFAULT_MODEL = "claude-haiku-5-5"
MAX_TOOL_RESULT_CHARS = 12000
TRUNCATION_MARKER = "...[truncated]"
DOC_SECTION_CHARS = 4000
DOC_SEARCH_LIMIT = 4

SYSTEM_PROMPT = """\
You are the question-answering assistant of Lunar Landing Navigator, a tool that \
compares lunar south-pole landing sites and dates using LRO/LOLA terrain horizons \
and JPL Horizons Sun and Earth ephemerides.

Rules:
- Every number you state must come from a tool result in this conversation. Do not \
estimate, recompute physics or invent values. If no tool provides a value, say that \
it is not available.
- Use search_docs for questions about methods, assumptions, data sources and \
limitations, and name the document you relied on.
- Use get_analysis, compare_analyses and get_recommendation for questions about the \
user's saved results. Their IDs are listed in the context of the user's message.
- Keep the units the tools return. You may convert units (for example seconds to \
hours) only if you show both values.
- Answer in the language of the user's question. Be concise and match the reader: \
mission planner, educator or member of the public.
- Results are decision support, not flight certification. Mention relevant \
limitations from the documentation.
"""

_TRUE = {"1", "true", "yes"}
_FALSE = {"", "0", "false", "no"}
_FINISHED = ("ready", "insufficient_data")


class AssistantSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    model: str = Field(default=DEFAULT_MODEL, min_length=1)
    effort: Literal["low", "medium", "high"] = "low"
    max_tool_rounds: int = Field(default=4, ge=1, le=8)
    max_output_tokens: int = Field(default=2000, ge=256, le=8000)


def settings_from_env(env: Mapping[str, str]) -> AssistantSettings:
    """Read ``ASSISTANT_ENABLED`` and optional ``ASSISTANT_MODEL``.

    ``ASSISTANT_ENABLED`` accepts ``1/true/yes`` and ``0/false/no`` (any case,
    surrounding whitespace ignored); missing or empty means disabled. Any other
    value raises ``ValueError``. An empty ``ASSISTANT_MODEL`` keeps the default.
    """
    flag = env.get("ASSISTANT_ENABLED", "").strip().lower()
    if flag not in _TRUE | _FALSE:
        raise ValueError(
            f"ASSISTANT_ENABLED must be one of true/false/yes/no/1/0, got {flag!r}"
        )
    model = env.get("ASSISTANT_MODEL", "").strip() or DEFAULT_MODEL
    return AssistantSettings(enabled=flag in _TRUE, model=model)


class ChatTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=4000)


class AssistantQuestion(BaseModel):
    """One question plus optional prior turns and the IDs of open results."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    question: str = Field(min_length=1, max_length=2000)
    analysis_ids: list[str] = Field(default_factory=list, max_length=2)
    recommendation_id: str | None = Field(default=None, min_length=1, max_length=100)
    history: list[ChatTurn] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def validate_history(self) -> "AssistantQuestion":
        """History alternates user/assistant, starting with user, ending with assistant."""
        expected = ["user", "assistant"]
        for index, turn in enumerate(self.history):
            if turn.role != expected[index % 2]:
                raise ValueError("History must alternate user and assistant turns")
        if self.history and self.history[-1].role != "assistant":
            raise ValueError("History must end with an assistant turn")
        return self


class Source(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["doc", "api"]
    ref: str


class AssistantAnswer(BaseModel):
    answer: str
    sources: list[Source]
    model: str
    tool_calls: int
    input_tokens: int
    output_tokens: int


class AssistantUnavailable(Exception):
    """The provider cannot be used now (disabled, key rejected, rate limit, outage)."""


class AssistantFailed(Exception):
    """The provider answered but no usable answer was produced."""


class ToolResult(NamedTuple):
    content: str
    sources: tuple[Source, ...]
    is_error: bool


def truncate(text: str, limit: int = MAX_TOOL_RESULT_CHARS) -> str:
    """Return ``text`` unchanged if it fits, else cut it so that the result,
    including ``TRUNCATION_MARKER``, is exactly ``limit`` characters."""
    if len(text) <= limit:
        return text
    return text[: limit - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER


def _dump(data) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def summarize_analysis(job: dict) -> dict:
    """Compact view of a finished analysis job for the model.

    Keeps the site, UTC window, step, Sun and Earth metrics with interval
    counts, refinement status and observer height; drops frames and intervals.
    Raises ``KeyError`` when a required field is missing.
    """
    result = job["result"]
    analysis = result["analysis"]
    request = analysis["request"]
    summary = {
        "analysis_id": job["id"],
        "status": job["status"],
        "site": request["site"],
        "start_utc": request["start_utc"],
        "end_utc": request["end_utc"],
        "step_s": request["step_s"],
        "refinement_status": analysis.get("refinement", {}).get("status"),
        "observer_height_above_terrain_m": result.get("horizon", {}).get(
            "observer_height_above_terrain_m"
        ),
    }
    for body in ("sun", "earth"):
        summary[body] = {
            "metrics": analysis[body]["metrics"],
            "interval_count": len(analysis[body]["intervals"]),
        }
    return summary


def summarize_recommendation(rec: dict) -> dict:
    """Status, weights, ranking (``None`` until finished) and candidate states."""
    return {
        "recommendation_id": rec["id"],
        "status": rec["status"],
        "weights": rec["request"]["weights"],
        "ranking": rec.get("ranking"),
        "candidates": [
            {
                "candidate_id": c["candidate_id"],
                "site_id": c["site"]["site_id"],
                "state": c["state"],
                "error": c.get("error"),
            }
            for c in rec["candidates"]
        ],
    }


class _NoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _SearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: StrictStr


class _AnalysisArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    analysis_id: StrictStr


class _CompareArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    a_id: StrictStr
    b_id: StrictStr


class _RecommendationArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recommendation_id: StrictStr


def _schema(**properties: str) -> dict:
    return {
        "type": "object",
        "properties": {
            name: {"type": "string", "description": text}
            for name, text in properties.items()
        },
        "required": list(properties),
        "additionalProperties": False,
    }


_DEFINITIONS = [
    {
        "name": "search_docs",
        "description": (
            "Search the project documentation (methods, assumptions, data sources, "
            "limitations, API behaviour). Returns the most relevant sections."
        ),
        "input_schema": _schema(query="Keywords to search for, in English."),
    },
    {
        "name": "list_presets",
        "description": "List the curated lunar landing site presets with coordinates.",
        "input_schema": _schema(),
    },
    {
        "name": "get_analysis",
        "description": (
            "Get the computed Sun and Earth visibility metrics of a saved analysis."
        ),
        "input_schema": _schema(analysis_id="ID of a saved analysis."),
    },
    {
        "name": "compare_analyses",
        "description": (
            "Compare two saved analyses (two sites or two dates) and return metric "
            "deltas, changed intervals and explanations."
        ),
        "input_schema": _schema(
            a_id="ID of the first saved analysis.",
            b_id="ID of the second saved analysis.",
        ),
    },
    {
        "name": "get_recommendation",
        "description": (
            "Get a landing-site recommendation: status, weights, ranking with score "
            "breakdown and candidate states."
        ),
        "input_schema": _schema(recommendation_id="ID of a recommendation."),
    },
]
for _definition in _DEFINITIONS:
    _definition["strict"] = True


class _ToolError(Exception):
    pass


class ToolBox:
    """Read-only tools over saved backend state and the documentation index."""

    def __init__(
        self, store, recommendations, sections: list[DocSection], presets: list[dict]
    ):
        self.store = store
        self.recommendations = recommendations
        self.sections = list(sections)
        self.presets = list(presets)
        self._handlers = {
            "search_docs": (_SearchArgs, self._search_docs),
            "list_presets": (_NoArgs, self._list_presets),
            "get_analysis": (_AnalysisArgs, self._get_analysis),
            "compare_analyses": (_CompareArgs, self._compare),
            "get_recommendation": (_RecommendationArgs, self._get_recommendation),
        }

    def definitions(self) -> list[dict]:
        """Tool definitions in a fixed order: search_docs, list_presets,
        get_analysis, compare_analyses, get_recommendation."""
        return json.loads(json.dumps(_DEFINITIONS))

    def run(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Execute one tool. Problems the model can react to (unknown tool, bad
        arguments, missing or unfinished results) return ``is_error=True``."""
        if name not in self._handlers:
            return ToolResult(f"Error: unknown tool {name!r}", (), True)
        model, handler = self._handlers[name]
        try:
            args = model.model_validate(arguments)
            content, sources = handler(args)
        except ValidationError as exc:
            return ToolResult(f"Error: invalid arguments for {name}: {exc}", (), True)
        except _ToolError as exc:
            return ToolResult(f"Error: {exc}", (), True)
        return ToolResult(truncate(content), tuple(sources), False)

    def _search_docs(self, args: _SearchArgs):
        try:
            hits = search(self.sections, args.query, limit=DOC_SEARCH_LIMIT)
        except ValueError as exc:
            raise _ToolError(str(exc)) from exc
        if not hits:
            return "No matching documentation sections.", []
        refs = [f"{s.path}#{s.heading}" for s in hits]
        payload = [
            {"source": ref, "text": truncate(s.text, DOC_SECTION_CHARS)}
            for ref, s in zip(refs, hits)
        ]
        return _dump(payload), [Source(kind="doc", ref=ref) for ref in refs]

    def _list_presets(self, _args: _NoArgs):
        return _dump(self.presets), [Source(kind="api", ref="/presets")]

    def _finished_job(self, analysis_id: str) -> dict:
        job = self.store.get(analysis_id)
        if job is None:
            raise _ToolError(f"analysis {analysis_id!r} not found")
        if job["status"] not in _FINISHED:
            raise _ToolError(
                f"analysis {analysis_id!r} is {job['status']}; no result yet"
            )
        return job

    def _get_analysis(self, args: _AnalysisArgs):
        job = self._finished_job(args.analysis_id)
        try:
            summary = summarize_analysis(job)
        except KeyError as exc:
            raise _ToolError(f"analysis result is missing field {exc}") from exc
        return _dump(summary), [
            Source(kind="api", ref=f"/analyses/{args.analysis_id}/result")
        ]

    def _compare(self, args: _CompareArgs):
        a, b = self._finished_job(args.a_id), self._finished_job(args.b_id)
        try:
            result = compare_results(a["result"], b["result"])
        except (ValueError, KeyError, TypeError) as exc:
            raise _ToolError(f"incompatible results: {exc}") from exc
        result["job_ids"] = {"a_id": args.a_id, "b_id": args.b_id}
        return _dump(result), [Source(kind="api", ref="/comparisons")]

    def _get_recommendation(self, args: _RecommendationArgs):
        rec = self.recommendations.get(args.recommendation_id)
        if rec is None:
            raise _ToolError(f"recommendation {args.recommendation_id!r} not found")
        return _dump(summarize_recommendation(rec)), [
            Source(kind="api", ref=f"/recommendations/{args.recommendation_id}")
        ]


def _user_message(question: AssistantQuestion) -> str:
    parts = []
    if question.analysis_ids:
        parts.append("open analysis IDs: " + ", ".join(question.analysis_ids))
    if question.recommendation_id:
        parts.append("open recommendation ID: " + question.recommendation_id)
    context = "; ".join(parts) or "no saved analyses or recommendations are open"
    return f"{question.question}\n\nContext: {context}."


def _create(client, **kwargs):
    try:
        return client.messages.create(**kwargs)
    except anthropic.APIConnectionError as exc:
        raise AssistantUnavailable("Cannot reach the Claude API") from exc
    except anthropic.APIStatusError as exc:
        code = exc.status_code
        if code in (401, 403):
            raise AssistantUnavailable(
                "The Claude API rejected the configured key"
            ) from exc
        if code == 429 or code >= 500:
            raise AssistantUnavailable(
                f"The Claude API is temporarily unavailable ({code})"
            ) from exc
        raise AssistantFailed(
            f"The Claude API rejected the request ({code}): {exc.message}"
        ) from exc


def answer_question(
    client, settings: AssistantSettings, toolbox: ToolBox, question: AssistantQuestion
) -> AssistantAnswer:
    """Run the tool-use loop until the model produces a final text answer.

    Raises ``AssistantUnavailable`` for authentication, permission, rate-limit,
    connection and server errors, and ``AssistantFailed`` for refusals, output
    truncation, exhausted tool rounds, empty answers and rejected requests (for
    example an exhausted credit balance).
    """
    messages = [{"role": t.role, "content": t.text} for t in question.history]
    messages.append({"role": "user", "content": _user_message(question)})
    system = [
        {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
    ]
    tools = toolbox.definitions()
    sources: list[Source] = []
    tool_calls = rounds = input_tokens = output_tokens = 0
    while True:
        response = _create(
            client,
            model=settings.model,
            max_tokens=settings.max_output_tokens,
            system=system,
            tools=tools,
            messages=messages,
            output_config={"effort": settings.effort},
        )
        used = response.usage
        input_tokens += (
            used.input_tokens
            + (used.cache_read_input_tokens or 0)
            + (used.cache_creation_input_tokens or 0)
        )
        output_tokens += used.output_tokens
        stop = response.stop_reason
        if stop in ("end_turn", "stop_sequence"):
            text = "".join(b.text for b in response.content if b.type == "text").strip()
            if not text:
                raise AssistantFailed("The model returned an empty answer")
            return AssistantAnswer(
                answer=text,
                sources=sources,
                model=settings.model,
                tool_calls=tool_calls,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        if stop == "refusal":
            raise AssistantFailed("The model declined to answer this question")
        if stop == "max_tokens":
            raise AssistantFailed("The answer exceeded the output limit")
        if stop not in ("tool_use", "pause_turn"):
            raise AssistantFailed(f"Unexpected stop reason {stop!r}")
        rounds += 1
        if rounds > settings.max_tool_rounds:
            raise AssistantFailed(
                f"No answer within {settings.max_tool_rounds} tool rounds"
            )
        messages.append({"role": "assistant", "content": response.content})
        if stop == "pause_turn":
            continue
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            tool_calls += 1
            outcome = toolbox.run(block.name, block.input)
            for source in outcome.sources:
                if source not in sources:
                    sources.append(source)
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": outcome.content,
                    "is_error": outcome.is_error,
                }
            )
        messages.append({"role": "user", "content": results})


def register_assistant_routes(
    app, toolbox: ToolBox, settings: AssistantSettings, client
) -> None:
    """``GET /assistant/status`` and ``POST /assistant/ask``.

    ``client`` is ``None`` when the assistant is disabled or no API key is set;
    asking then returns 503 with the reason.
    """
    if not settings.enabled:
        reason = "The assistant is disabled; set ASSISTANT_ENABLED=true to enable it"
    elif client is None:
        reason = "ANTHROPIC_API_KEY is not set"
    else:
        reason = None

    @app.get("/assistant/status")
    def assistant_status():
        return {
            "enabled": reason is None,
            "model": settings.model,
            "reason": reason,
            "doc_sections": len(toolbox.sections),
        }

    @app.post("/assistant/ask")
    def assistant_ask(body: AssistantQuestion):
        if reason is not None:
            raise HTTPException(503, reason)
        try:
            answer = answer_question(client, settings, toolbox, body)
        except AssistantUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        except AssistantFailed as exc:
            raise HTTPException(502, str(exc)) from exc
        return answer.model_dump(mode="json")
