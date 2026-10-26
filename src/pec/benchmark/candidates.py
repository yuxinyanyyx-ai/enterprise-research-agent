from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from src.llm import apollo
from src.pec.benchmark.dataset import read_jsonl, validate_case, write_jsonl
from src.pec.benchmark.retrieval import CallBudget, _json_object
from src.pec.knowledge.chunk_index import _load_chunks, _load_manifest
from src.pec.knowledge.lifecycle import visible
from src.pec.knowledge.meetings import meeting_topics
from src.pec.knowledge.storage import active_dir


CANDIDATE_COUNTS = {
    "fact": 8,
    "cross_document": 6,
    "insight": 6,
    "historical_change": 4,
    "evidence_conflict": 3,
    "unanswerable": 3,
}
GENERATION_GROUPS = tuple((question_type,) for question_type in CANDIDATE_COUNTS)
TYPE_GUIDANCE = {
    "fact": "Ask one answerable factual question with a directly stated answer.",
    "cross_document": "Require synthesis of evidence from at least two distinct source files.",
    "insight": "Ask for a cautious synthesis or comparison supported by cited facts, not speculation.",
    "historical_change": "Ask how an explicitly dated fact or status changed across evidence.",
    "evidence_conflict": "Ask about two explicitly inconsistent source statements; preserve both positions.",
    "unanswerable": "Ask a plausible question for which the full index may have no supporting answer.",
}
TYPE_LABELS = {
    "fact": "事实查询",
    "cross_document": "跨文档查询",
    "insight": "Insight 综合分析",
    "historical_change": "历史变化",
    "evidence_conflict": "证据冲突",
    "unanswerable": "无答案问题",
}
EXPECTED_ANSWERABILITY = {
    "fact": "answerable",
    "cross_document": "answerable",
    "insight": "answerable",
    "historical_change": "answerable",
    "evidence_conflict": "conflict",
    "unanswerable": "unanswerable",
}


def _evidence_bundle(chunks: list[dict[str, Any]], manifest: dict[str, Any]) -> list[dict[str, str]]:
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk in chunks:
        text = str(chunk.get("text") or "").strip()
        if (
            chunk.get("method") != "topic"
            and visible(manifest, chunk.get("source_ref", ""))
            and len(text) >= 80
        ):
            by_source[chunk["source_ref"]].append(chunk)
    for rows in by_source.values():
        rows.sort(key=lambda row: (-min(len(row["text"]), 1600), row["chunk_id"]))

    selected = []
    source_names = sorted(by_source)
    counts: Counter[str] = Counter()
    while source_names and len(selected) < 36:
        keep_going = False
        for source in source_names:
            choices = by_source[source]
            next_index = counts[source]
            if next_index >= min(4, len(choices)):
                continue
            keep_going = True
            row = choices[next_index]
            counts[source] += 1
            selected.append(
                {
                    "evidence_id": row["chunk_id"],
                    "source_file": source,
                    "location": str(row.get("loc") or ""),
                    "text": row["text"][:1200],
                }
            )
            if len(selected) >= 36:
                break
        if not keep_going:
            break
    while sum(len(json.dumps(item, ensure_ascii=False)) for item in selected) > 28000:
        selected.pop()
    return selected


