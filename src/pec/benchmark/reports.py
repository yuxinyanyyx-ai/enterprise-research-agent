from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.pec.benchmark.metrics import summarize_strategy


def write_report(payload: dict[str, Any], output_root: Path) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = output_root / timestamp
    output.mkdir()
    (output / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (output / "report.md").write_text(render_markdown(payload), encoding="utf-8")
    return output


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# PEC RAG Benchmark",
        "",
        f"- Run: `{payload.get('run_id', '')}`",
        f"- Status: `{payload.get('status', '')}`",
        f"- Git commit: `{payload.get('git_commit', '')}`",
        f"- Dataset version: `{payload.get('dataset_version', '')}`",
        f"- Index generation: `{payload.get('index_generation', '')}`",
        f"- Cases: {payload.get('case_count', 0)}",
        f"- Answer generation: {payload.get('answer_mode', False)}",
        "",
        "> Automatic LLM judge scores are not human validation. Human score fields remain separate.",
        "",
        "## Strategy results",
        "",
        "| Strategy | Cases | Recall@5 | Recall@10 | MRR | Hit rate | P50 ms | P95 ms | LLM calls | Embeddings | Reranks |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    strategies = payload.get("strategies", {})
    for name, summary in strategies.items():
        retrieval = summary.get("retrieval", {})
        calls = summary.get("calls", {})
        lines.append(
            f"| {name} | {summary.get('case_count', 0)} | "
            f"{_display(retrieval.get('recall_at_5'))} | "
            f"{_display(retrieval.get('recall_at_10'))} | "
            f"{_display(retrieval.get('mrr'))} | "
            f"{_display(retrieval.get('hit_rate'))} | "
            f"{_display(retrieval.get('query_latency_p50_ms'))} | "
            f"{_display(retrieval.get('query_latency_p95_ms'))} | "
            f"{calls.get('llm', 0)} | {calls.get('embedding', 0)} | {calls.get('rerank', 0)} |"
        )
    ablation = payload.get("topic_ablation")
    if ablation:
        mixed = ablation.get("mixed_raw_and_topic", {})
        raw = ablation.get("raw_only", {})
        lines.extend(
            [
                "",
                "## Topic + raw Chunk ablation",
                "",
                f"Index contains {ablation.get('topic_chunk_count', 0)} topic chunks and "
                f"{ablation.get('raw_chunk_count', 0)} non-topic chunks. Both rankings share the "
                "same vector query call and index generation.",
                "",
                "| Corpus | Recall@5 | Recall@10 | MRR | Hit rate |",
                "|---|---:|---:|---:|---:|",
                f"| Raw + Topic | {_display(mixed.get('recall_at_5'))} | "
                f"{_display(mixed.get('recall_at_10'))} | {_display(mixed.get('mrr'))} | "
                f"{_display(mixed.get('hit_rate'))} |",
                f"| Raw only | {_display(raw.get('recall_at_5'))} | "
                f"{_display(raw.get('recall_at_10'))} | {_display(raw.get('mrr'))} | "
                f"{_display(raw.get('hit_rate'))} |",
                "",
                f"Recall@5 delta (Raw + Topic - Raw only): "
                f"{_display(ablation.get('recall_at_5_delta_mixed_minus_raw'))}",
            ]
        )
    lines.extend(["", "## Answer evaluation (automatic)", ""])
    if not payload.get("answer_mode"):
        lines.append("Not run (retrieval-only run).")
    else:
        lines.extend(
            [
                "| Strategy | Correctness | Faithfulness | Citation precision | Correct refusal | Conflict identification |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for name, summary in strategies.items():
            metrics = summary.get("answer_evaluation_auto", {})
            lines.append(
                f"| {name} | {_display(metrics.get('answer_correctness'))} | "
                f"{_display(metrics.get('faithfulness'))} | "
                f"{_display(metrics.get('citation_precision'))} | "
                f"{_display(metrics.get('correct_refusal_rate'))} | "
                f"{_display(metrics.get('conflict_identification_rate'))} |"
            )
    lines.extend(["", "## Answer evaluation (human audit)", ""])
    if not payload.get("answer_mode"):
        lines.append("Not run (retrieval-only run).")
    else:
        lines.extend(
            [
                "| Strategy | Audited cases | Correctness | Faithfulness | Citation precision | Correct refusal | Conflict identification |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for name, summary in strategies.items():
            metrics = summary.get("answer_evaluation_human", {})
            lines.append(
                f"| {name} | {metrics.get('audited_case_count', 0)} | "
                f"{_display(metrics.get('answer_correctness'))} | "
                f"{_display(metrics.get('faithfulness'))} | "
                f"{_display(metrics.get('citation_precision'))} | "
                f"{_display(metrics.get('correct_refusal_rate'))} | "
                f"{_display(metrics.get('conflict_identification_rate'))} |"
            )
    failures = payload.get("failures", [])
    lines.extend(["", "## Failed cases", ""])
    if not failures:
        lines.append("None.")
    else:
        lines.extend(["| Case | Strategy | Stage | Error |", "|---|---|---|---|"])
        for failure in failures:
            lines.append(
                f"| {failure.get('case_id')} | {failure.get('strategy')} | "
                f"{failure.get('stage')} | {failure.get('error')} |"
            )
    lines.extend(
        [
            "",
            "## Reproduction metadata",
            "",
            f"- Retrieval config: `{json.dumps(payload.get('retrieval_config', {}), ensure_ascii=False, sort_keys=True)}`",
            f"- Models: `{json.dumps(payload.get('models', {}), ensure_ascii=False, sort_keys=True)}`",
            "- Embedding/rerank token usage and price are unavailable unless the Apollo endpoint returns usage/pricing metadata; these are reported as null, not estimated.",
            "- Manual audit metrics are not inferred from automatic scoring.",
            "",
        ]
    )
    return "\n".join(lines)


def compare_reports(first_path: Path, second_path: Path) -> dict[str, Any]:
    first = json.loads(first_path.read_text(encoding="utf-8"))
    second = json.loads(second_path.read_text(encoding="utf-8"))
    comparisons = {}
    for strategy in sorted(set(first.get("strategies", {})) | set(second.get("strategies", {}))):
        left = first.get("strategies", {}).get(strategy, {}).get("retrieval", {})
        right = second.get("strategies", {}).get(strategy, {}).get("retrieval", {})
        comparisons[strategy] = {
            metric: (
                round(right[metric] - left[metric], 4)
                if isinstance(left.get(metric), (int, float))
                and isinstance(right.get(metric), (int, float))
                else None
            )
            for metric in ("recall_at_5", "recall_at_10", "mrr", "hit_rate")
        }
    return {
        "first_run": first.get("run_id"),
        "second_run": second.get("run_id"),
        "same_dataset": first.get("dataset_version") == second.get("dataset_version"),
        "same_index": first.get("index_generation") == second.get("index_generation"),
        "retrieval_metric_deltas": comparisons,
    }


def record_human_audit(
    report_path: Path,
    *,
    case_id: str,
    strategy: str,
    auditor: str,
    note: str,
    answer_correctness: float,
    faithfulness: float,
    citation_precision: float,
    correct_refusal: bool,
    conflict_identified: bool,
) -> dict[str, Any]:
    scores = (answer_correctness, faithfulness, citation_precision)
    if any(not 0 <= score <= 1 for score in scores):
        raise ValueError("human scores must be between 0 and 1")
    if not auditor.strip() or not note.strip():
        raise ValueError("auditor and audit note are required")
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    strategy_data = payload.get("strategies", {}).get(strategy)
    if strategy_data is None:
        raise ValueError(f"strategy not found in report: {strategy}")
    case = next(
        (row for row in strategy_data.get("cases", []) if row.get("case_id") == case_id),
        None,
    )
    if case is None or case.get("status") != "ok" or case.get("answer") is None:
        raise ValueError("human audit requires a successful answer-generation case")
    case["human_evaluation"] = {
        "answer_correctness": answer_correctness,
        "faithfulness": faithfulness,
        "citation_precision": citation_precision,
        "correct_refusal": correct_refusal,
        "conflict_identified": conflict_identified,
        "auditor": auditor.strip(),
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "note": note.strip(),
    }
    summary = summarize_strategy(strategy_data["cases"])
    summary["cases"] = strategy_data["cases"]
    payload["strategies"][strategy] = summary
    audit_entries = payload.setdefault(
        "human_audit", {"status": "not_started", "sampled_case_ids": [], "metrics": None}
    )
    audit_case = {"case_id": case_id, "strategy": strategy}
    sampled = audit_entries.setdefault("sampled_case_ids", [])
    if audit_case not in sampled:
        sampled.append(audit_case)
    expected = sum(
        row.get("status") == "ok" and row.get("answer") is not None
        for section in payload.get("strategies", {}).values()
        for row in section.get("cases", [])
    )
    audit_entries["status"] = (
        "complete" if len(sampled) >= expected and expected else "partially_reviewed"
    )
    audit_entries["audited_case_count"] = len(sampled)
    payload["strategies"][strategy] = summary
    report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    report_path.with_name("report.md").write_text(
        render_markdown(payload), encoding="utf-8"
    )
    return case["human_evaluation"]


def _display(value: Any) -> str:
    return "n/a" if value is None else str(value)
