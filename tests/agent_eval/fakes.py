"""Strict observable substitutes for Agent Eval external boundaries."""

from __future__ import annotations

from collections import deque
from copy import copy
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
import json

from src.agent.context_budget import estimate_input_tokens, estimate_tokens


class UnexpectedBoundaryCall(AssertionError):
    pass


@dataclass
class CallLog:
    events: list[dict[str, Any]] = field(default_factory=list)

    def record(self, boundary: str, **details: Any) -> None:
        self.events.append({"boundary": boundary, **details})

    def count(self, boundary: str) -> int:
        return sum(event["boundary"] == boundary for event in self.events)


class ScriptedLLMProvider:
    """One provider implementing both structured and plain LLM calls."""

    def __init__(self, call_log: CallLog) -> None:
        self.call_log = call_log
        self._outputs: deque[Any] = deque()
        self.tool_schemas: list[dict[str, Any]] = []
        self.tool_choice: str | None = None

    def enqueue(self, *outputs: Any) -> None:
        self._outputs.extend(outputs)

    def _next(self, call_type: str, messages: Any) -> Any:
        if not self._outputs:
            raise UnexpectedBoundaryCall(f"undeclared LLM {call_type} call")
        self.call_log.record(
            "llm", call_type=call_type, messages=messages,
            tool_schemas=self.tool_schemas, tool_choice=self.tool_choice,
        )
        output = self._outputs.popleft()
        if isinstance(output, BaseException):
            raise output
        return output

    def with_structured_output(
        self, schema: type, *, include_raw: bool = False
    ) -> "StructuredScriptedLLM":
        bound = copy(self)
        bound.tool_schemas = [convert_to_openai_tool(schema)]
        return StructuredScriptedLLM(bound, schema, include_raw=include_raw)

    def bind_tools(self, tools, *, tool_choice=None):
        bound = copy(self)
        bound.tool_schemas = [convert_to_openai_tool(tool) for tool in tools]
        bound.tool_choice = tool_choice
        return bound

    def invoke(self, messages: Any) -> AIMessage:
        started = perf_counter()
        try:
            output = self._next("text", messages)
            if isinstance(output, dict) and "tool_calls" in output:
                response = AIMessage(content="", tool_calls=output["tool_calls"])
            elif isinstance(output, dict) and output.get("answer_from") == "tool":
                from src.agent.nodes import _format_dmf_result_text
                message = next(message for message in reversed(messages) if isinstance(message, ToolMessage))
                data = json.loads(message.content)
                if message.name == "search_dmf":
                    response = AIMessage(content=_format_dmf_result_text(data))
                elif message.name == "export_dmf_excel":
                    search_message = next((item for item in reversed(messages) if isinstance(item, ToolMessage) and item.name == "search_dmf"), None)
                    text = _format_dmf_result_text(json.loads(search_message.content)) if search_message else ""
                    response = AIMessage(content=text + "\n" + data.get("file_name", data.get("message", "")))
                else:
                    result = data.get("data", {}).get("dmf_results")
                    response = AIMessage(content=_format_dmf_result_text(result) if result else data.get("message", ""))
            else:
                response = output if isinstance(output, AIMessage) else AIMessage(content=str(output))
        except Exception as exc:
            self.call_log.record(
                "llm_invocation",
                call_type="text",
                duration_ms=round((perf_counter() - started) * 1000, 3),
                input_tokens=estimate_input_tokens(messages, self.tool_schemas),
                output_tokens=None,
                total_tokens=None,
                token_source="estimated",
                selected_tools=[],
                failure_code="llm_provider_error",
                error_type=type(exc).__name__,
            )
            raise
        input_tokens = estimate_input_tokens(messages, self.tool_schemas)
        output_tokens = estimate_tokens(json.dumps(response.model_dump(), ensure_ascii=False, default=str))
        self.call_log.record(
            "llm_invocation",
            call_type="text",
            duration_ms=round((perf_counter() - started) * 1000, 3),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            token_source="estimated",
            selected_tools=[str(call.get("name", "")) for call in response.tool_calls],
        )
        return response

    def assert_exhausted(self) -> None:
        if self._outputs:
            raise AssertionError(f"{len(self._outputs)} scripted LLM output(s) were unused")


