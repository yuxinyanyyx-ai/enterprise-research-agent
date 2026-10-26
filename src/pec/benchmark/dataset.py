from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


QUESTION_TYPES = (
    "fact",
    "cross_document",
    "insight",
    "historical_change",
    "evidence_conflict",
    "unanswerable",
)
REVIEW_STATUSES = ("pending_review", "approved", "rejected")
ANSWERABILITY = ("answerable", "conflict", "unanswerable")


def dataset_version(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_case(
    case: dict[str, Any],
    *,
    approved: bool = False,
    evidence_by_id: dict[str, dict[str, Any]] | None = None,
) -> None:
    required = (
        "case_id",
        "question",
        "question_type",
        "reference_answer",
        "expected_source_files",
        "expected_evidence_ids",
        "answerability",
        "review_status",
    )
    missing = [key for key in required if key not in case]
    if missing:
        raise ValueError(f"{case.get('case_id', '<unknown>')}: missing fields {missing}")
    if not isinstance(case["question"], str) or not case["question"].strip():
        raise ValueError(f"{case['case_id']}: question must not be empty")
    if case["question_type"] not in QUESTION_TYPES:
        raise ValueError(f"{case['case_id']}: invalid question_type")
    if case["answerability"] not in ANSWERABILITY:
        raise ValueError(f"{case['case_id']}: invalid answerability")
    if case["review_status"] not in REVIEW_STATUSES:
        raise ValueError(f"{case['case_id']}: invalid review_status")
    if not isinstance(case["expected_source_files"], list) or not isinstance(
        case["expected_evidence_ids"], list
    ):
        raise ValueError(f"{case['case_id']}: source files and evidence IDs must be lists")
    if case["answerability"] == "unanswerable" and (
        case["expected_source_files"] or case["expected_evidence_ids"]
    ):
        raise ValueError(f"{case['case_id']}: unanswerable cases cannot expect source evidence")
    if case["answerability"] != "unanswerable" and not case["expected_evidence_ids"]:
        raise ValueError(f"{case['case_id']}: answerable cases require evidence IDs")
    if case["question_type"] == "evidence_conflict" and case["answerability"] != "conflict":
        raise ValueError(f"{case['case_id']}: evidence_conflict type must be labeled conflict")
    if case["question_type"] == "unanswerable" and case["answerability"] != "unanswerable":
        raise ValueError(f"{case['case_id']}: unanswerable type has the wrong label")

    if evidence_by_id is not None:
        for evidence_id in case["expected_evidence_ids"]:
            if evidence_id not in evidence_by_id:
                raise ValueError(f"{case['case_id']}: unknown evidence ID {evidence_id}")
        actual_sources = {
            evidence_by_id[evidence_id].get(
                "source_ref", evidence_by_id[evidence_id].get("source_file", "")
            )
            for evidence_id in case["expected_evidence_ids"]
        }
        if case["answerability"] != "unanswerable" and set(
            case["expected_source_files"]
        ) != actual_sources:
            raise ValueError(f"{case['case_id']}: expected source files do not match evidence IDs")

    if approved:
        if case["review_status"] != "approved":
            raise ValueError(f"{case['case_id']}: only approved cases may be evaluated")
        for key in ("reviewer", "reviewed_at", "review_note"):
            if not str(case.get(key, "")).strip():
                raise ValueError(f"{case['case_id']}: approval requires {key}")
        if case["answerability"] in {"conflict", "unanswerable"}:
            for key in ("answerability_confirmed_by", "answerability_confirmation"):
                if not str(case.get(key, "")).strip():
                    raise ValueError(
                        f"{case['case_id']}: {case['answerability']} approval requires {key}"
                    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSONL") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{line_number}: each JSONL row must be an object")
        validate_case(row)
        rows.append(row)
    ids = [row["case_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("dataset contains duplicate case_id values")
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    path.write_text(payload, encoding="utf-8")


def update_review(
    path: Path,
    case_id: str,
    *,
    status: str,
    reviewer: str,
    note: str,
    confirm_answerability: str = "",
) -> dict[str, Any]:
    if status not in {"approved", "rejected"}:
        raise ValueError("review status must be approved or rejected")
    if not reviewer.strip() or not note.strip():
        raise ValueError("reviewer and review note are required")

    rows = read_jsonl(path)
    selected = next((row for row in rows if row["case_id"] == case_id), None)
    if selected is None:
        raise ValueError(f"case not found: {case_id}")
    selected.update(
        review_status=status,
        reviewer=reviewer.strip(),
        reviewed_at=datetime.now(timezone.utc).isoformat(),
        review_note=note.strip(),
    )
    if selected["answerability"] in {"conflict", "unanswerable"}:
        if status == "approved" and not confirm_answerability.strip():
            raise ValueError(
                "conflict and unanswerable cases require explicit --confirm-answerability"
            )
        if confirm_answerability.strip():
            selected["answerability_confirmed_by"] = reviewer.strip()
            selected["answerability_confirmation"] = confirm_answerability.strip()
    if status == "approved":
        validate_case(selected, approved=True)
    write_jsonl(path, rows)
    return selected


def approved_cases(
    rows: list[dict[str, Any]],
    *,
    evidence_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    approved = []
    for row in rows:
        if row["review_status"] != "approved":
            continue
        validate_case(row, approved=True, evidence_by_id=evidence_by_id)
        approved.append(row)
    return approved
