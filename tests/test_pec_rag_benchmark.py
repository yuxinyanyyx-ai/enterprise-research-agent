from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from src.pec.benchmark.candidates import (
    CANDIDATE_COUNTS,
    GENERATION_GROUPS,
    _build_assignments,
    _validate_candidates,
)
from src.pec.benchmark.dataset import approved_cases, read_jsonl, update_review, write_jsonl
from src.pec.benchmark.metrics import (
    retrieval_case_metrics,
    retrieval_evidence_metrics,
    summarize_strategy,
)
from src.pec.benchmark.reports import (
    compare_reports,
    record_human_audit,
    render_markdown,
)
from src.pec.benchmark.retrieval import (
    BenchmarkResponseError,
    BenchmarkRetriever,
    CallBudget,
)
from src.pec.benchmark.runner import _case_result, run_benchmark

pytestmark = pytest.mark.rag_benchmark


def _case(**overrides):
    return {
        "case_id": "PEC-FACT-001",
        "question": "Which project was discussed?",
        "question_type": "fact",
        "reference_answer": "Project Alpha",
        "expected_source_files": ["meeting.docx"],
        "expected_evidence_ids": ["chunk-1"],
        "answerability": "answerable",
        "review_status": "pending_review",
        **overrides,
    }


def _retriever(*, llm_content='{"queries": []}'):
    chunks = [
        {
            "chunk_id": "chunk-1",
            "source_ref": "meeting.docx",
            "loc": "Page 1",
            "text": "Project Alpha evaluation was planned for 2026.",
            "method": "text",
        },
        {
            "chunk_id": "chunk-2",
            "source_ref": "other.docx",
            "loc": "Page 2",
            "text": "Project Beta remained under review.",
            "method": "text",
        },
    ]
    matrix = np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    calls = {"embed": 0, "rerank": 0, "llm": 0}

    def embed(texts, access_token=None):
        calls["embed"] += 1
        return [[1.0, 0.0] for _ in texts]

    def rerank(query, documents, *, top_n, access_token=None):
        calls["rerank"] += 1
        return [
            {"index": index, "relevance_score": 0.9 - index * 0.1}
            for index in range(min(top_n, len(documents)))
        ]

    class Response:
        content = llm_content
        usage_metadata = {"input_tokens": 10, "output_tokens": 3}

    class LLM:
        def invoke(self, messages):
            calls["llm"] += 1
            return Response()

        def with_structured_output(self, schema, *, include_raw=False):
            llm = self

            class StructuredLLM:
                def invoke(self, messages):
                    raw = llm.invoke(messages)
                    try:
                        parsed = schema.model_validate_json(raw.content)
                    except Exception as exc:
                        return {
                            "raw": raw,
                            "parsed": None,
                            "parsing_error": exc,
                        }
                    return {"raw": raw, "parsed": parsed, "parsing_error": None}

            return StructuredLLM()

    return (
        BenchmarkRetriever(
            chunks=chunks,
            embeddings=matrix,
            manifest={"files": {}},
            embedding_model="test-embedding",
            rerank_model="test-rerank",
            token="test-token",
            budget=CallBudget(maximum=20),
            candidate_k=2,
            top_n=2,
            llm_factory=lambda *args, **kwargs: LLM(),
            embed=embed,
            rerank=rerank,
        ),
        calls,
    )


def test_only_reviewed_cases_are_eligible_and_conflict_needs_manual_confirmation(tmp_path):
    path = tmp_path / "cases.jsonl"
    write_jsonl(path, [_case()])
    assert read_jsonl(path)[0]["review_status"] == "pending_review"
    assert approved_cases(
        read_jsonl(path), evidence_by_id={"chunk-1": {"source_ref": "meeting.docx"}}
    ) == []

    update_review(
        path,
        "PEC-FACT-001",
        status="approved",
        reviewer="reviewer",
        note="Checked source and answer.",
    )
    approved = approved_cases(
        read_jsonl(path), evidence_by_id={"chunk-1": {"source_ref": "meeting.docx"}}
    )
    assert [case["case_id"] for case in approved] == ["PEC-FACT-001"]

    conflict = _case(
        case_id="PEC-CONFLICT-001",
        question_type="evidence_conflict",
        answerability="conflict",
        expected_source_files=["meeting.docx"],
        expected_evidence_ids=["chunk-1"],
    )
    write_jsonl(path, [conflict])
    with pytest.raises(ValueError, match="confirm-answerability"):
        update_review(
            path,
            "PEC-CONFLICT-001",
            status="approved",
            reviewer="reviewer",
            note="Reviewed",
        )