class StructuredScriptedLLM:
    def __init__(
        self, provider: ScriptedLLMProvider, schema: type, *, include_raw: bool = False
    ) -> None:
        self.provider = provider
        self.schema = schema
        self.include_raw = include_raw

    def invoke(self, messages: Any) -> Any:
        started = perf_counter()
        try:
            output = self.provider._next("structured", messages)
            response = output if isinstance(output, self.schema) else self.schema.model_validate(output)
        except Exception as exc:
            self.provider.call_log.record(
                "llm_invocation",
                call_type="structured",
                duration_ms=round((perf_counter() - started) * 1000, 3),
                input_tokens=estimate_input_tokens(messages, self.provider.tool_schemas),
                output_tokens=None,
                total_tokens=None,
                token_source="estimated",
                selected_tools=[],
                failure_code="llm_provider_error",
                error_type=type(exc).__name__,
            )
            raise
        input_tokens = estimate_input_tokens(messages, self.provider.tool_schemas)
        output_tokens = estimate_tokens(json.dumps(
            response.model_dump() if hasattr(response, "model_dump") else response,
            ensure_ascii=False,
            default=str,
        ))
        self.provider.call_log.record(
            "llm_invocation",
            call_type="structured",
            duration_ms=round((perf_counter() - started) * 1000, 3),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            token_source="estimated",
            selected_tools=[],
        )
        if self.include_raw:
            return {"raw": None, "parsed": response, "parsing_error": None}
        return response


class RecordingLLMFactory:
    """Wrap a real LLM factory while preserving the Eval call contract."""

    def __init__(self, factory: Any, call_log: CallLog) -> None:
        self.factory = factory
        self.call_log = call_log

    def __call__(self) -> "RecordingLLM":
        return RecordingLLM(self.factory(), self.call_log)


class RecordingLLM:
    def __init__(
        self,
        delegate: Any,
        call_log: CallLog,
        tool_schemas: list[dict[str, Any]] | None = None,
    ) -> None:
        self.delegate = delegate
        self.call_log = call_log
        self.tool_schemas = tool_schemas or []

    def with_structured_output(
        self, schema: type, *, include_raw: bool = False
    ) -> "RecordingStructuredLLM":
        return RecordingStructuredLLM(
            self.delegate.with_structured_output(schema, include_raw=include_raw),
            self.call_log,
        )

    def bind_tools(self, tools, *, tool_choice=None):
        return RecordingLLM(
            self.delegate.bind_tools(tools, **({"tool_choice": tool_choice} if tool_choice is not None else {})),
            self.call_log,
            [convert_to_openai_tool(tool) for tool in tools],
        )

    def invoke(self, messages: Any) -> Any:
        self.call_log.record("llm", call_type="text")
        started = perf_counter()
        try:
            response = self.delegate.invoke(messages)
        except Exception as exc:
            self.call_log.record(
                "llm_invocation",
                call_type="text",
                duration_ms=round((perf_counter() - started) * 1000, 3),
                input_tokens=estimate_input_tokens(messages, self.tool_schemas),
                output_tokens=None,
                total_tokens=None,
                token_source="estimated",
                selected_tools=[],
                failure_code="llm_provider_error",
                error_type=type(exc).__name__,
            )
            raise
        input_tokens, output_tokens, total_tokens, token_source = _token_counts(
            response, messages, self.tool_schemas
        )
        self.call_log.record(
            "llm_invocation",
            call_type="text",
            duration_ms=round((perf_counter() - started) * 1000, 3),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            token_source=token_source,
            selected_tools=[str(call.get("name", "")) for call in getattr(response, "tool_calls", [])],
        )
        return response


class RecordingStructuredLLM:
    def __init__(self, delegate: Any, call_log: CallLog) -> None:
        self.delegate = delegate
        self.call_log = call_log

    def invoke(self, messages: Any) -> Any:
        self.call_log.record("llm", call_type="structured")
        started = perf_counter()
        try:
            response = self.delegate.invoke(messages)
        except Exception as exc:
            self.call_log.record(
                "llm_invocation",
                call_type="structured",
                duration_ms=round((perf_counter() - started) * 1000, 3),
                input_tokens=estimate_input_tokens(messages),
                output_tokens=None,
                total_tokens=None,
                token_source="estimated",
                selected_tools=[],
                failure_code="llm_provider_error",
                error_type=type(exc).__name__,
            )
            raise
        input_tokens, output_tokens, total_tokens, token_source = _token_counts(response, messages, [])
        self.call_log.record(
            "llm_invocation",
            call_type="structured",
            duration_ms=round((perf_counter() - started) * 1000, 3),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            token_source=token_source,
            selected_tools=[],
        )
        return response