def _validate_candidates(
    question_type: str,
    payload: dict[str, Any],
    *,
    allowed_evidence: dict[str, dict[str, str]],
    start_number: int,
    expected_count: int | None = None,
) -> list[dict[str, Any]]:
    raw_cases = payload.get("cases")
    expected_count = expected_count or CANDIDATE_COUNTS[question_type]
    if not isinstance(raw_cases, list) or len(raw_cases) != expected_count:
        raise ValueError(
            f"{question_type}: Apollo must return exactly {expected_count} cases"
        )
    result = []
    for offset, raw in enumerate(raw_cases):
        if not isinstance(raw, dict):
            raise ValueError(f"{question_type}: each case must be an object")
        question = str(raw.get("question") or "").strip()
        reference_answer = str(raw.get("reference_answer") or "").strip()
        answerability = str(raw.get("answerability") or "").strip()
        evidence_ids = raw.get("expected_evidence_ids")
        source_files = raw.get("expected_source_files", [])
        if not question or not reference_answer:
            raise ValueError(f"{question_type}: question and reference_answer are required")
        if answerability != EXPECTED_ANSWERABILITY[question_type]:
            raise ValueError(f"{question_type}: invalid proposed answerability label")
        if not isinstance(evidence_ids, list) or not isinstance(source_files, list):
            raise ValueError(f"{question_type}: evidence IDs and optional sources must be lists")
        evidence_ids = list(dict.fromkeys(str(item) for item in evidence_ids))
        if any(not isinstance(item, str) for item in source_files):
            raise ValueError(f"{question_type}: source file values must be strings")
        if any(evidence_id not in allowed_evidence for evidence_id in evidence_ids):
            raise ValueError(f"{question_type}: cited evidence must be in the active index bundle")
        actual_sources = {
            allowed_evidence[evidence_id]["source_file"]
            for evidence_id in evidence_ids
        }
        if question_type == "unanswerable":
            if evidence_ids or source_files:
                raise ValueError("unanswerable candidates must not include expected evidence")
        else:
            if not evidence_ids:
                raise ValueError(f"{question_type}: candidate requires evidence IDs")
        if question_type == "evidence_conflict" and len(evidence_ids) < 2:
            raise ValueError("evidence_conflict candidates require at least two evidence IDs")
        if question_type == "cross_document" and len(actual_sources) < 2:
            raise ValueError("cross_document candidates must cite at least two source files")
        candidate = {
            "case_id": f"PEC-{question_type.upper()}-{start_number + offset:03d}",
            "question": question,
            "question_type": question_type,
            "question_type_label": TYPE_LABELS[question_type],
            "reference_answer": reference_answer,
            "expected_source_files": sorted(actual_sources),
            "expected_evidence_ids": evidence_ids,
            "answerability": answerability,
            "review_status": "pending_review",
            "review_note": "",
            "candidate_generation": "apollo_llm_unreviewed",
        }
        if source_files and set(source_files) != actual_sources:
            candidate["generation_warnings"] = [
                "LLM source file proposal differed from evidence IDs; expected source files were derived from the active index."
            ]
        result.append(candidate)
    return result


