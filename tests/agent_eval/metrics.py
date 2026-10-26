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
    token_usage = {
        source: {
            "calls": 0,
            "input_calls": 0,
            "input": 0,
            "output_calls": 0,
            "output": 0,
            "total_calls": 0,
            "total": 0,
        }
        for source in ("actual", "estimated", "missing")
    }
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
                    source = str(metric.get("token_source", "missing"))
                    if source not in token_usage:
                        source = "missing"
                    bucket = token_usage[source]
                    bucket["calls"] += 1
                    input_tokens = metric.get("input_tokens")
                    output_tokens = metric.get("output_tokens")
                    total_tokens = metric.get("total_tokens")
                    if isinstance(input_tokens, int) and not isinstance(input_tokens, bool) and input_tokens >= 0:
                        bucket["input"] += input_tokens
                        bucket["input_calls"] += 1
                    else:
                        input_tokens = None
                    if isinstance(output_tokens, int) and not isinstance(output_tokens, bool) and output_tokens >= 0:
                        bucket["output"] += output_tokens
                        bucket["output_calls"] += 1
                    else:
                        output_tokens = None
                    if isinstance(total_tokens, int) and not isinstance(total_tokens, bool) and total_tokens >= 0:
                        bucket["total"] += total_tokens
                        bucket["total_calls"] += 1
                    elif input_tokens is not None and output_tokens is not None:
                        bucket["total"] += input_tokens + output_tokens
                        bucket["total_calls"] += 1
                    token_sources[source] += 1

    serialized_usage = {}
    for source, bucket in token_usage.items():
        serialized_usage[source] = {
            "calls": bucket["calls"],
            "input": bucket["input"] if bucket["input_calls"] else None,
            "input_calls": bucket["input_calls"],
            "output": bucket["output"] if bucket["output_calls"] else None,
            "output_calls": bucket["output_calls"],
            "total": bucket["total"] if bucket["total_calls"] else None,
            "total_calls": bucket["total_calls"],
        }

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
            **serialized_usage,
            "missing_usage_calls": token_usage["missing"]["calls"],
            "sources": dict(sorted(token_sources.items())),
        },
        "failure_reasons": dict(sorted(failures.items())),
    }
