"""Redacted JSON and Markdown reports for Agent Eval results."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tests.agent_eval.metrics import summarize_results
from tests.agent_eval.runner import CaseResult


_SECRET_PATTERNS = (
    re.compile(r"(?i)authorization\s*[:=]\s*(?:bearer\s+)?\S+"),
    re.compile(r"(?i)bearer\s+\S+"),
    re.compile(r"(?i)(?:api[_-]?key|token|cookie|captcha)\s*[:=]\s*\S+"),
)


def redact_text(value: str) -> str:
    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def _payload(result: CaseResult, model: str) -> dict[str, Any]:
    return {
        "case_id": result.case_id,
        "mode": result.mode,
        "model": model,
        "task_success": result.task_success,
        "failure_reason": result.failure_reason,
        "manual_review": None,
        "turns": [
            {
                "turn": turn.index + 1,
                "executed_nodes": turn.executed_nodes,
                "pending_nodes": turn.pending_nodes,
                "interrupted": turn.interrupted,
                "call_deltas": turn.call_deltas,
                "duration_ms": turn.duration_ms,
                "expected_tool_decisions": turn.expected_tool_decisions,
                "selected_tool_decisions": turn.selected_tool_decisions,
                "llm_metrics": turn.llm_metrics,
                "answer": redact_text(turn.answer),
            }
            for turn in result.turns
        ],
    }


def write_report(
    result: CaseResult,
    *,
    model: str,
    output_root: str | Path = "outputs/agent_eval",
) -> Path:
    """Write one result without persisting prompts, raw fixtures, or call payloads."""

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output_dir = Path(output_root) / timestamp
    output_dir.mkdir(parents=True, exist_ok=False)
    payload = _payload(result, model)
    (output_dir / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    lines = [
        f"# Agent Eval: {result.case_id}",
        "",
        f"- Mode: {result.mode}",
        f"- Model: {model}",
        "- Manual review: pending",
    ]
    for turn in payload["turns"]:
        lines.extend(
            [
                "",
                f"## Turn {turn['turn']}",
                f"- Nodes: {', '.join(turn['executed_nodes'])}",
                f"- Calls: {turn['call_deltas']}",
                "",
                str(turn["answer"]),
            ]
        )
    (output_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return output_dir


def write_summary_report(
    results: list[CaseResult],
    *,
    model: str,
    output_root: str | Path = "outputs/agent_eval",
) -> Path:
    """Write aggregate metrics without persisting prompts or tool arguments."""

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output_dir = Path(output_root) / f"{timestamp}-summary"
    output_dir.mkdir(parents=True, exist_ok=False)
    metrics = summarize_results(results)
    payload = {
        "mode": "live",
        "model": model,
        "metrics": metrics,
        "cases": [
            {
                "case_id": result.case_id,
                "task_success": result.task_success,
                "failure_reason": result.failure_reason,
            }
            for result in results
        ],
    }
    (output_dir / "metrics.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# Agent Eval Summary",
        "",
        f"- Model: {model}",
        f"- Cases: {metrics['case_count']}",
        f"- Task success: {metrics['successful_case_count']}/{metrics['evaluated_case_count']}",
        f"- Task success rate: {_percentage(metrics['task_success_rate'])}",
        f"- Tool selection accuracy: {_percentage(metrics['tool_selection']['accuracy'])} "
        f"({metrics['tool_selection']['correct_decisions']}/{metrics['tool_selection']['total_decisions']})",
        f"- Request latency P50/P95: {metrics['latency_ms']['request_p50']}/"
        f"{metrics['latency_ms']['request_p95']} ms",
        f"- LLM latency P50/P95: {metrics['latency_ms']['llm_p50']}/"
        f"{metrics['latency_ms']['llm_p95']} ms",
        f"- Tokens (input/output/total): {metrics['token_consumption']['input']}/"
        f"{metrics['token_consumption']['output']}/{metrics['token_consumption']['total']}",
        f"- Token usage sources: {metrics['token_consumption']['sources']}",
        f"- Tool calls: {metrics['tool_call_counts']}",
        f"- Failure reasons: {metrics['failure_reasons']}",
    ]
    (output_dir / "metrics.md").write_text("\n".join(lines), encoding="utf-8")
    return output_dir


def _percentage(value: float | None) -> str:
    return "not measured" if value is None else f"{value:.1%}"