def test_unanswerable_case_requires_empty_expected_evidence():
    invalid = _case(
        question_type="unanswerable",
        answerability="unanswerable",
    )
    with pytest.raises(ValueError, match="cannot expect source evidence"):
        from src.pec.benchmark.dataset import validate_case

        validate_case(invalid)


def test_dataset_validation_accepts_candidate_bundle_source_file_provenance():
    from src.pec.benchmark.dataset import validate_case

    validate_case(
        _case(),
        evidence_by_id={"chunk-1": {"source_file": "meeting.docx"}},
    )


def test_candidate_validation_uses_index_provenance_and_keeps_llm_proposals_pending():
    evidence = {
        "chunk-1": {"evidence_id": "chunk-1", "source_file": "actual.docx"},
        "chunk-2": {"evidence_id": "chunk-2", "source_file": "actual.docx"},
    }
    generated = _validate_candidates(
        "fact",
        {
            "cases": [
                {
                    "question": f"What was discussed in case {index}?",
                    "reference_answer": f"Project Alpha {index}.",
                    "answerability": "answerable",
                    "expected_source_files": ["invented.docx"],
                    "expected_evidence_ids": ["chunk-1", "chunk-2"],
                }
                for index in range(8)
            ]
        },
        allowed_evidence=evidence,
        start_number=1,
    )
    assert generated[0]["expected_source_files"] == ["actual.docx"]
    assert generated[0]["review_status"] == "pending_review"
    assert generated[0]["generation_warnings"]
    assert len(GENERATION_GROUPS) == 6
    assert sum(CANDIDATE_COUNTS.values()) == 30

    with pytest.raises(ValueError, match="active index bundle"):
        _validate_candidates(
            "fact",
            {
                "cases": [
                    {
                        "question": f"Question {index}",
                        "reference_answer": f"Answer {index}",
                        "answerability": "answerable",
                        "expected_source_files": ["actual.docx"],
                        "expected_evidence_ids": ["unknown"],
                    }
                    for index in range(8)
                ]
            },
            allowed_evidence=evidence,
            start_number=1,
        )


def test_candidate_evidence_slots_are_bound_to_real_chunks_and_source_files():
    bundle = [
        {"evidence_id": f"chunk-{index}", "source_file": source, "text": f"evidence {index}"}
        for index, source in enumerate(
            ["one.docx", "one.docx", "two.docx", "two.docx", "three.docx", "three.docx"],
            1,
        )
    ]
    cross_document = _build_assignments("cross_document", bundle)
    assert len(cross_document) == CANDIDATE_COUNTS["cross_document"]
    assert all(
        len({item["source_file"] for item in assignment["evidence"]}) == 2
        for assignment in cross_document
    )
    assert all(
        all(item["evidence_id"] in {row["evidence_id"] for row in bundle} for item in assignment["evidence"])
        for assignment in cross_document
    )
    assert all(not assignment["evidence"] for assignment in _build_assignments("unanswerable", bundle))

    conflict_pair = [bundle[0], bundle[2]]
    conflicts = _build_assignments(
        "evidence_conflict", bundle, conflict_pairs=[conflict_pair]
    )
    assert len(conflicts) == 3
    assert all(
        [item["evidence_id"] for item in assignment["evidence"]]
        == [item["evidence_id"] for item in conflict_pair]
        for assignment in conflicts
    )
    with pytest.raises(ValueError, match="no structured conflict evidence"):
        _build_assignments("evidence_conflict", bundle)


