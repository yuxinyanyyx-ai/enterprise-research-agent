from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import RLock
from typing import Any, Iterator, Literal
from uuid import uuid4


UsageSource = Literal["actual", "estimated", "missing"]
PricingSource = Literal["actual", "estimated"]

TRACE_DIR = Path(__file__).resolve().parents[2] / "logs" / "traces"
MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5
_lock = RLock()
_handler: RotatingFileHandler | None = None
_handler_path: Path | None = None
_warned = False
_active_context: ContextVar[TraceContext | None] = ContextVar(
    "active_trace_context", default=None
)


@dataclass(frozen=True)
class TraceContext:
    trace_id: str
    request_id: str
    span_id: str
    parent_span_id: str | None


@dataclass
class TraceSpan:
    trace_id: str
    request_id: str
    span_id: str
    parent_span_id: str | None
    operation_name: str
    component: str
    model_name: str | None
    start_time: str
    duration_ms: float | None = None
    status: str = "success"
    error_type: str | None = None
    retry_count: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    token_usage_source: UsageSource = "missing"
    cost_amount: str | None = None
    cost_currency: str | None = None
    cost_source: Literal["actual", "estimated", "missing"] = "missing"
    metadata: dict[str, Any] = field(default_factory=dict)

    def set_usage(
        self,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        source: UsageSource = "missing",
    ) -> None:
        self.input_tokens = _valid_token_count(input_tokens)
        self.output_tokens = _valid_token_count(output_tokens)
        self.total_tokens = _valid_token_count(total_tokens)
        if self.total_tokens is None and (
            self.input_tokens is not None and self.output_tokens is not None
        ):
            self.total_tokens = self.input_tokens + self.output_tokens
        has_usage = any(
            value is not None
            for value in (self.input_tokens, self.output_tokens, self.total_tokens)
        )
        self.token_usage_source = source if has_usage else "missing"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PriceRule:
    model_name: str
    input_per_million: Decimal
    output_per_million: Decimal | None
    currency: str
    source: PricingSource
    output_billed: bool = True

    def __post_init__(self) -> None:
        names = (
            ("input_per_million", "output_per_million")
            if self.output_billed
            else ("input_per_million",)
        )
        if not self.output_billed and self.output_per_million is not None:
            raise ValueError("output price must be omitted when output tokens are not billed")
        for name in names:
            try:
                value = Decimal(str(getattr(self, name)))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise ValueError(f"{name} must be a non-negative decimal") from exc
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be a non-negative decimal")
            object.__setattr__(self, name, value)
        if not self.model_name.strip() or not self.currency.strip():
            raise ValueError("price rules require a model name and currency")


@dataclass(frozen=True)
class CostResult:
    amount: Decimal | None
    currency: str | None
    source: Literal["actual", "estimated", "missing"]


_SAFE_METADATA_KEYS = frozenset(
    {
        "event",
        "attempt",
        "attempt_count",
        "retryable",
        "http_status",
        "provider_error_code",
        "input_count",
        "result_count",
        "top_n",
        "recall_top_k",
        "batch_size",
        "vector_dimension",
        "tool_name",
        "tool_call_id",
        "estimated_input_tokens",
        "input_budget_tokens",
        "token_usage_completeness",
    }
)
_price_rules: tuple[PriceRule, ...] = ()


def configure_price_rules(rules: tuple[PriceRule, ...]) -> None:
    """Install explicitly sourced prices; the default registry is intentionally empty."""
    global _price_rules
    models = [rule.model_name for rule in rules]
    if len(models) != len(set(models)):
        raise ValueError("price rules must have unique model names")
    _price_rules = tuple(rules)


def configured_price_rules() -> tuple[PriceRule, ...]:
    return _price_rules


def _valid_token_count(value: int | None) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _safe_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in (metadata or {}).items():
        if key not in _SAFE_METADATA_KEYS:
            continue
        if isinstance(value, bool) or value is None:
            safe[key] = value
        elif isinstance(value, int):
            safe[key] = value
        elif isinstance(value, float) and value == value and abs(value) != float("inf"):
            safe[key] = value
        elif isinstance(value, str):
            safe[key] = value[:128]
    return safe


