from __future__ import annotations

from math import ceil
from typing import Any


def percentile(values: list[float], percent: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, ceil(percent * len(ordered) / 100) - 1)
    return round(ordered[index], 3)


def retrieval_case_metrics(
    ranked_ids: list[str], expected_ids: list[str]
) -> dict[str, float | None]:
    if not expected_ids:
        return {
            "recall_at_5": None,
            "recall_at_10": None,
            "mrr": None,
            "hit_rate": None,
        }
    expected = set(expected_ids)
    positions = [index for index, item in enumerate(ranked_ids, 1) if item in expected]
    reciprocal_rank = 1 / positions[0] if positions else 0.0
    return {
        "recall_at_5": len(expected.intersection(ranked_ids[:5])) / len(expected),
        "recall_at_10": len(expected.intersection(ranked_ids[:10])) / len(expected),
        "mrr": reciprocal_rank,
        "hit_rate": float(bool(positions)),
    }


def summarize_strategy(results: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [row for row in results if row.get("status") == "ok"]
    retrieval_rows = [
        row for row in valid if row.get("retrieval_metrics", {}).get("recall_at_5") is not None
    ]
    answer_rows = [row for row in valid if row.get("answer_evaluation")]

    def mean(metric: str) -> float | None:
        values = [
            float(row["retrieval_metrics"][metric])
            for row in retrieval_rows
            if row["retrieval_metrics"].get(metric) is not None
        ]
        return round(sum(values) / len(values), 4) if values else None

    latency = [float(row["latency_ms"]) for row in valid]
    answers = [row["answer_evaluation"] for row in answer_rows]
    human_rows = [
        row for row in valid if isinstance(row.get("human_evaluation"), dict)
    ]

    def answer_mean(metric: str) -> float | None:
        values = [float(row[metric]) for row in answers if row.get(metric) is not None]
        return round(sum(values) / len(values), 4) if values else None

    def human_mean(metric: str) -> float | None:
        values = [
            float(row["human_evaluation"][metric])
            for row in human_rows
            if row["human_evaluation"].get(metric) is not None
        ]
        return round(sum(values) / len(values), 4) if values else None

    refusal_rows = [
        row for row in answer_rows if row.get("answerability") == "unanswerable"
    ]
    conflict_rows = [row for row in answer_rows if row.get("answerability") == "conflict"]
    return {
        "case_count": len(results),
        "successful_case_count": len(valid),
        "failed_case_count": len(results) - len(valid),
        "retrieval": {
            "recall_at_5": mean("recall_at_5"),
            "recall_at_10": mean("recall_at_10"),
            "mrr": mean("mrr"),
            "hit_rate": mean("hit_rate"),
            "query_latency_p50_ms": percentile(latency, 50),
            "query_latency_p95_ms": percentile(latency, 95),
        },
        "answer_evaluation_auto": {
            "answer_correctness": answer_mean("answer_correctness"),
            "faithfulness": answer_mean("faithfulness"),
            "citation_precision": answer_mean("citation_precision"),
            "correct_refusal_rate": (
                round(
                    sum(
                        bool(row["answer_evaluation"].get("correct_refusal"))
                        for row in refusal_rows
                    )
                    / len(refusal_rows),
                    4,
                )
                if refusal_rows else None
            ),
            "conflict_identification_rate": (
                round(
                    sum(
                        bool(row["answer_evaluation"].get("conflict_identified"))
                        for row in conflict_rows
                    )
                    / len(conflict_rows),
                    4,
                )
                if conflict_rows else None
            ),
        },
        "answer_evaluation_human": {
            "audited_case_count": len(human_rows),
            "answer_correctness": human_mean("answer_correctness"),
            "faithfulness": human_mean("faithfulness"),
            "citation_precision": human_mean("citation_precision"),
            "correct_refusal_rate": (
                round(
                    sum(
                        bool(row["human_evaluation"].get("correct_refusal"))
                        for row in human_rows
                        if row.get("answerability") == "unanswerable"
                    )
                    / sum(row.get("answerability") == "unanswerable" for row in human_rows),
                    4,
                )
                if any(row.get("answerability") == "unanswerable" for row in human_rows)
                else None
            ),
            "conflict_identification_rate": (
                round(
                    sum(
                        bool(row["human_evaluation"].get("conflict_identified"))
                        for row in human_rows
                        if row.get("answerability") == "conflict"
                    )
                    / sum(row.get("answerability") == "conflict" for row in human_rows),
                    4,
                )
                if any(row.get("answerability") == "conflict" for row in human_rows)
                else None
            ),
        },
        "calls": {
            "llm": sum(row.get("calls", {}).get("llm", 0) for row in results),
            "embedding": sum(row.get("calls", {}).get("embedding", 0) for row in results),
            "rerank": sum(row.get("calls", {}).get("rerank", 0) for row in results),
            "input_tokens": sum(row.get("calls", {}).get("input_tokens", 0) for row in results),
            "output_tokens": sum(row.get("calls", {}).get("output_tokens", 0) for row in results),
            "embedding_tokens": None,
            "rerank_tokens": None,
            "estimated_cost": None,
        },
    }