def test_retrieval_metrics_measure_ranked_evidence_and_no_answer_as_not_applicable():
    scores = retrieval_case_metrics(
        ["unrelated", "chunk-2", "chunk-1"], ["chunk-1", "chunk-2"]
    )
    assert scores == {
        "recall_at_5": 1.0,
        "recall_at_10": 1.0,
        "mrr": 0.5,
        "hit_rate": 1.0,
    }
    assert retrieval_case_metrics([], [])["recall_at_5"] is None


def test_retrieval_evidence_metrics_expand_all_evidence_at_each_hit_rank():
    scores = retrieval_evidence_metrics(
        [["chunk-1", "chunk-2"], ["unrelated"]],
        ["chunk-1", "chunk-2"],
    )

    assert scores == {
        "recall_at_5": 1.0,
        "recall_at_10": 1.0,
        "mrr": 1.0,
        "hit_rate": 1.0,
    }
    assert retrieval_evidence_metrics([], [])["recall_at_5"] is None


def test_topic_retrieval_exposes_its_linked_raw_evidence():
    chunks = [
        {
            "chunk_id": "topic-1",
            "source_ref": "meeting.docx",
            "loc": "Topic 1",
            "text": "Project Alpha topic",
            "method": "topic",
            "evidence_ids": ["chunk-1", "chunk-2"],
        },
        {
            "chunk_id": "chunk-1",
            "source_ref": "meeting.docx",
            "loc": "Page 1",
            "text": "Project Alpha first evidence",
            "method": "text",
        },
        {
            "chunk_id": "chunk-2",
            "source_ref": "meeting.docx",
            "loc": "Page 2",
            "text": "Project Alpha second evidence",
            "method": "text",
        },
    ]
    retriever = BenchmarkRetriever(
        chunks=chunks,
        embeddings=np.asarray([[1.0, 0.0], [0.8, 0.2], [0.0, 1.0]], dtype=np.float32),
        manifest={"files": {}},
        embedding_model="test-embedding",
        rerank_model="test-rerank",
        token="test-token",
        budget=CallBudget(maximum=2),
        candidate_k=3,
        top_n=1,
        llm_factory=lambda *args, **kwargs: None,
        embed=lambda texts, access_token=None: [[1.0, 0.0] for _ in texts],
        rerank=lambda *args, **kwargs: pytest.fail("Vector strategy must not rerank"),
    )

    result = retriever.retrieve("Project Alpha", "vector")

    assert result["ranked_evidence_ids"] == ["topic-1"]
    assert result["ranked_evidence_groups"] == [["chunk-1", "chunk-2"]]


def test_answer_uses_structured_output_and_accounts_for_raw_usage():
    retriever, calls = _retriever(
        llm_content=json.dumps(
            {
                "answer": "Project Alpha was discussed.",
                "cited_evidence_ids": ["chunk-1"],
                "abstained": False,
                "conflict_identified": False,
            }
        )
    )
    result = retriever.answer(
        _case(),
        [
            {
                "chunk_id": "chunk-1",
                "source_ref": "meeting.docx",
                "loc": "Page 1",
                "text": "Project Alpha evaluation was planned for 2026.",
            }
        ],
    )

    assert result["answer"] == "Project Alpha was discussed."
    assert result["cited_evidence_ids"] == ["chunk-1"]
    assert retriever.budget.input_tokens == 10
    assert calls["llm"] == 1


def test_answer_parse_failure_has_a_safe_diagnostic_code():
    retriever, _ = _retriever(llm_content="not JSON")

    with pytest.raises(BenchmarkResponseError, match="structured_answer_parse_failed"):
        retriever.answer(_case(), [])


def test_case_result_preserves_retrieval_when_answer_generation_fails(monkeypatch):
    retriever, _ = _retriever()

    def fail_answer(case, hits):
        raise BenchmarkResponseError("structured_answer_parse_failed")

    monkeypatch.setattr(retriever, "answer", fail_answer)
    row, failures = _case_result(
        case=_case(),
        strategy="vector",
        retriever=retriever,
        answer_mode=True,
    )

    assert row["status"] == "error"
    assert row["retrieval_status"] == "ok"
    assert row["answer_status"] == "error"
    assert row["ranked_evidence_ids"][0] == "chunk-1"
    assert row["retrieval_metrics"]["recall_at_5"] == 1.0
    assert failures[0]["error"] == (
        "BenchmarkResponseError (structured_answer_parse_failed)"
    )


