"""Explicitly opted-in end-to-end Watchlist Agent evaluation."""

from __future__ import annotations

import os
import json
from contextlib import ExitStack
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import func, select

from src.agent.react_graph import build_react_graph
from src.dmf_history.comparison import normalize_text
from src.dmf_history.models import DMFMonitorRun
from src.dmf_history.repository import (
    DMFHistoryRepository,
    get_configured_history_repository,
)
from src.dmf_watchlist.models import (
    DMFWatchlist,
    DMFWatchlistEvent,
    DMFWatchlistRun,
)
from src.dmf_watchlist.repository import DMFWatchlistRepository
from src.dmf_watchlist.service import (
    DMFWatchlistService,
    create_watchlist_service,
)
from src.settings import PROJECT_ROOT, get_settings
from tests.agent_eval.fakes import CallLog, RecordingLLMFactory
from tests.agent_eval.report import redact_text


@dataclass
class RealTurnResult:
    index: int
    user_query: str
    executed_nodes: list[str]
    answer: str
    intent: dict[str, Any]
    service_calls: list[dict[str, Any]]
    database: dict[str, Any]


@dataclass
class RealWatchlistResult:
    case_id: str = "WATCH-REAL-001"
    dmf_no: str = ""
    database_revision: str = ""
    event_provenance: str = "real_external_change"
    turns: list[RealTurnResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for turn in payload["turns"]:
            turn["answer"] = redact_text(turn["answer"])
        return payload

    def write_report(self, output_root: Path | None = None) -> Path:
        root = output_root or PROJECT_ROOT / "outputs" / "agent_eval"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        output_dir = root / f"{timestamp}-{self.case_id}"
        output_dir.mkdir(parents=True, exist_ok=False)
        payload = self.to_dict()
        (output_dir / "report.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        lines = [
            f"# Agent Eval: {self.case_id}",
            "",
            f"- DMF: {self.dmf_no}",
            f"- Database revision: {self.database_revision}",
            f"- Event provenance: {self.event_provenance}",
        ]
        for turn in payload["turns"]:
            lines.extend(
                [
                    "",
                    f"## Turn {turn['index'] + 1}",
                    f"- Input: {turn['user_query']}",
                    f"- Intent: {turn['intent']}",
                    f"- Nodes: {', '.join(turn['executed_nodes'])}",
                    f"- Service calls: {turn['service_calls']}",
                    f"- Database: {turn['database']}",
                    "",
                    turn["answer"],
                ]
            )
        (output_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
        return output_dir


class RecordingWatchlistService:
    """Record a safe method trace while preserving real Service behavior."""

    def __init__(self, delegate: DMFWatchlistService, call_log: CallLog) -> None:
        self.delegate = delegate
        self.call_log = call_log

    def _record(self, method: str, **details: Any) -> None:
        self.call_log.record("watchlist", method=method, **details)

    def add(self, dmf_no: str, interval_hours: int = 24) -> Any:
        self._record("add", dmf_no=dmf_no, interval_hours=interval_hours)
        return self.delegate.add(dmf_no, interval_hours)

    def remove(self, *, watchlist_id: str = "", dmf_no: str = "") -> Any:
        self._record("remove", watchlist_id=watchlist_id, dmf_no=dmf_no)
        return self.delegate.remove(watchlist_id=watchlist_id, dmf_no=dmf_no)

    def list_watchlists(self) -> Any:
        self._record("list_watchlists")
        return self.delegate.list_watchlists()

    def resolve(self, *, watchlist_id: str = "", dmf_no: str = "") -> Any:
        self._record("resolve", watchlist_id=watchlist_id, dmf_no=dmf_no)
        return self.delegate.resolve(watchlist_id=watchlist_id, dmf_no=dmf_no)

    def run_manual(
        self, watchlist_id: str, *, idempotency_key: str | None = None
    ) -> Any:
        self._record(
            "run_manual",
            watchlist_id=watchlist_id,
            idempotency_key=idempotency_key,
        )
        return self.delegate.run_manual(
            watchlist_id,
            idempotency_key=idempotency_key,
        )

    def list_events(self, *, watchlist_id: str = "", dmf_no: str = "") -> Any:
        self._record("list_events", watchlist_id=watchlist_id, dmf_no=dmf_no)
        return self.delegate.list_events(watchlist_id=watchlist_id, dmf_no=dmf_no)

    def acknowledge_event(
        self,
        event_id: str,
        *,
        watchlist_id: str = "",
        dmf_no: str = "",
    ) -> Any:
        self._record(
            "acknowledge_event",
            event_id=event_id,
            watchlist_id=watchlist_id,
            dmf_no=dmf_no,
        )
        return self.delegate.acknowledge_event(
            event_id,
            watchlist_id=watchlist_id,
            dmf_no=dmf_no,
        )


class RealWatchlistEvalRunner:
    """Run the supported Watchlist lifecycle against isolated real persistence."""

    def __init__(self, *, dmf_no: str, llm_factory: Callable[[], Any]) -> None:
        if not normalize_text(dmf_no):
            raise ValueError("AGENT_EVAL_WATCHLIST_DMF_NO is required")
        self.dmf_no = dmf_no.strip()
        self.call_log = CallLog()
        self.llm_factory = RecordingLLMFactory(llm_factory, self.call_log)
        self._temporary_directory = TemporaryDirectory(prefix="agent-eval-real-watchlist-")
        self.sandbox = Path(self._temporary_directory.name).resolve()
        self.database_path = self.sandbox / "watchlist-eval.db"
        self.database_url = f"sqlite:///{self.database_path.as_posix()}"
        self._stack = ExitStack()
        self._old_cwd = Path.cwd()
        self.history: DMFHistoryRepository | None = None
        self.repository: DMFWatchlistRepository | None = None

    def __enter__(self) -> "RealWatchlistEvalRunner":
        if os.getenv("AGENT_EVAL_WATCHLIST_REAL") != "1":
            raise RuntimeError("set AGENT_EVAL_WATCHLIST_REAL=1")
        self._stack.enter_context(
            patch.dict(
                os.environ,
                {
                    "DATABASE_URL": self.database_url,
                    "DMF_HISTORY_ENABLED": "true",
                    "DMF_WATCHLIST_ENABLED": "true",
                },
            )
        )
        try:
            os.chdir(self.sandbox)
            self._clear_caches()
            self._upgrade_database()
            self.history = DMFHistoryRepository(self.database_url)
            self.repository = DMFWatchlistRepository(self.history.engine)
        except Exception:
            os.chdir(self._old_cwd)
            self._clear_caches()
            self._stack.close()
            self._temporary_directory.cleanup()
            raise
        return self

    def __exit__(self, *_: Any) -> None:
        os.chdir(self._old_cwd)
        if self.history is not None:
            self.history.engine.dispose()
        self._clear_caches()
        self._stack.close()
        self._temporary_directory.cleanup()

    @staticmethod
    def _clear_caches() -> None:
        get_settings.cache_clear()
        create_watchlist_service.cache_clear()
        get_configured_history_repository.cache_clear()

    def _upgrade_database(self) -> None:
        config = Config(str(PROJECT_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
        config.set_main_option("sqlalchemy.url", self.database_url.replace("%", "%%"))
        command.upgrade(config, "head")

    def _database_revision(self) -> str:
        assert self.history is not None
        with self.history.engine.connect() as connection:
            revision = connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one()
        return str(revision)

    def _database_snapshot(self) -> dict[str, Any]:
        assert self.history is not None
        with self.history.session_factory() as session:
            counts = {
                "watchlists": session.scalar(select(func.count()).select_from(DMFWatchlist)),
                "watchlist_runs": session.scalar(
                    select(func.count()).select_from(DMFWatchlistRun)
                ),
                "watchlist_events": session.scalar(
                    select(func.count()).select_from(DMFWatchlistEvent)
                ),
                "monitor_runs": session.scalar(
                    select(func.count()).select_from(DMFMonitorRun)
                ),
            }
            rows = list(session.scalars(select(DMFWatchlist)))
        return {
            **counts,
            "watchlist_statuses": {row.id: row.status for row in rows},
        }

    @staticmethod
    def _intent(state: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "data_source",
            "watchlist_action",
            "watchlist_id",
            "watchlist_event_id",
            "dmf_no",
            "needs_clarification",
        )
        return {key: state.get(key) for key in keys if state.get(key) not in (None, "")}

    def _invoke(
        self,
        graph: Any,
        config: dict[str, Any],
        result: RealWatchlistResult,
        user_query: str,
    ) -> dict[str, Any]:
        before_calls = len(self.call_log.events)
        updates = list(
            graph.stream(
                {
                    "user_query": user_query,
                    "request_id": f"WATCH-REAL-001-turn-{len(result.turns) + 1}",
                    "warnings": [],
                },
                config=config,
                stream_mode="updates",
            )
        )
        state = dict(graph.get_state(config).values)
        service_calls = [
            event
            for event in self.call_log.events[before_calls:]
            if event["boundary"] == "watchlist"
        ]
        result.turns.append(
            RealTurnResult(
                index=len(result.turns),
                user_query=user_query,
                executed_nodes=[
                    node
                    for update in updates
                    for node in update
                    if node != "__interrupt__"
                ],
                answer=str(state.get("final_answer") or ""),
                intent=self._intent(state),
                service_calls=service_calls,
                database=self._database_snapshot(),
            )
        )
        return state

    @staticmethod
    def _unique_result_dmf(state: dict[str, Any]) -> str:
        values = {
            normalize_text(str(record.get("dmf_no", "")))
            for query_result in (state.get("dmf_results") or {}).get("results", [])
            for record in query_result.get("records", [])
            if normalize_text(str(record.get("dmf_no", "")))
        }
        if len(values) != 1:
            raise AssertionError(f"expected one unique DMF result, got {sorted(values)}")
        return next(iter(values))

    def _seed_event_if_needed(self, watchlist_id: str) -> tuple[str, str]:
        assert self.repository is not None
        events = self.repository.list_events(watchlist_id)
        if events:
            return events[0].id, "real_external_change"
        runs = self.repository.list_runs(watchlist_id)
        if not runs:
            raise AssertionError("real manual check did not persist a Watchlist run")
        if runs[0].status not in {"no_change", "changed"}:
            raise AssertionError(
                f"real external Watchlist check failed with status {runs[0].status}"
            )
        if not runs[0].monitor_run_id or not runs[0].snapshot_id:
            raise AssertionError(
                "real external Watchlist check did not persist history evidence"
            )
        event_id = str(uuid4())
        with self.repository.session_factory.begin() as session:
            session.add(
                DMFWatchlistEvent(
                    id=event_id,
                    watchlist_id=watchlist_id,
                    run_id=runs[0].id,
                    snapshot_id=runs[0].snapshot_id,
                    event_type="field_changed",
                    before_payload=None,
                    after_payload=None,
                    status="unread",
                    dedupe_key=f"agent-eval-seed:{event_id}",
                    created_at=datetime.now(timezone.utc),
                )
            )
        return event_id, "harness_seed_after_real_run"

    def run(self) -> RealWatchlistResult:
        assert self.history is not None and self.repository is not None
        if not self.database_path.is_relative_to(self.sandbox):
            raise RuntimeError("real Eval database must be inside the temporary sandbox")

        service = RecordingWatchlistService(
            DMFWatchlistService(self.repository, self.history),
            self.call_log,
        )
        result = RealWatchlistResult(
            dmf_no=self.dmf_no,
            database_revision=self._database_revision(),
        )
        config = {"configurable": {"thread_id": f"WATCH-REAL-001-{uuid4()}"}}

        with patch("src.agent.nodes.create_apollo_llm", self.llm_factory), patch(
            "src.agent.nodes.create_watchlist_service", lambda: service
        ):
            graph = build_react_graph(checkpointer=InMemorySaver(), llm_factory=self.llm_factory)
            query_state = self._invoke(
                graph,
                config,
                result,
                f"查询 DMF {self.dmf_no}",
            )
            observed_dmf = self._unique_result_dmf(query_state)
            if observed_dmf != normalize_text(self.dmf_no):
                raise AssertionError(
                    f"query returned {observed_dmf!r}, expected {normalize_text(self.dmf_no)!r}"
                )

            self._invoke(graph, config, result, "关注刚才的结果")
            rows = self.repository.list()
            if len(rows) != 1 or normalize_text(rows[0].dmf_no) != observed_dmf:
                raise AssertionError("Agent did not persist the expected active Watchlist")
            watchlist_id = rows[0].id

            self._invoke(graph, config, result, "查看团队关注清单")
            self._invoke(graph, config, result, f"立即检查 DMF {self.dmf_no}")
            event_id, result.event_provenance = self._seed_event_if_needed(watchlist_id)
            self._invoke(graph, config, result, f"查看 DMF {self.dmf_no} 的事件")
            self._invoke(
                graph,
                config,
                result,
                f"确认 DMF {self.dmf_no} 的事件 {event_id}",
            )
            event = next(
                item for item in self.repository.list_events(watchlist_id) if item.id == event_id
            )
            if event.status != "acknowledged" or event.acknowledged_at is None:
                raise AssertionError("Agent did not acknowledge the selected event")

            self._invoke(graph, config, result, f"取消关注 DMF {self.dmf_no}")
            deleted = self.repository.get(watchlist_id, include_deleted=True)
            if deleted.status != "deleted" or self.repository.list():
                raise AssertionError("Agent did not soft-delete the Watchlist")

        expected_methods = [
            "add",
            "list_watchlists",
            "resolve",
            "run_manual",
            "list_events",
            "acknowledge_event",
            "remove",
        ]
        actual_methods = [
            event["method"]
            for event in self.call_log.events
            if event["boundary"] == "watchlist"
        ]
        if actual_methods != expected_methods:
            raise AssertionError(
                f"unexpected Watchlist service calls: {actual_methods!r}"
            )

        return result