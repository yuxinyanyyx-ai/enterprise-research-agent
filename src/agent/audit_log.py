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
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "pid": os.getpid(),
            "request_id": str(state.get("request_id") or "")[:128],
            "tool_call_id": str((call or {}).get("id") or "")[:128],
            "tool_name": str((call or {}).get("name") or "")[:128],
        }
        allowed = {
            "execution_id", "duration_ms", "status", "error_type", "reason",
            "round", "argument_count", "operation", "decision",
            "estimated_input_tokens", "input_budget_tokens", "source_message_count", "sent_message_count",
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
    started = time.perf_counter()
    metadata = {"execution_id": execution_id, "operation": operation}
    write_event("execution.started", state, call=call, **metadata,
                argument_count=len((call or {}).get("args") or {}))
    outcome = {"status": "completed"}
    try:
        yield outcome
    except Exception as exc:
        outcome = {"status": "error", "error_type": type(exc).__name__}
        raise
    finally:
        write_event("execution.finished", state, call=call, **metadata, **outcome,
                    duration_ms=round((time.perf_counter() - started) * 1000, 3))