def test_strategy_summary_includes_retrieval_from_answer_failures():
    summary = summarize_strategy(
        [
            {
                "status": "ok",
                "retrieval_status": "ok",
                "latency_ms": 10,
                "retrieval_metrics": {"recall_at_5": 0.5},
                "answer_evaluation": {"answer_correctness": 1.0},
            },
            {
                "status": "error",
                "retrieval_status": "ok",
                "answer_status": "error",
                "latency_ms": 20,
                "retrieval_metrics": {"recall_at_5": 1.0},
            },
            {
                "status": "error",
                "retrieval_status": "error",
                "retrieval_metrics": {"recall_at_5": None},
            },
        ]
    )

    assert summary["retrieval"]["recall_at_5"] == 0.75
    assert summary["retrieval_metric_case_count"] == 2
    assert summary["retrieval_successful_case_count"] == 2
    assert summary["answer_evaluated_case_count"] == 1
    assert summary["successful_case_count"] == 1


def test_benchmark_rejects_mismatched_index_generation(monkeypatch, tmp_path):
    import src.pec.benchmark.runner as runner

    monkeypatch.setattr(
        runner,
        "_load_index",
        lambda: (
            tmp_path / "active-generation",
            {"generation": "manifest-generation"},
            [],
            np.empty((0, 0), dtype=np.float32),
        ),
    )

    with pytest.raises(RuntimeError, match="generations do not match"):
        run_benchmark(
            dataset_path=tmp_path / "cases.jsonl",
            output_root=tmp_path / "reports",
            selected_cases=[],
            max_calls=0,
            answer_mode=False,
        )


def test_preflight_stops_before_apollo_for_mismatched_index(
    monkeypatch, tmp_path, capsys
):
    import argparse

    import src.pec.benchmark.cli as cli

    monkeypatch.setattr(
        cli,
        "_load_index",
        lambda: (
            tmp_path / "active-generation",
            {"generation": "manifest-generation"},
            [],
            np.empty((0, 0), dtype=np.float32),
        ),
    )
    monkeypatch.setattr(
        cli.apollo,
        "get_access_token",
        lambda: pytest.fail("preflight must not call Apollo for a mismatched index"),
    )

    assert cli._preflight(argparse.Namespace(live=True, max_calls=3)) == 1
    statuses = json.loads(capsys.readouterr().out)
    assert statuses["index"]["generation_metadata_mismatch"] is True
    assert statuses["embedding"] == {
        "status": "skipped",
        "reason": "index_generation_mismatch",
    }


@pytest.mark.parametrize(
    "strategy,expected",
    [
        ("vector", {"embed": 1, "rerank": 0, "llm": 0}),
        ("hybrid", {"embed": 1, "rerank": 0, "llm": 0}),
        ("hybrid_rerank", {"embed": 1, "rerank": 1, "llm": 0}),
        ("hybrid_iterative", {"embed": 1, "rerank": 1, "llm": 1}),
    ],
)
def test_retrieval_ablation_uses_expected_call_surfaces(strategy, expected):
    retriever, calls = _retriever()
    result = retriever.retrieve("Project Alpha evaluation", strategy)
    assert result["ranked_evidence_ids"][0] == "chunk-1"
    assert calls == expected


def test_summary_and_markdown_distinguish_automatic_evaluation():
    summary = summarize_strategy(
        [
            {
                "case_id": "A",
                "status": "ok",
                "latency_ms": 12,
                "retrieval_metrics": {
                    "recall_at_5": 1.0,
                    "recall_at_10": 1.0,
                    "mrr": 1.0,
                    "hit_rate": 1.0,
                },
                "calls": {"llm": 1, "embedding": 1, "rerank": 0},
                "answerability": "unanswerable",
                "answer_evaluation": {
                    "answer_correctness": 1.0,
                    "faithfulness": 1.0,
                    "citation_precision": 1.0,
                    "correct_refusal": True,
                    "conflict_identified": False,
                },
            }
        ]
    )
    assert summary["retrieval"]["recall_at_5"] == 1.0
    assert summary["answer_evaluation_auto"]["correct_refusal_rate"] == 1.0
    assert summary["calls"]["embedding_tokens"] is None
    markdown = render_markdown(
        {
            "run_id": "run",
            "status": "official_live",
            "git_commit": "abc",
            "dataset_version": "hash",
            "index_generation": "generation",
            "case_count": 1,
            "answer_mode": True,
            "strategies": {"vector": summary},
        }
    )
    assert "automatic llm judge" in markdown.lower()
    assert "token usage and price" in markdown


