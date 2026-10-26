"""Local, payload-free Agent execution logs, independent of root logging."""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import RLock
from uuid import uuid4

from src.observability.tracing import current_trace_context, trace_span


LOG_DIR = Path(__file__).resolve().parents[2] / "logs" / "agent"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3
_lock = RLock()
_handler: RotatingFileHandler | None = None
_handler_path: Path | None = None
_warned = False


def write_event(event: str, state, *, call=None, **details) -> None:
    """Persist metadata only; logging must never change business outcomes."""
    global _handler, _handler_path, _warned
    try:
        active = current_trace_context()
        state_request_id = str(state.get("request_id") or "")[:128]
        state_trace_id = str(state.get("trace_id") or "")[:128]
        active_matches = active is not None and (
            not state_request_id or active.request_id == state_request_id
        ) and (
            not state_trace_id or active.trace_id == state_trace_id
        )
        request_id = state_request_id or (active.request_id if active else "")
        trace_id = (
            state_trace_id
            or (active.trace_id if active_matches else "")
            or request_id
            or uuid4().hex
        )
        span_id = (
            (active.span_id if active_matches else None)
            or str(state.get("root_span_id") or "")
            or uuid4().hex
        )
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "pid": os.getpid(),
            "trace_id": trace_id,
            "request_id": request_id,
            "span_id": span_id,
            "parent_span_id": active.parent_span_id if active_matches else None,
            "tool_call_id": str((call or {}).get("id") or "")[:128],
            "tool_name": str((call or {}).get("name") or "")[:128],
        }
        allowed = {
            "execution_id", "duration_ms", "status", "error_type", "reason",
            "failure_code",
            "round", "argument_count", "operation", "decision",
            "estimated_input_tokens", "input_budget_tokens", "source_message_count", "sent_message_count",
            "context_view", "emergency_fallback",
            "artifact_resolution", "source_tool_call_id", "error_code", "result_count",
        }
        payload.update({key: value for key, value in details.items() if key in allowed})
        message = json.dumps(payload, ensure_ascii=True)
        path = LOG_DIR / f"agent-{os.getpid()}.jsonl"
        with _lock:
            if _handler is None or _handler_path != path:
                if _handler is not None:
                    _handler.close()
                    _handler = None
                path.parent.mkdir(parents=True, exist_ok=True)
                _handler = RotatingFileHandler(
                    path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT,
                    encoding="utf-8",
                )
                _handler.setFormatter(logging.Formatter("%(message)s"))
                _handler_path = path
            _handler.handle(logging.LogRecord(
                "agent.audit", logging.INFO, "", 0, message, (), None,
            ))
    except Exception:
        if not _warned:
            _warned = True
            logging.getLogger(__name__).warning("Agent audit log unavailable")


@contextmanager
def trace_operation(state, *, call=None, operation="tool"):
    execution_id = uuid4().hex
    metadata = {"execution_id": execution_id, "operation": operation}
    request_id = str(state.get("request_id") or "") or None
    trace_id = str(state.get("trace_id") or "") or None
    parent_span_id = str(state.get("root_span_id") or "") or None
    component = "agent.model" if operation == "react.model" else "agent.tool"
    span_metadata = {
        **({"tool_name": str((call or {}).get("name") or "")[:128]} if call else {}),
        **({"tool_call_id": str((call or {}).get("id") or "")[:128]} if call else {}),
    }
    if operation == "react.model":
        with _audit_operation_events(state, call, metadata, operation, None) as outcome:
            yield outcome
        return
    with trace_span(
        f"agent.{operation}",
        component,
        request_id=request_id,
        trace_id=trace_id,
        parent_span_id=parent_span_id,
        metadata=span_metadata,
    ) as span:
        with _audit_operation_events(state, call, metadata, operation, span) as outcome:
            yield outcome


@contextmanager
def _audit_operation_events(state, call, metadata, operation, span):
    started = time.perf_counter()
    write_event("execution.started", state, call=call, **metadata,
                argument_count=len((call or {}).get("args") or {}))
    outcome = {"status": "completed"}
    try:
        yield outcome
    except Exception as exc:
        outcome = {
            "status": "error",
            "error_type": type(exc).__name__,
            "failure_code": (
                "llm_provider_error" if operation == "react.model" else "operation_error"
            ),
        }
        if span is not None:
            span.status = "error"
            span.error_type = type(exc).__name__
        raise
    finally:
        if outcome["status"] == "business_failure":
            outcome.setdefault("failure_code", "business_operation_failed")
        if span is not None and outcome["status"] != "completed":
            span.status = "error" if outcome["status"] == "error" else outcome["status"]
        if span is not None and outcome.get("error_type"):
            span.error_type = outcome["error_type"]
        if span is not None and isinstance(outcome.get("result_count"), int):
            span.metadata["result_count"] = outcome["result_count"]
        write_event("execution.finished", state, call=call, **metadata, **outcome,
                    duration_ms=round((time.perf_counter() - started) * 1000, 3))