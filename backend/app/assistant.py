"""Optional natural-language Q&A assistant backed by the Claude API.

The assistant never computes physics. It answers by calling read-only tools that
return results the backend has already computed (saved analyses, comparisons,
recommendations, presets) and by searching the project documentation. Every
answer lists the sources it used. The feature is off unless
``ASSISTANT_ENABLED`` is true and ``ANTHROPIC_API_KEY`` is set; the rest of the
application never depends on it.
"""

from collections.abc import Mapping
from typing import Any, Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.doc_search import DocSection

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
    raise NotImplementedError


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
        raise NotImplementedError


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
    raise NotImplementedError


def summarize_analysis(job: dict) -> dict:
    """Compact view of a finished analysis job for the model.

    Keeps the site, UTC window, step, Sun and Earth metrics with interval
    counts, refinement status and observer height; drops frames and intervals.
    Raises ``KeyError`` when a required field is missing.
    """
    raise NotImplementedError


def summarize_recommendation(rec: dict) -> dict:
    """Status, weights, ranking (``None`` until finished) and candidate states."""
    raise NotImplementedError


class ToolBox:
    """Read-only tools over saved backend state and the documentation index."""

    def __init__(
        self, store, recommendations, sections: list[DocSection], presets: list[dict]
    ):
        raise NotImplementedError

    def definitions(self) -> list[dict]:
        """Tool definitions in a fixed order: search_docs, list_presets,
        get_analysis, compare_analyses, get_recommendation."""
        raise NotImplementedError

    def run(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Execute one tool. Problems the model can react to (unknown tool, bad
        arguments, missing or unfinished results) return ``is_error=True``."""
        raise NotImplementedError


def answer_question(
    client, settings: AssistantSettings, toolbox: ToolBox, question: AssistantQuestion
) -> AssistantAnswer:
    """Run the tool-use loop until the model produces a final text answer.

    Raises ``AssistantUnavailable`` for authentication, permission, rate-limit,
    connection and server errors, and ``AssistantFailed`` for refusals, output
    truncation, exhausted tool rounds, empty answers and rejected requests (for
    example an exhausted credit balance).
    """
    raise NotImplementedError


def register_assistant_routes(
    app, toolbox: ToolBox, settings: AssistantSettings, client
) -> None:
    """``GET /assistant/status`` and ``POST /assistant/ask``.

    ``client`` is ``None`` when the assistant is disabled or no API key is set;
    asking then returns 503 with the reason.
    """
    raise NotImplementedError
