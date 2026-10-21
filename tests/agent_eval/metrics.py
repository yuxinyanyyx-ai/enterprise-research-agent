"""Aggregate task, tool-selection, latency, token, and failure metrics."""

from __future__ import annotations

from collections import Counter
from math import ceil
from typing import Any, Sequence

from tests.agent_eval.runner import CaseResult


def _percentile(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, ceil(percentile * len(ordered) / 100) - 1)], 3)


def summarize_results(results: Sequence[CaseResult]) -> dict[str, Any]:
    evaluated = [result for result in results if result.task_success is not None]
    successful = sum(result.task_success is True for result in evaluated)
    expected_decisions = correct_decisions = 0
    for result in results:
        for turn in result.turns:
            if turn.expected_tool_decisions is None:
                continue
            expected = turn.expected_tool_decisions
            actual = turn.selected_tool_decisions
            count = max(len(expected), len(actual))
            expected_decisions += count
            correct_decisions += sum(
                index < len(expected)
                and index < len(actual)
                and expected[index] == actual[index]
                for index in range(count)
            )

    call_counts: Counter[str] = Counter()
    request_latencies: list[float] = []
    llm_latencies: list[float] = []
    token_totals: Counter[str] = Counter()
    token_sources: Counter[str] = Counter()
    failures: Counter[str] = Counter()
    for result in results:
        if result.task_success is False:
            failures[result.failure_reason or "unknown_failure"] += 1
        if result.turns:
            for turn in result.turns:
                request_latencies.append(turn.duration_ms)
                for name, count in turn.call_deltas.items():
                    call_counts[name] += count
                for metric in turn.llm_metrics:
                    llm_latencies.append(float(metric.get("duration_ms", 0)))
                    token_totals["input"] += int(metric.get("input_tokens", 0))
                    token_totals["output"] += int(metric.get("output_tokens", 0))
                    token_sources[str(metric.get("token_source", "unknown"))] += 1

    return {
        "case_count": len(results),
        "evaluated_case_count": len(evaluated),
        "successful_case_count": successful,
        "task_success_rate": successful / len(evaluated) if evaluated else None,
        "tool_selection": {
            "correct_decisions": correct_decisions,
            "total_decisions": expected_decisions,
            "accuracy": correct_decisions / expected_decisions if expected_decisions else None,
        },
        "tool_call_counts": dict(sorted(call_counts.items())),
        "latency_ms": {
            "request_p50": _percentile(request_latencies, 50),
            "request_p95": _percentile(request_latencies, 95),
            "llm_p50": _percentile(llm_latencies, 50),
            "llm_p95": _percentile(llm_latencies, 95),
        },
        "token_consumption": {
            "input": token_totals["input"],
            "output": token_totals["output"],
            "total": token_totals["input"] + token_totals["output"],
            "sources": dict(sorted(token_sources.items())),
        },
        "failure_reasons": dict(sorted(failures.items())),
    }
