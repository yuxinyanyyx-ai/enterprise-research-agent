"""Strict observable substitutes for Agent Eval external boundaries."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
import json


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

    def enqueue(self, *outputs: Any) -> None:
        self._outputs.extend(outputs)

    def _next(self, call_type: str, messages: Any) -> Any:
        if not self._outputs:
            raise UnexpectedBoundaryCall(f"undeclared LLM {call_type} call")
        self.call_log.record("llm", call_type=call_type, messages=messages, tool_schemas=self.tool_schemas)
        output = self._outputs.popleft()
        if isinstance(output, BaseException):
            raise output
        return output

    def with_structured_output(self, schema: type) -> "StructuredScriptedLLM":
        self.tool_schemas = [convert_to_openai_tool(schema)]
        return StructuredScriptedLLM(self, schema)

    def bind_tools(self, tools):
        self.tool_schemas = [convert_to_openai_tool(tool) for tool in tools]
        return self

    def invoke(self, messages: Any) -> AIMessage:
        output = self._next("text", messages)
        if isinstance(output, dict) and "tool_calls" in output:
            return AIMessage(content="", tool_calls=output["tool_calls"])
        if isinstance(output, dict) and output.get("answer_from") == "tool":
            from src.agent.nodes import _format_dmf_result_text
            message = next(message for message in reversed(messages) if isinstance(message, ToolMessage))
            data = json.loads(message.content)
            if message.name == "search_dmf":
                return AIMessage(content=_format_dmf_result_text(data))
            if message.name == "export_dmf_excel":
                search_message = next((item for item in reversed(messages) if isinstance(item, ToolMessage) and item.name == "search_dmf"), None)
                text = _format_dmf_result_text(json.loads(search_message.content)) if search_message else ""
                return AIMessage(content=text + "\n" + data.get("file_name", data.get("message", "")))
            result = data.get("data", {}).get("dmf_results")
            return AIMessage(content=_format_dmf_result_text(result) if result else data.get("message", ""))
        return output if isinstance(output, AIMessage) else AIMessage(content=str(output))

    def assert_exhausted(self) -> None:
        if self._outputs:
            raise AssertionError(f"{len(self._outputs)} scripted LLM output(s) were unused")


class StructuredScriptedLLM:
    def __init__(self, provider: ScriptedLLMProvider, schema: type) -> None:
        self.provider = provider
        self.schema = schema

    def invoke(self, messages: Any) -> Any:
        output = self.provider._next("structured", messages)
        return output if isinstance(output, self.schema) else self.schema.model_validate(output)


class RecordingLLMFactory:
    """Wrap a real LLM factory while preserving the Eval call contract."""

    def __init__(self, factory: Any, call_log: CallLog) -> None:
        self.factory = factory
        self.call_log = call_log

    def __call__(self) -> "RecordingLLM":
        return RecordingLLM(self.factory(), self.call_log)


class RecordingLLM:
    def __init__(self, delegate: Any, call_log: CallLog) -> None:
        self.delegate = delegate
        self.call_log = call_log

    def with_structured_output(self, schema: type) -> "RecordingStructuredLLM":
        return RecordingStructuredLLM(
            self.delegate.with_structured_output(schema),
            self.call_log,
        )

    def bind_tools(self, tools):
        return RecordingLLM(self.delegate.bind_tools(tools), self.call_log)

    def invoke(self, messages: Any) -> Any:
        self.call_log.record("llm", call_type="text")
        return self.delegate.invoke(messages)


class RecordingStructuredLLM:
    def __init__(self, delegate: Any, call_log: CallLog) -> None:
        self.delegate = delegate
        self.call_log = call_log

    def invoke(self, messages: Any) -> Any:
        self.call_log.record("llm", call_type="structured")
        return self.delegate.invoke(messages)


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
