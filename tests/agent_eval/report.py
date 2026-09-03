"""Redacted JSON and Markdown reports for Agent Eval results."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
        "manual_review": None,
        "turns": [
            {
                "turn": turn.index + 1,
                "executed_nodes": turn.executed_nodes,
                "pending_nodes": turn.pending_nodes,
                "interrupted": turn.interrupted,
                "call_deltas": turn.call_deltas,
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