def test_vector_report_compares_topic_and_raw_only_without_an_extra_embedding():
    chunks = [
        {
            "chunk_id": "topic-1",
            "source_ref": "meeting.docx",
            "loc": "Topic 1",
            "text": "Project Alpha",
            "method": "topic",
        },
        {
            "chunk_id": "chunk-1",
            "source_ref": "meeting.docx",
            "loc": "Page 1",
            "text": "Raw project evidence",
            "method": "text",
        },
    ]
    calls = []
    retriever = BenchmarkRetriever(
        chunks=chunks,
        embeddings=np.asarray([[0.0, 1.0], [1.0, 0.0]], dtype=np.float32),
        manifest={"files": {}},
        embedding_model="test-embedding",
        rerank_model="test-rerank",
        token="test-token",
        budget=CallBudget(maximum=2),
        candidate_k=2,
        top_n=1,
        llm_factory=lambda *args, **kwargs: None,
        embed=lambda texts, access_token=None: calls.append("embedding") or [[0.0, 1.0]],
        rerank=lambda *args, **kwargs: pytest.fail("Vector ablation must not rerank"),
    )

    result = retriever.retrieve("raw evidence", "vector")
    assert result["ranked_evidence_ids"] == ["topic-1"]
    assert [item["chunk_id"] for item in result["topic_ablation_raw_only"]] == ["chunk-1"]
    assert calls == ["embedding"]


def test_report_comparison_refuses_to_assume_matching_versions(tmp_path: Path):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    common = {"strategies": {"vector": {"retrieval": {"recall_at_5": 0.5}}}}
    first.write_text(json.dumps({**common, "run_id": "a", "dataset_version": "one"}))
    second.write_text(json.dumps({**common, "run_id": "b", "dataset_version": "two"}))
    result = compare_reports(first, second)
    assert result["same_dataset"] is False
    assert result["retrieval_metric_deltas"]["vector"]["recall_at_5"] == 0.0


def test_human_audit_is_saved_separately_from_auto_judge(tmp_path: Path):
    report = tmp_path / "report.json"
    payload = {
        "answer_mode": True,
        "strategies": {
            "hybrid_rerank": {
                "cases": [
                    {
                        "case_id": "PEC-FACT-001",
                        "status": "ok",
                        "answerability": "answerable",
                        "latency_ms": 10,
                        "retrieval_metrics": {
                            "recall_at_5": 1.0,
                            "recall_at_10": 1.0,
                            "mrr": 1.0,
                            "hit_rate": 1.0,
                        },
                        "calls": {},
                        "answer": {"answer": "Project Alpha"},
                        "answer_evaluation": {
                            "answer_correctness": 0.5,
                            "faithfulness": 0.5,
                            "citation_precision": 0.5,
                            "correct_refusal": False,
                            "conflict_identified": False,
                        },
                        "human_evaluation": None,
                    }
                ]
            }
        },
    }
    report.write_text(json.dumps(payload), encoding="utf-8")

    record_human_audit(
        report,
        case_id="PEC-FACT-001",
        strategy="hybrid_rerank",
        auditor="reviewer",
        note="Checked the cited source.",
        answer_correctness=1,
        faithfulness=0.9,
        citation_precision=1,
        correct_refusal=False,
        conflict_identified=False,
    )
    updated = json.loads(report.read_text(encoding="utf-8"))
    row = updated["strategies"]["hybrid_rerank"]["cases"][0]
    assert row["answer_evaluation"]["answer_correctness"] == 0.5
    assert row["human_evaluation"]["answer_correctness"] == 1
    assert updated["human_audit"]["status"] == "complete"
    assert (tmp_path / "report.md").exists()