def _token_counts(
    response: Any,
    messages: Any,
    tool_schemas: list[dict[str, Any]],
) -> tuple[int | None, int | None, int | None, str]:
    raw_response = response.get("raw") if isinstance(response, dict) else response
    usage = getattr(raw_response, "usage_metadata", None)
    metadata = getattr(raw_response, "response_metadata", None)
    if not isinstance(usage, dict) and isinstance(metadata, dict):
        usage = metadata.get("token_usage") or metadata.get("usage")
    if isinstance(usage, dict):
        input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
        output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
        total_tokens = usage.get("total_tokens")
        valid_input = input_tokens if isinstance(input_tokens, int) and not isinstance(input_tokens, bool) and input_tokens >= 0 else None
        valid_output = output_tokens if isinstance(output_tokens, int) and not isinstance(output_tokens, bool) and output_tokens >= 0 else None
        valid_total = total_tokens if isinstance(total_tokens, int) and not isinstance(total_tokens, bool) and total_tokens >= 0 else None
        if any(value is not None for value in (valid_input, valid_output, valid_total)):
            return valid_input, valid_output, valid_total, "actual"
    input_tokens = estimate_input_tokens(messages, tool_schemas)
    output_tokens = estimate_tokens(json.dumps(
        response.model_dump() if hasattr(response, "model_dump") else response,
        ensure_ascii=False,
        default=str,
    ))
    return input_tokens, output_tokens, input_tokens + output_tokens, "estimated"


class QueuedBoundary:
    def __init__(self, name: str, call_log: CallLog) -> None:
        self.name = name
        self.call_log = call_log
        self._outputs: deque[Any] = deque()

    def enqueue(self, *outputs: Any) -> None:
        self._outputs.extend(outputs)

    def take(self, **details: Any) -> Any:
        if not self._outputs:
            raise UnexpectedBoundaryCall(f"undeclared {self.name} call")
        self.call_log.record(self.name, **details)
        output = self._outputs.popleft()
        if isinstance(output, BaseException):
            raise output
        return output

    def assert_exhausted(self) -> None:
        if self._outputs:
            raise AssertionError(f"{len(self._outputs)} {self.name} output(s) were unused")


class FakeDMFSearch(QueuedBoundary):
    def __init__(self, call_log: CallLog) -> None:
        super().__init__("dmf", call_log)

    def __call__(self, **query: Any) -> dict[str, Any]:
        return self.take(query=query)


class FakeDocumentExtractor(QueuedBoundary):
    def __init__(self, call_log: CallLog) -> None:
        super().__init__("document_extract", call_log)

    def __call__(self, markdown: str) -> dict[str, Any]:
        return self.take(markdown=markdown)


class FakeExporter(QueuedBoundary):
    def __init__(self, call_log: CallLog, output_dir: Path) -> None:
        super().__init__("export", call_log)
        self.output_dir = output_dir

    def __call__(self, result: dict[str, Any], **_: Any) -> Path:
        output = self.take(result=result)
        if isinstance(output, dict):
            path = Path(str(output.get("file_path", "agent-eval.xlsx")))
        else:
            path = Path(str(output))
        if not path.is_absolute():
            path = self.output_dir / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return path


def _namespace(value: dict[str, Any], *, enum_fields: set[str]) -> SimpleNamespace:
    converted = dict(value)
    for field_name in enum_fields:
        if field_name in converted and isinstance(converted[field_name], str):
            converted[field_name] = SimpleNamespace(value=converted[field_name])
    return SimpleNamespace(**converted)


class FakeWatchlistService(QueuedBoundary):
    def __init__(self, call_log: CallLog) -> None:
        super().__init__("watchlist", call_log)

    def add(self, dmf_no: str, interval_hours: int = 24) -> Any:
        value = self.take(action="add", dmf_no=dmf_no, interval_hours=interval_hours)
        return _namespace(value, enum_fields={"status"})

    def remove(self, **target: Any) -> Any:
        value = self.take(action="remove", **target)
        return _namespace(value, enum_fields={"status"})

    def list_watchlists(self) -> list[Any]:
        values = self.take(action="list")
        return [_namespace(value, enum_fields={"status"}) for value in values]

    def resolve(self, *, watchlist_id: str = "", dmf_no: str = "") -> Any:
        return SimpleNamespace(
            id=watchlist_id or "watch-1",
            dmf_no=dmf_no or "DMF-001",
        )

    def run_manual(self, watchlist_id: str, *, idempotency_key: str | None = None) -> Any:
        value = self.take(
            action="run",
            watchlist_id=watchlist_id,
            idempotency_key=idempotency_key,
        )
        return _namespace(value, enum_fields={"status"})

    def list_events(self, **target: Any) -> tuple[Any, list[Any]]:
        value = self.take(action="events", **target)
        watchlist = _namespace(value["watchlist"], enum_fields={"status"})
        events = [
            _namespace(event, enum_fields={"event_type", "status"})
            for event in value.get("events", [])
        ]
        return watchlist, events

    def acknowledge_event(self, event_id: str, **target: Any) -> Any:
        value = self.take(action="ack", event_id=event_id, **target)
        return _namespace(value, enum_fields={"status"})