def calculate_cost(
    *,
    model_name: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    token_usage_source: UsageSource,
    price_rules: tuple[PriceRule, ...] = (),
) -> CostResult:
    """Return no cost unless usage and an exact model-specific rule are available."""
    if (
        not model_name
        or token_usage_source == "missing"
        or _valid_token_count(input_tokens) is None
        or (
            output_tokens is None
            and any(
                rule.model_name == model_name and rule.output_billed
                for rule in price_rules
            )
        )
        or (output_tokens is not None and _valid_token_count(output_tokens) is None)
    ):
        return CostResult(None, None, "missing")
    rule = next((item for item in price_rules if item.model_name == model_name), None)
    if rule is None:
        return CostResult(None, None, "missing")
    if rule.output_billed and (output_tokens is None or rule.output_per_million is None):
        return CostResult(None, None, "missing")
    try:
        amount = Decimal(input_tokens) * rule.input_per_million
        if rule.output_billed:
            amount += Decimal(output_tokens) * rule.output_per_million
        amount /= Decimal(1_000_000)
    except (InvalidOperation, TypeError, ValueError):
        return CostResult(None, None, "missing")
    source: Literal["actual", "estimated"] = (
        "actual"
        if token_usage_source == "actual" and rule.source == "actual"
        else "estimated"
    )
    return CostResult(amount, rule.currency, source)


def current_trace_context() -> TraceContext | None:
    return _active_context.get()


@contextmanager
def trace_span(
    operation_name: str,
    component: str,
    *,
    request_id: str | None = None,
    trace_id: str | None = None,
    parent_span_id: str | None = None,
    model_name: str | None = None,
    metadata: dict[str, Any] | None = None,
    price_rules: tuple[PriceRule, ...] | None = None,
) -> Iterator[TraceSpan]:
    active = _active_context.get()
    parent_span_id = parent_span_id or None
    resolved_trace_id = trace_id or (active.trace_id if active else uuid4().hex)
    resolved_request_id = request_id or (active.request_id if active else uuid4().hex)
    if active is not None and (
        active.trace_id == resolved_trace_id
        and active.request_id == resolved_request_id
    ):
        parent_span_id = active.span_id
    started = time.perf_counter()
    span = TraceSpan(
        trace_id=resolved_trace_id,
        request_id=resolved_request_id,
        span_id=uuid4().hex,
        parent_span_id=parent_span_id,
        operation_name=operation_name[:128],
        component=component[:128],
        model_name=model_name[:128] if model_name else None,
        start_time=datetime.now(UTC).isoformat(),
        metadata=_safe_metadata(metadata),
    )
    token = _active_context.set(TraceContext(
        span.trace_id, span.request_id, span.span_id, span.parent_span_id
    ))
    try:
        yield span
    except asyncio.CancelledError:
        span.status = "cancelled"
        span.error_type = "CancelledError"
        raise
    except Exception as exc:
        span.status = "error"
        span.error_type = type(exc).__name__
        raise
    finally:
        span.duration_ms = round((time.perf_counter() - started) * 1000, 3)
        quote = calculate_cost(
            model_name=span.model_name,
            input_tokens=span.input_tokens,
            output_tokens=span.output_tokens,
            token_usage_source=span.token_usage_source,
            price_rules=price_rules if price_rules is not None else configured_price_rules(),
        )
        if quote.amount is not None:
            span.cost_amount = str(quote.amount)
            span.cost_currency = quote.currency
        span.cost_source = quote.source
        _active_context.reset(token)
        _write_span(span)


def _write_span(span: TraceSpan) -> None:
    global _handler, _handler_path, _warned
    try:
        path = TRACE_DIR / f"trace-{os.getpid()}.jsonl"
        payload = json.dumps(span.to_dict(), ensure_ascii=True, separators=(",", ":"))
        with _lock:
            if _handler is None or _handler_path != path:
                if _handler is not None:
                    _handler.close()
                path.parent.mkdir(parents=True, exist_ok=True)
                _handler = RotatingFileHandler(
                    path,
                    maxBytes=MAX_BYTES,
                    backupCount=BACKUP_COUNT,
                    encoding="utf-8",
                )
                _handler.setFormatter(logging.Formatter("%(message)s"))
                _handler_path = path
            _handler.handle(
                logging.LogRecord("trace.span", logging.INFO, "", 0, payload, (), None)
            )
    except Exception:
        if not _warned:
            _warned = True
            logging.getLogger(__name__).warning("Trace sink unavailable")
