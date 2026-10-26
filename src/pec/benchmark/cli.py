from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from src.llm import apollo
from src.pec.benchmark.candidates import generate_candidates
from src.pec.benchmark.dataset import (
    QUESTION_TYPES,
    read_jsonl,
    update_review,
)
from src.pec.benchmark.reports import compare_reports, record_human_audit
from src.pec.benchmark.runner import _load_index, run_benchmark


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATASET = PROJECT_ROOT / "data" / "pec" / "benchmark" / "candidate_cases.jsonl"
DEFAULT_REPORTS = PROJECT_ROOT / "outputs" / "pec-rag-benchmark"


def _add_live_budget(parser: argparse.ArgumentParser, *, default_calls: int) -> None:
    parser.add_argument("--live", action="store_true", help="explicitly allow real Apollo calls")
    parser.add_argument(
        "--max-calls",
        type=int,
        required=True,
        help=f"hard upper limit for Apollo model requests (minimum {default_calls})",
    )


def _preflight(args: argparse.Namespace) -> int:
    if not args.live:
        raise ValueError("preflight makes real Apollo requests; pass --live explicitly")
    if args.max_calls < 3:
        raise ValueError("preflight requires --max-calls >= 3")
    directory, manifest, chunks, matrix = _load_index()
    statuses: dict[str, Any] = {
        "index": {
            "status": "ok",
            "generation": directory.name,
            "manifest_generation": manifest.get("generation"),
            "generation_metadata_mismatch": manifest.get("generation") != directory.name,
            "chunk_count": len(chunks),
            "embedding_shape": list(matrix.shape),
        },
        "oauth": {"status": "not_run"},
        "embedding": {"status": "not_run"},
        "rerank": {"status": "not_run"},
        "llm": {"status": "not_run"},
    }
    token = apollo.get_access_token()
    statuses["oauth"] = {"status": "ok"}
    failures = []
    checks = (
        (
            "embedding",
            lambda: apollo.embed_texts(
                ["PEC RAG benchmark connectivity check"], access_token=token
            ),
        ),
        (
            "rerank",
            lambda: apollo.rerank_documents(
                "benchmark connectivity",
                ["PEC evidence", "unrelated text"],
                top_n=2,
                access_token=token,
            ),
        ),
        (
            "llm",
            lambda: apollo.invoke_with_gateway_retries(
                apollo.create_apollo_llm(
                    token, max_tokens=8, timeout=30, max_retries=0
                ),
                "Reply with only OK",
                retries=0,
                operation_name="benchmark.preflight",
                component="pec.benchmark",
            ),
        ),
    )
    for name, check in checks:
        try:
            check()
            statuses[name] = {"status": "ok"}
        except Exception as exc:
            response = getattr(exc, "response", None)
            detail = f"HTTP {response.status_code}" if response is not None else type(exc).__name__
            statuses[name] = {"status": "failed", "error": detail}
            failures.append(name)
    print(json.dumps(statuses, ensure_ascii=False, indent=2))
    return 1 if failures else 0


