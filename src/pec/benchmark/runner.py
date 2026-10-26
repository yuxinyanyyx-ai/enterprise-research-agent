from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from src.llm import apollo
from src.pec.benchmark.dataset import approved_cases, dataset_version
from src.pec.benchmark.metrics import retrieval_case_metrics, summarize_strategy
from src.pec.benchmark.retrieval import (
    STRATEGIES,
    BenchmarkRetriever,
    CallBudget,
)
from src.pec.benchmark.reports import write_report
from src.pec.knowledge.chunk_index import (
    _embeddings_ready,
    _load_chunks,
    _load_embeddings,
    _load_manifest,
)
from src.pec.knowledge.lifecycle import visible
from src.pec.knowledge.storage import active_dir


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _safe_error(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if response is not None:
        return f"{type(exc).__name__} (HTTP {response.status_code})"
    return type(exc).__name__


def _load_index() -> tuple[Path, dict[str, Any], list[dict[str, Any]], np.ndarray]:
    directory = active_dir()
    manifest = _load_manifest(directory)
    chunks = _load_chunks(directory)
    matrix = _load_embeddings(directory, mmap=True)
    if matrix is None or not _embeddings_ready(manifest, len(chunks), matrix):
        raise RuntimeError("published PEC index embeddings are missing or inconsistent")
    if matrix.ndim != 2 or len(matrix) != len(chunks):
        raise RuntimeError("published PEC index vector shape does not match chunks")
    return directory, manifest, chunks, matrix


def _index_evidence(chunks: list[dict[str, Any]], manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        chunk["chunk_id"]: chunk
        for chunk in chunks
        if chunk.get("method") != "topic"
        and visible(manifest, chunk.get("source_ref", ""))
    }


def _case_result(
    *,
    case: dict[str, Any],
    strategy: str,
    retriever: BenchmarkRetriever,
    answer_mode: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    start = dict(retriever.budget.snapshot())
    stage = "retrieval"
    try:
        retrieved = retriever.retrieve(case["question"], strategy)
        row: dict[str, Any] = {
            "case_id": case["case_id"],
            "question": case["question"],
            "question_type": case["question_type"],
            "answerability": case["answerability"],
            "expected_source_files": case["expected_source_files"],
            "expected_evidence_ids": case["expected_evidence_ids"],
            "reference_answer": case["reference_answer"],
            "reviewer": case.get("reviewer"),
            "status": "ok",
            "hits": retrieved["hits"],
            "hit_sources": list(dict.fromkeys(hit["source_ref"] for hit in retrieved["hits"])),
            "ranked_evidence_ids": retrieved["ranked_evidence_ids"],
            "topic_ablation_raw_only": retrieved["topic_ablation_raw_only"],
            "topic_ablation_raw_only_metrics": retrieval_case_metrics(
                [
                    hit["chunk_id"]
                    for hit in retrieved["topic_ablation_raw_only"]
                ],
                case["expected_evidence_ids"],
            ),
            "retrieval_metrics": retrieval_case_metrics(
                retrieved["ranked_evidence_ids"], case["expected_evidence_ids"]
            ),
            "latency_ms": retrieved["latency_ms"],
            "answer_evaluation": None,
            "answer": None,
            "warnings": retrieved["warnings"],
            "calls": {},
            "human_evaluation": None,
        }
        if answer_mode:
            stage = "answer_generation"
            generated = retriever.answer(case, retrieved["hits"])
            stage = "answer_judging"
            judged = retriever.judge(case, generated, retrieved["hits"])
            row["answer"] = generated
            row["answer_evaluation"] = judged
        now = retriever.budget.snapshot()
        row["calls"] = {
            key: int(now[key] or 0) - int(start[key] or 0)
            for key in ("llm", "embedding", "rerank", "input_tokens", "output_tokens")
        }
        return row, []
    except Exception as exc:
        now = retriever.budget.snapshot()
        row = {
            "case_id": case["case_id"],
            "question": case["question"],
            "question_type": case["question_type"],
            "answerability": case["answerability"],
            "expected_source_files": case["expected_source_files"],
            "expected_evidence_ids": case["expected_evidence_ids"],
            "reference_answer": case["reference_answer"],
            "reviewer": case.get("reviewer"),
            "status": "error",
            "error_stage": stage,
            "error": _safe_error(exc),
            "hits": [],
            "hit_sources": [],
            "ranked_evidence_ids": [],
            "topic_ablation_raw_only": [],
            "topic_ablation_raw_only_metrics": retrieval_case_metrics(
                [], case["expected_evidence_ids"]
            ),
            "retrieval_metrics": retrieval_case_metrics([], case["expected_evidence_ids"]),
            "latency_ms": None,
            "answer_evaluation": None,
            "answer": None,
            "warnings": [],
            "calls": {
                key: int(now[key] or 0) - int(start[key] or 0)
                for key in ("llm", "embedding", "rerank", "input_tokens", "output_tokens")
            },
            "human_evaluation": None,
        }
        return row, [
            {
                "case_id": case["case_id"],
                "strategy": strategy,
                "stage": stage,
                "error": row["error"],
            }
        ]


def run_benchmark(
    *,
    dataset_path: Path,
    output_root: Path,
    selected_cases: list[dict[str, Any]],
    max_calls: int,
    answer_mode: bool,
    candidate_k: int = 40,
    top_n: int = 10,
) -> Path:
    directory, manifest, chunks, matrix = _load_index()
    if not selected_cases:
        raise ValueError("no approved cases selected for evaluation")
    evidence_by_id = _index_evidence(chunks, manifest)
    cases = approved_cases(selected_cases, evidence_by_id=evidence_by_id)
    if len(cases) != len(selected_cases):
        raise ValueError("formal evaluation may only include approved cases")
    calls_per_case = 18 if answer_mode else 10
    required_budget = calls_per_case * len(cases)
    if max_calls < required_budget:
        raise ValueError(
            f"max_calls={max_calls} is insufficient for {len(cases)} cases; "
            f"allow at least {required_budget} calls to compare all four strategies"
        )
    embedding_model = (os.getenv("APOLLO_EMBEDDING_MODEL") or "").strip()
    if manifest.get("embedding_model") != embedding_model:
        raise RuntimeError("APOLLO_EMBEDDING_MODEL differs from the published index model")
    token = apollo.get_access_token()
    budget = CallBudget(maximum=max_calls)
    retriever = BenchmarkRetriever(
        chunks=chunks,
        embeddings=matrix,
        manifest=manifest,
        embedding_model=embedding_model,
        rerank_model=(os.getenv("APOLLO_RERANK_MODEL") or "").strip(),
        token=token,
        budget=budget,
        candidate_k=candidate_k,
        top_n=top_n,
    )
    results = {strategy: [] for strategy in STRATEGIES}
    failures = []
    for case in cases:
        for strategy in STRATEGIES:
            row, case_failures = _case_result(
                case=case,
                strategy=strategy,
                retriever=retriever,
                answer_mode=answer_mode,
            )
            results[strategy].append(row)
            failures.extend(case_failures)
            if sum(budget.counts.values()) >= max_calls:
                break
        if sum(budget.counts.values()) >= max_calls:
            break

    completed_case_ids = {
        row["case_id"]
        for rows in results.values()
        for row in rows
    }
    attempted = len(completed_case_ids)
    vector_rows = results["vector"]
    raw_only_summary = summarize_strategy(
        [
            {
                **row,
                "retrieval_metrics": row["topic_ablation_raw_only_metrics"],
                "latency_ms": row.get("latency_ms") or 0,
                "answer_evaluation": None,
                "calls": {},
            }
            for row in vector_rows
        ]
    )
    mixed_summary = summarize_strategy(vector_rows)
    payload = {
        "run_id": uuid.uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "official_live" if all(case["review_status"] == "approved" for case in cases) else "not_official",
        "official": all(case["review_status"] == "approved" for case in cases),
        "answer_mode": answer_mode,
        "case_count": attempted,
        "selected_case_count": len(cases),
        "git_commit": _git_commit(),
        "dataset_version": dataset_version(dataset_path),
        "dataset_path": str(dataset_path),
        "index_generation": directory.name,
        "manifest_generation": manifest.get("generation"),
        "index_generation_metadata_mismatch": manifest.get("generation") != directory.name,
        "index_chunk_count": len(chunks),
        "index_embedding_shape": list(matrix.shape),
        "models": {
            "answer_llm": apollo.model_name(),
            "embedding": embedding_model,
            "rerank": (os.getenv("APOLLO_RERANK_MODEL") or "").strip(),
            "temperature": 0.1,
            "max_retries": 0,
        },
        "retrieval_config": {
            "strategies": list(STRATEGIES),
            "candidate_k": candidate_k,
            "top_n": top_n,
            "rerank_min_score": float(os.getenv("RERANK_MIN_SCORE", "0.4")),
            "iterative_query_limit": 2,
            "max_model_calls": max_calls,
            "worst_case_calls_per_case": calls_per_case,
            "minimum_comparable_call_budget": required_budget,
            "same_published_index": True,
        },
        "calls_total": budget.snapshot(),
        "strategies": {
            strategy: {
                **summarize_strategy(rows),
                "cases": rows,
            }
            for strategy, rows in results.items()
        },
        "topic_ablation": {
            "description": (
                "Vector retrieval over raw+topic chunks compared with raw-only chunks, "
                "using the same query embedding and published index."
            ),
            "topic_chunk_count": sum(chunk.get("method") == "topic" for chunk in chunks),
            "raw_chunk_count": sum(chunk.get("method") != "topic" for chunk in chunks),
            "mixed_raw_and_topic": mixed_summary["retrieval"],
            "raw_only": raw_only_summary["retrieval"],
            "recall_at_5_delta_mixed_minus_raw": (
                round(
                    mixed_summary["retrieval"]["recall_at_5"]
                    - raw_only_summary["retrieval"]["recall_at_5"],
                    4,
                )
                if mixed_summary["retrieval"]["recall_at_5"] is not None
                and raw_only_summary["retrieval"]["recall_at_5"] is not None
                else None
            ),
            "cases": [
                {
                    "case_id": row["case_id"],
                    "mixed_ranked_evidence_ids": row["ranked_evidence_ids"],
                    "raw_only_ranked_evidence_ids": [
                        hit["chunk_id"] for hit in row["topic_ablation_raw_only"]
                    ],
                    "raw_only_metrics": row["topic_ablation_raw_only_metrics"],
                }
                for row in vector_rows
            ],
        },
        "failures": failures,
        "error_attribution": {
            "counts_by_type": {
                error: sum(item["error"] == error for item in failures)
                for error in sorted({item["error"] for item in failures})
            }
        },
        "human_audit": {
            "status": "not_started",
            "sampled_case_ids": [],
            "metrics": None,
        },
    }
    return write_report(payload, output_root)
