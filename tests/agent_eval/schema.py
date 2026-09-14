"""Strict schema for data-driven Agent evaluation cases."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Fixture(StrictModel):
    kind: Literal[
        "llm",
        "dmf_result",
        "document_extraction",
        "export_result",
        "watchlist_result",
        "error",
    ]
    value: Any
    sensitive: bool = False


class DocumentSpec(StrictModel):
    document_id: str
    markdown: str
    trusted: bool = True


class AnswerExpectation(StrictModel):
    contains: list[str] = Field(default_factory=list)
    not_contains: list[str] = Field(default_factory=list)
    regex: list[str] = Field(default_factory=list)
    markdown_headers: list[str] = Field(default_factory=list)
    empty: bool | None = None


class CallExpectation(StrictModel):
    llm: int | None = Field(default=None, ge=0)
    dmf: int | None = Field(default=None, ge=0)
    document_extract: int | None = Field(default=None, ge=0)
    export: int | None = Field(default=None, ge=0)
    watchlist: int | None = Field(default=None, ge=0)


class LLMInputExpectation(StrictModel):
    call_index: int = Field(default=0, ge=0)
    call_type: Literal["text", "structured"] | None = None
    max_messages: int | None = Field(default=None, ge=1)
    max_chars: int | None = Field(default=None, ge=1)
    max_estimated_tokens: int | None = Field(default=None, ge=1)
    message_types: list[str] | None = None
    contains: list[str] = Field(default_factory=list)
    not_contains: list[str] = Field(default_factory=list)
    current_query_verbatim: bool = False
    tool_pairs: bool = False


class TurnExpectation(StrictModel):
    route: str | None = None
    interrupt: bool | None = None
    state: dict[str, Any] = Field(default_factory=dict)
    answer: AnswerExpectation = Field(default_factory=AnswerExpectation)
    artifact_types: list[str] = Field(default_factory=list)
    call_deltas: CallExpectation = Field(default_factory=CallExpectation)
    llm_inputs: list[LLMInputExpectation] = Field(default_factory=list)


class AgentTurn(StrictModel):
    user_query: str | None = None
    resume: Any | None = None
    scripted_llm: list[str] = Field(default_factory=list)
    dmf_result: str | None = None
    document_extraction: str | None = None
    export_result: str | None = None
    watchlist_result: str | None = None
    expected: TurnExpectation = Field(default_factory=TurnExpectation)

    @model_validator(mode="after")
    def require_input(self) -> "AgentTurn":
        if self.user_query is None and self.resume is None:
            raise ValueError("turn requires user_query or resume")
        return self

    def fixture_names(self) -> set[str]:
        names = set(self.scripted_llm)
        names.update(
            name
            for name in (
                self.dmf_result,
                self.document_extraction,
                self.export_result,
                self.watchlist_result,
            )
            if name is not None
        )
        return names


class AgentCase(StrictModel):
    id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    description: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    profile: Literal["standard", "real_watchlist"] = "standard"
    modes: set[Literal["deterministic", "live"]] = Field(
        default_factory=lambda: {"deterministic", "live"}
    )
    initial_state: dict[str, Any] = Field(default_factory=dict)
    documents: list[DocumentSpec] = Field(default_factory=list)
    turns: list[AgentTurn] = Field(min_length=1)
    fixtures: dict[str, Fixture] = Field(default_factory=dict)
    expected_calls: CallExpectation = Field(default_factory=CallExpectation)
    allowed_external: bool = False

    @model_validator(mode="after")
    def validate_references(self) -> "AgentCase":
        if self.profile == "real_watchlist" and (
            self.modes != {"live"} or not self.allowed_external
        ):
            raise ValueError(
                "real_watchlist profile requires modes: [live] and allowed_external: true"
            )

        missing = sorted(
            {
                fixture_name
                for turn in self.turns
                for fixture_name in turn.fixture_names()
                if fixture_name not in self.fixtures
            }
        )
        if missing:
            raise ValueError(f"unknown fixture reference(s): {', '.join(missing)}")

        document_ids = [document.document_id for document in self.documents]
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("document_id values must be unique within a case")
        return self


class CaseFile(StrictModel):
    cases: list[AgentCase] = Field(min_length=1)