def _generate(args: argparse.Namespace) -> int:
    if not args.live:
        raise ValueError("candidate generation requires real Apollo calls; pass --live explicitly")
    result = generate_candidates(
        output_path=args.output,
        max_calls=args.max_calls,
        force=args.force,
        resume=args.resume,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _review(args: argparse.Namespace) -> int:
    result = update_review(
        args.dataset,
        args.case_id,
        status=args.status,
        reviewer=args.reviewer,
        note=args.note,
        confirm_answerability=args.confirm_answerability,
    )
    print(
        json.dumps(
            {
                "case_id": result["case_id"],
                "review_status": result["review_status"],
                "reviewer": result["reviewer"],
            },
            ensure_ascii=False,
        )
    )
    return 0


def _selected_cases(args: argparse.Namespace) -> tuple[Path, list[dict[str, Any]]]:
    rows = read_jsonl(args.dataset)
    if args.question_type:
        selected_types = set(args.question_type)
        rows = [row for row in rows if row["question_type"] in selected_types]
    if args.retry_failures:
        previous = json.loads(args.retry_failures.read_text(encoding="utf-8"))
        failed_ids = {
            row["case_id"]
            for strategy in previous.get("strategies", {}).values()
            for row in strategy.get("cases", [])
            if row.get("status") != "ok"
        }
        rows = [row for row in rows if row["case_id"] in failed_ids]
    rows = [row for row in rows if row["review_status"] == "approved"]
    if args.max_cases <= 0:
        raise ValueError("--max-cases must be a positive integer")
    rows = rows[: args.max_cases]
    if not rows:
        raise ValueError("no approved cases match the requested filters")
    return args.dataset, rows


def _run(args: argparse.Namespace) -> int:
    if not args.live:
        raise ValueError("benchmark execution requires real Apollo calls; pass --live explicitly")
    if args.max_calls <= 0:
        raise ValueError("--max-calls must be a positive integer")
    dataset_path, cases = _selected_cases(args)
    output = run_benchmark(
        dataset_path=dataset_path,
        output_root=args.output_root,
        selected_cases=cases,
        max_calls=args.max_calls,
        answer_mode=not args.retrieval_only,
        candidate_k=args.candidate_k,
        top_n=args.top_n,
    )
    print(str(output))
    return 0


def _compare(args: argparse.Namespace) -> int:
    result = compare_reports(args.first, args.second)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _audit(args: argparse.Namespace) -> int:
    result = record_human_audit(
        args.report,
        case_id=args.case_id,
        strategy=args.strategy,
        auditor=args.auditor,
        note=args.note,
        answer_correctness=args.answer_correctness,
        faithfulness=args.faithfulness,
        citation_precision=args.citation_precision,
        correct_refusal=args.correct_refusal == "true",
        conflict_identified=args.conflict_identified == "true",
    )
    print(
        json.dumps(
            {
                "case_id": args.case_id,
                "strategy": args.strategy,
                "auditor": result["auditor"],
                "human_evaluation": result,
            },
            ensure_ascii=False,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Real Apollo PEC RAG benchmark")
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight", help="check the active index and Apollo APIs")
    _add_live_budget(preflight, default_calls=3)
    preflight.set_defaults(handler=_preflight)

    generate = subparsers.add_parser("generate", help="generate unreviewed candidate cases")
    _add_live_budget(generate, default_calls=7)
    generate.add_argument("--output", type=Path, default=DEFAULT_DATASET)
    generate.add_argument("--force", action="store_true")
    generate.add_argument("--resume", action="store_true")
    generate.set_defaults(handler=_generate)

    review = subparsers.add_parser("review", help="approve or reject a candidate case")
    review.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    review.add_argument("--case-id", required=True)
    review.add_argument("--status", choices=("approved", "rejected"), required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--note", required=True)
    review.add_argument("--confirm-answerability", default="")
    review.set_defaults(handler=_review)

    run = subparsers.add_parser("run", help="run live retrieval and optional answer evaluation")
    _add_live_budget(run, default_calls=1)
    run.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    run.add_argument("--max-cases", type=int, required=True)
    run.add_argument("--question-type", choices=QUESTION_TYPES, action="append")
    run.add_argument("--retry-failures", type=Path)
    run.add_argument("--retrieval-only", action="store_true")
    run.add_argument("--candidate-k", type=int, default=40)
    run.add_argument("--top-n", type=int, default=10)
    run.add_argument("--output-root", type=Path, default=DEFAULT_REPORTS)
    run.set_defaults(handler=_run)

    compare = subparsers.add_parser("compare", help="compare metrics from two reports")
    compare.add_argument("first", type=Path)
    compare.add_argument("second", type=Path)
    compare.add_argument("--output", type=Path)
    compare.set_defaults(handler=_compare)

    audit = subparsers.add_parser("audit", help="record a separate human answer evaluation")
    audit.add_argument("--report", type=Path, required=True)
    audit.add_argument("--case-id", required=True)
    audit.add_argument("--strategy", choices=("vector", "hybrid", "hybrid_rerank", "hybrid_iterative"), required=True)
    audit.add_argument("--auditor", required=True)
    audit.add_argument("--note", required=True)
    audit.add_argument("--answer-correctness", type=float, required=True)
    audit.add_argument("--faithfulness", type=float, required=True)
    audit.add_argument("--citation-precision", type=float, required=True)
    audit.add_argument("--correct-refusal", choices=("true", "false"), required=True)
    audit.add_argument("--conflict-identified", choices=("true", "false"), required=True)
    audit.set_defaults(handler=_audit)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except Exception as exc:
        print(f"benchmark error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
