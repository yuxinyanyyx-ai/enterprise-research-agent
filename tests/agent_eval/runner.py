"""Shared deterministic/live runner for real Agent graph execution."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Literal
from unittest.mock import patch
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from src.agent.react_graph import build_react_graph
from src.services.document_dmf_service import DocumentDMFService
from src.settings import get_settings
from tests.agent_eval.fakes import (
    CallLog,
    FakeDMFSearch,
    FakeDocumentExtractor,
    FakeExporter,
    FakeWatchlistService,
    RecordingLLMFactory,
    ScriptedLLMProvider,
    UnexpectedBoundaryCall,
)
from tests.agent_eval.schema import AgentCase, AgentTurn, Fixture


@dataclass
class TurnResult:
    index: int
    state: dict[str, Any]
    executed_nodes: list[str]
    pending_nodes: list[str]
    interrupted: bool
    answer: str
    call_deltas: dict[str, int]
    call_log: list[dict[str, Any]]


@dataclass
class CaseResult:
    case_id: str
    mode: Literal["deterministic", "live"]
    turns: list[TurnResult] = field(default_factory=list)


class AgentEvalRunner:
    """Execute real Agent nodes while substituting only external dependencies."""

    def __init__(
        self,
        *,
        mode: Literal["deterministic", "live"] = "deterministic",
        llm_factory: Callable[[], Any] | None = None,
    ) -> None:
        if mode == "live" and llm_factory is None:
            raise ValueError("live mode requires llm_factory")
        self.mode = mode
        self.call_log = CallLog()
        self.scripted_llm = ScriptedLLMProvider(self.call_log)
        self.llm_factory = (
            RecordingLLMFactory(llm_factory, self.call_log)
            if llm_factory is not None
            else lambda: self.scripted_llm
        )
        self.dmf = FakeDMFSearch(self.call_log)
        self.document_extractor = FakeDocumentExtractor(self.call_log)
        self._temporary_directory = TemporaryDirectory(prefix="agent-eval-")
        self.output_dir = Path(self._temporary_directory.name)
        self.exporter = FakeExporter(self.call_log, self.output_dir)
        self.watchlist = FakeWatchlistService(self.call_log)

    def close(self) -> None:
        from src.agent import audit_log
        with audit_log._lock:
            if audit_log._handler_path and audit_log._handler_path.is_relative_to(self.output_dir):
                if audit_log._handler is not None:
                    audit_log._handler.close()
                audit_log._handler = None
                audit_log._handler_path = None
        self._temporary_directory.cleanup()

    def __enter__(self) -> "AgentEvalRunner":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    @staticmethod
    def _fixture_value(fixture: Fixture) -> Any:
        if fixture.kind == "error":
            return RuntimeError(str(fixture.value))
        return fixture.value

    def _enqueue_turn(self, case: AgentCase, turn: AgentTurn) -> None:
        if self.mode == "deterministic":
            self.scripted_llm.enqueue(
                *(self._fixture_value(case.fixtures[name]) for name in turn.scripted_llm)
            )
        if turn.dmf_result:
            self.dmf.enqueue(self._fixture_value(case.fixtures[turn.dmf_result]))
        if turn.document_extraction:
            self.document_extractor.enqueue(
                self._fixture_value(case.fixtures[turn.document_extraction])
            )
        if turn.export_result:
            self.exporter.enqueue(self._fixture_value(case.fixtures[turn.export_result]))
        if turn.watchlist_result:
            self.watchlist.enqueue(
                self._fixture_value(case.fixtures[turn.watchlist_result])
            )

    def _document_state(self, case: AgentCase) -> dict[str, dict[str, Any]]:
        artifacts: dict[str, dict[str, Any]] = {}
        for document in case.documents:
            markdown_path = self.output_dir / f"{document.document_id}.md"
            markdown_path.write_text(document.markdown, encoding="utf-8")
            artifacts[document.document_id] = {
                "document_id": document.document_id,
                "file_name": markdown_path.name,
                "source_path": str(markdown_path),
                "markdown_path": str(markdown_path),
                "status": "parsed",
            }
        return artifacts

    @staticmethod
    def _blocked_network(*_: Any, **__: Any) -> None:
        raise UnexpectedBoundaryCall("deterministic Agent Eval attempted network access")

    def run(self, case: AgentCase) -> CaseResult:
        if self.mode not in case.modes:
            raise ValueError(f"case {case.id} does not support mode {self.mode}")
        if self.mode == "deterministic" and case.allowed_external:
            raise ValueError("deterministic cases cannot allow external access")

        thread_id = f"agent-eval-{case.id}-{uuid4()}"
        config = {"configurable": {"thread_id": thread_id}}
        result = CaseResult(case_id=case.id, mode=self.mode)
        document_settings = replace(get_settings(), result_dir=self.output_dir)

        def document_service_factory() -> DocumentDMFService:
            return DocumentDMFService(
                settings=document_settings,
                extractor=self.document_extractor,
                searcher=self.dmf,
            )

        with ExitStack() as stack:
            if self.mode == "deterministic":
                stack.enter_context(patch("requests.sessions.Session.request", self._blocked_network))
                stack.enter_context(patch("httpx.Client.request", self._blocked_network))
                stack.enter_context(patch("httpx.AsyncClient.request", self._blocked_network))
            stack.enter_context(patch("src.agent.nodes.create_apollo_llm", self.llm_factory))
            stack.enter_context(patch("src.tools.dmf_tools.search_dmf_queries", self.dmf))
            stack.enter_context(patch("src.agent.nodes.DocumentDMFService", document_service_factory))
            stack.enter_context(patch("src.tools.export_tools.export_multi_query_result", self.exporter))
            stack.enter_context(patch("src.agent.audit_log.LOG_DIR", self.output_dir / "agent-logs"))
            stack.enter_context(
                patch("src.agent.nodes.create_watchlist_service", lambda: self.watchlist)
            )
            graph = build_react_graph(checkpointer=InMemorySaver(), llm_factory=self.llm_factory)

            for index, turn in enumerate(case.turns):
                self._enqueue_turn(case, turn)
                before = {
                    boundary: self.call_log.count(boundary)
                    for boundary in (
                        "llm",
                        "dmf",
                        "document_extract",
                        "export",
                        "watchlist",
                    )
                }
                if turn.resume is not None:
                    graph_input: Any = Command(resume=turn.resume)
                else:
                    graph_input = {
                        "user_query": turn.user_query,
                        "request_id": f"{case.id}-turn-{index + 1}",
                        "warnings": [],
                    }
                    if index == 0:
                        graph_input.update(case.initial_state)
                        if case.documents:
                            graph_input["document_artifacts"] = self._document_state(case)

                updates = list(graph.stream(graph_input, config=config, stream_mode="updates"))
                executed_nodes = [
                    node
                    for update in updates
                    for node in update
                    if node != "__interrupt__"
                ]
                interrupted = any("__interrupt__" in update for update in updates)
                snapshot = graph.get_state(config)
                state = dict(snapshot.values)
                after = {
                    boundary: self.call_log.count(boundary)
                    for boundary in before
                }
                result.turns.append(
                    TurnResult(
                        index=index,
                        state=state,
                        executed_nodes=executed_nodes,
                        pending_nodes=list(snapshot.next),
                        interrupted=interrupted,
                        answer=str(state.get("final_answer") or ""),
                        call_deltas={name: after[name] - before[name] for name in before},
                        call_log=list(self.call_log.events),
                    )
                )

        if self.mode == "deterministic":
            self.scripted_llm.assert_exhausted()
        self.dmf.assert_exhausted()
        self.document_extractor.assert_exhausted()
        self.exporter.assert_exhausted()
        self.watchlist.assert_exhausted()
        return result