def _build_assignments(
    question_type: str,
    bundle: list[dict[str, str]],
    *,
    related_pairs: list[list[dict[str, str]]] | None = None,
    conflict_pairs: list[list[dict[str, str]]] | None = None,
) -> list[dict[str, Any]]:
    count = CANDIDATE_COUNTS[question_type]
    by_source: dict[str, list[dict[str, str]]] = defaultdict(list)
    for item in bundle:
        by_source[item["source_file"]].append(item)
    sources = sorted(by_source)

    def different_source_pair(index: int) -> list[dict[str, str]]:
        first_source = sources[index % len(sources)]
        second_source = sources[(index + 1) % len(sources)]
        return [
            by_source[first_source][(index // len(sources)) % len(by_source[first_source])],
            by_source[second_source][(index // len(sources)) % len(by_source[second_source])],
        ]

    def historical_pair(index: int) -> list[dict[str, str]]:
        dated = [
            item
            for item in bundle
            if re.search(r"\b(?:19|20)\d{2}\b", item["text"])
        ]
        for first_index, first in enumerate(dated):
            first_years = set(re.findall(r"\b(?:19|20)\d{2}\b", first["text"]))
            for second in dated[first_index + 1 :]:
                second_years = set(re.findall(r"\b(?:19|20)\d{2}\b", second["text"]))
                if first["source_file"] != second["source_file"] and first_years != second_years:
                    return [first, second]
        return different_source_pair(index)

    assignments = []
    for index in range(count):
        if question_type == "unanswerable":
            evidence = []
        elif question_type == "fact":
            evidence = [bundle[index % len(bundle)]]
        elif question_type == "cross_document" and related_pairs:
            evidence = related_pairs[index % len(related_pairs)]
        elif question_type == "evidence_conflict" and conflict_pairs:
            evidence = conflict_pairs[index % len(conflict_pairs)]
        elif question_type == "evidence_conflict":
            raise ValueError("published PEC index has no structured conflict evidence for candidates")
        elif question_type == "cross_document":
            evidence = different_source_pair(index)
        elif question_type == "historical_change":
            evidence = historical_pair(index)
        else:
            source = sources[index % len(sources)]
            source_rows = by_source[source]
            if len(source_rows) >= 2:
                first = source_rows[(index * 2) % len(source_rows)]
                second = source_rows[(index * 2 + 1) % len(source_rows)]
                evidence = [first, second]
            else:
                evidence = different_source_pair(index)
        assignments.append(
            {
                "evidence_slot": f"S{index + 1:03d}",
                "evidence": evidence,
            }
        )
    return assignments


def _meeting_assignment_sets(
    topics: list[dict[str, Any]],
    manifest: dict[str, Any],
    chunks: list[dict[str, Any]],
) -> tuple[list[list[dict[str, str]]], list[list[dict[str, str]]], list[str]]:
    by_id = {
        chunk["chunk_id"]: chunk
        for chunk in chunks
        if chunk.get("method") != "topic"
        and visible(manifest, chunk.get("source_ref", ""))
    }

    def evidence_rows(ids: list[str]) -> list[dict[str, str]]:
        result = []
        for evidence_id in dict.fromkeys(ids):
            chunk = by_id.get(evidence_id)
            if chunk:
                result.append(
                    {
                        "evidence_id": evidence_id,
                        "source_file": chunk["source_ref"],
                        "location": str(chunk.get("loc") or ""),
                        "text": str(chunk.get("text") or ""),
                    }
                )
        return result

    related_pairs: list[list[dict[str, str]]] = []
    conflict_pairs: list[list[dict[str, str]]] = []
    conflict_notes = []
    for meeting in meeting_topics(topics, manifest):
        source_evidence: dict[str, dict[str, str]] = {}
        for evidence_id in meeting.get("evidence_ids", []):
            rows = evidence_rows([evidence_id])
            if rows:
                source_evidence.setdefault(rows[0]["source_file"], rows[0])
        if len(source_evidence) >= 2:
            related_pairs.append(list(source_evidence.values())[:2])
        for conflict in meeting.get("conflicts", []):
            rows = evidence_rows(conflict.get("evidence_ids") or [])
            if len({row["evidence_id"] for row in rows}) >= 2:
                conflict_pairs.append(rows)
                conflict_notes.append(
                    f"{meeting.get('title', '')}: {conflict.get('reason', '')}"
                )
    return related_pairs, conflict_pairs, conflict_notes


def _prepare_type_candidates(
    question_type: str,
    raw_cases: list[Any],
    *,
    allowed_evidence: dict[str, dict[str, str]],
    assignments: list[dict[str, Any]],
    start_number: int,
    expected_count: int,
) -> list[dict[str, Any]]:
    typed_cases = [
        row
        for row in raw_cases
        if isinstance(row, dict) and row.get("question_type") == question_type
    ]
    slot_map = {item["evidence_slot"]: item["evidence"] for item in assignments}
    if len(typed_cases) != expected_count:
        raise ValueError(f"{question_type}: Apollo returned the wrong case count")
    used_slots = set()
    for row in typed_cases:
        slot = row.get("evidence_slot")
        if slot not in slot_map or slot in used_slots:
            raise ValueError(f"{question_type}: Apollo returned an unknown or duplicate evidence_slot")
        used_slots.add(slot)
        assigned_evidence = slot_map[slot]
        row["expected_evidence_ids"] = [
            item["evidence_id"] for item in assigned_evidence
        ]
        row["expected_source_files"] = sorted(
            {item["source_file"] for item in assigned_evidence}
        )
        quotes = row.get("evidence_quotes", [])
        row["evidence_quotes"] = [
            item.get("quote", "") if isinstance(item, dict) else str(item)
            for item in quotes
        ] if isinstance(quotes, list) else []
    if used_slots != set(slot_map):
        raise ValueError(f"{question_type}: Apollo did not use every assigned evidence_slot")
    generated = _validate_candidates(
        question_type,
        {"cases": typed_cases},
        allowed_evidence=allowed_evidence,
        start_number=start_number,
        expected_count=expected_count,
    )
    for case, raw_case in zip(generated, typed_cases):
        case["evidence_quotes"] = raw_case.get("evidence_quotes", [])
    return generated


def generate_candidates(
    *,
    output_path: Path,
    max_calls: int,
    force: bool = False,
    resume: bool = False,
) -> dict[str, Any]:
    if output_path.exists() and not force:
        raise FileExistsError(f"refusing to overwrite existing candidate dataset: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path = output_path.with_suffix(".partial.jsonl")
    progress_path = output_path.with_suffix(".generation-progress.json")
    directory = active_dir()
    manifest = _load_manifest(directory)
    chunks = _load_chunks(directory)
    topics_path = directory / "topics.json"
    topics = json.loads(topics_path.read_text(encoding="utf-8")) if topics_path.is_file() else []
    all_related_pairs, all_conflict_pairs, conflict_notes = _meeting_assignment_sets(
        topics, manifest, chunks
    )
    bundle = _evidence_bundle(chunks, manifest)
    if len(bundle) < 8 or len({item["source_file"] for item in bundle}) < 2:
        raise RuntimeError("active PEC index does not contain enough source evidence")
    allowed = {
        chunk["chunk_id"]: {
            "evidence_id": chunk["chunk_id"],
            "source_file": chunk["source_ref"],
            "text": chunk["text"],
        }
        for chunk in chunks
        if chunk.get("method") != "topic"
        and visible(manifest, chunk.get("source_ref", ""))
    }
    prompt_evidence_by_id = {item["evidence_id"]: item for item in bundle}
    for item in [*all_related_pairs, *all_conflict_pairs]:
        for evidence in item:
            prompt_evidence_by_id.setdefault(evidence["evidence_id"], evidence)
    prompt_evidence = list(prompt_evidence_by_id.values())
    aliases_by_id = {
        item["evidence_id"]: f"E{index:03d}"
        for index, item in enumerate(prompt_evidence, 1)
    }
    if resume:
        if not partial_path.is_file() or not progress_path.is_file():
            raise FileNotFoundError("no resumable partial candidate generation exists")
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        if progress.get("active_index_generation") != directory.name:
            raise RuntimeError("cannot resume candidates against a different published index generation")
        rows = read_jsonl(partial_path)
        for row in rows:
            validate_case(row, evidence_by_id=allowed)
            if row["review_status"] != "pending_review":
                raise ValueError("partial generated cases must remain pending_review")
        if any(
            sum(row["question_type"] == kind for row in rows) > CANDIDATE_COUNTS[kind]
            for kind in CANDIDATE_COUNTS
        ):
            raise ValueError("partial candidate counts exceed the required distribution")
    else:
        if partial_path.exists() or progress_path.exists():
            raise FileExistsError("partial candidate generation exists; use --resume or remove those named files")
        rows = []
        progress = {
            "active_index_generation": directory.name,
            "cumulative_calls": {
                "llm": 0,
                "embedding": 0,
                "rerank": 0,
                "input_tokens": 0,
                "output_tokens": 0,
            },
        }
        progress_path.write_text(
            json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        write_jsonl(partial_path, rows)

    existing_counts = Counter(row["question_type"] for row in rows)
    pending_groups = [
        tuple(
            kind
            for kind in group
            if existing_counts[kind] < CANDIDATE_COUNTS[kind]
        )
        for group in GENERATION_GROUPS
    ]
    pending_groups = [group for group in pending_groups if group]
    required_calls = len(pending_groups) + (1 if pending_groups else 0)
    if max_calls < required_calls:
        raise ValueError(f"candidate generation requires --max-calls >= {required_calls} for the remaining categories")
    token = apollo.get_access_token()
    llm = apollo.create_apollo_llm(
        token, streaming=False, timeout=120, max_retries=0
    )
    budget = CallBudget(maximum=max_calls)
    accounted = budget.snapshot()

    def record_usage() -> None:
        nonlocal accounted
        current = budget.snapshot()
        cumulative = progress["cumulative_calls"]
        for key in cumulative:
            cumulative[key] += int(current.get(key, 0) or 0) - int(accounted.get(key, 0) or 0)
        accounted = current
        progress["updated_at"] = datetime.now(timezone.utc).isoformat()
        progress_path.write_text(
            json.dumps(progress, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    next_number = len(rows) + 1
    evidence_text = json.dumps(prompt_evidence, ensure_ascii=False)
    for question_types in pending_groups:
        counts = {
            question_type: CANDIDATE_COUNTS[question_type] - existing_counts[question_type]
            for question_type in question_types
        }
        count = sum(counts.values())
        question_type = question_types[0]
        all_assignments = _build_assignments(
            question_type,
            bundle,
            related_pairs=all_related_pairs,
            conflict_pairs=all_conflict_pairs,
        )
        start_offset = existing_counts[question_type]
        assignments = all_assignments[start_offset : start_offset + count]
        assignments_for_prompt = [
            {
                "evidence_slot": assignment["evidence_slot"],
                "evidence": [
                    {**item, "evidence_id": aliases_by_id[item["evidence_id"]]}
                    for item in assignment["evidence"]
                ],
            }
            for assignment in assignments
        ]
        assignments_text = json.dumps(assignments_for_prompt, ensure_ascii=False)
        conflict_context = (
            "\nUnreviewed structured conflict cue (generation hint only; verify against assigned raw text): "
            + json.dumps(conflict_notes, ensure_ascii=False)
            if question_type == "evidence_conflict"
            else ""
        )
        budget.reserve("llm")
        prompt = (
            f"Generate exactly {count} Chinese candidate cases of question_type `{question_type}`. "
            f"{TYPE_GUIDANCE[question_type]} Use only real evidence assigned to the slots below. "
            "Use each evidence_slot exactly once and do not alter the slot or invent citations; the "
            "program attaches the published chunk IDs and source paths from the assignment. For "
            "answerable cases, the reference answer must be supported by every assigned evidence item "
            "and preserve dates, qualifications, status, and negation. Do not reconcile positions in "
            "conflict cases. For unanswerable cases, the evidence assignment is empty; propose a "
            "plausible question that is not answered by the provided visible excerpt, without making "
            "a definitive absence claim. Every case remains pending human review. Set answerability "
            f"to `{EXPECTED_ANSWERABILITY[question_type]}`. Return only JSON with one key `cases`; "
            "each case has exactly these keys: evidence_slot, question_type, question, reference_answer, "
            "answerability. Use each assigned slot exactly once.\n"
            f"Assignments: {assignments_text}{conflict_context}"
            + (
                f"\nVisible index evidence for the unanswerable proposal: {evidence_text}"
                if question_type == "unanswerable"
                else ""
            )
        )
        try:
            response = apollo.invoke_with_gateway_retries(
                llm,
                [
                    SystemMessage(
                        content="Create a draft dataset grounded exclusively in the provided evidence. Never fabricate source IDs."
                    ),
                    HumanMessage(content=prompt),
                ],
                retries=0,
            )
            budget.add_usage(response)
            record_usage()
            try:
                data = _json_object(response.content)
                raw_cases = data.get("cases")
            except ValueError:
                raw_cases = []
            if not isinstance(raw_cases, list):
                raw_cases = []
            for question_type in question_types:
                try:
                    generated = _prepare_type_candidates(
                        question_type,
                        raw_cases,
                        allowed_evidence=allowed,
                        assignments=assignments,
                        start_number=next_number,
                        expected_count=counts[question_type],
                    )
                except ValueError as validation_error:
                    if sum(budget.counts.values()) >= max_calls:
                        raise
                    budget.reserve("llm")
                    repair_prompt = (
                        f"Generate exactly {counts[question_type]} replacement cases of "
                        f"question_type {question_type}, answerability "
                        f"{EXPECTED_ANSWERABILITY[question_type]}. The prior draft failed slot "
                        "validation. Use each assigned evidence_slot exactly once, do not invent or "
                        "change slot IDs or citations, and write only JSON with one key `cases`. Each "
                        "case must have evidence_slot, question_type, question, reference_answer, and "
                        "answerability. Do not include markdown fences.\n"
                        f"Validation constraint: {str(validation_error)[:180]}\n"
                        f"Assignments: {assignments_text}{conflict_context}"
                    )
                    retry_response = apollo.invoke_with_gateway_retries(
                        llm,
                        [
                            SystemMessage(
                                content="Regenerate only valid evidence-grounded draft cases. All cases remain pending human review."
                            ),
                            HumanMessage(content=repair_prompt),
                        ],
                        retries=0,
                    )
                    budget.add_usage(retry_response)
                    record_usage()
                    retry_data = _json_object(retry_response.content)
                    retry_cases = retry_data.get("cases")
                    if not isinstance(retry_cases, list):
                        raise ValueError("Apollo repair response cases must be a list")
                    generated = _prepare_type_candidates(
                        question_type,
                        retry_cases,
                        allowed_evidence=allowed,
                        assignments=assignments,
                        start_number=next_number,
                        expected_count=counts[question_type],
                    )
                rows.extend(generated)
                next_number += len(generated)
                existing_counts[question_type] += len(generated)
                write_jsonl(partial_path, rows)
        except Exception as exc:
            record_usage()
            _write_generation_failure(
                output_path,
                directory_name=directory.name,
                question_types=list(question_types),
                model=apollo.model_name(),
                calls=progress["cumulative_calls"],
                error=_safe_error(exc),
            )
            progress["last_error"] = _safe_error(exc)
            progress_path.write_text(
                json.dumps(progress, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            raise

    expected_distribution = Counter(
        row["question_type"] for row in rows
    )
    if len(rows) != 30 or dict(expected_distribution) != CANDIDATE_COUNTS:
        raise RuntimeError("candidate generation produced an invalid dataset distribution")
    write_jsonl(output_path, rows)
    result = {
        "output": str(output_path),
        "candidate_count": len(rows),
        "question_type_counts": dict(expected_distribution),
        "review_status": "pending_review",
        "active_index_generation": directory.name,
        "manifest_generation": manifest.get("generation"),
        "index_generation_metadata_mismatch": manifest.get("generation") != directory.name,
        "git_commit": _git_commit(),
        "dataset_version": hashlib.sha256(output_path.read_bytes()).hexdigest(),
        "llm_model": apollo.model_name(),
        "embedding_model": manifest.get("embedding_model"),
        "structured_multi_source_group_count": len(all_related_pairs),
        "structured_conflict_pair_count": len(all_conflict_pairs),
        "calls": progress["cumulative_calls"],
        "last_attempt_calls": budget.snapshot(),
        "validation_retry_budget": max(0, max_calls - len(pending_groups)),
    }
    result["created_at"] = datetime.now(timezone.utc).isoformat()
    output_path.with_suffix(".generation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    prior_failure = output_path.with_suffix(".generation-error.json")
    if prior_failure.is_file():
        suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        prior_failure.replace(
            output_path.with_name(f"{output_path.stem}.generation-error-{suffix}.json")
        )
    partial_path.unlink(missing_ok=True)
    progress_path.unlink(missing_ok=True)
    return result


def _safe_error(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if response is not None:
        return f"{type(exc).__name__} (HTTP {response.status_code})"
    detail = str(exc).replace("\n", " ").strip()[:160]
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


def _write_generation_failure(
    output_path: Path,
    *,
    directory_name: str,
    question_types: list[str],
    model: str,
    calls: dict[str, Any],
    error: str,
) -> None:
    failure = {
        "status": "failed",
        "active_index_generation": directory_name,
        "question_types": question_types,
        "llm_model": model,
        "calls": calls,
        "error": error,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    output_path.with_suffix(".generation-error.json").write_text(
        json.dumps(failure, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


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
