"""Strict observable substitutes for Agent Eval external boundaries."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage


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

    def enqueue(self, *outputs: Any) -> None:
        self._outputs.extend(outputs)

    def _next(self, call_type: str, messages: Any) -> Any:
        if not self._outputs:
            raise UnexpectedBoundaryCall(f"undeclared LLM {call_type} call")
        self.call_log.record("llm", call_type=call_type, messages=messages)
        output = self._outputs.popleft()
        if isinstance(output, BaseException):
            raise output
        return output

    def with_structured_output(self, schema: type) -> "StructuredScriptedLLM":
        return StructuredScriptedLLM(self, schema)

    def invoke(self, messages: Any) -> AIMessage:
        output = self._next("text", messages)
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
        return path
