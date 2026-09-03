"""Stable query partitioning and conservative DMF record comparison."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from typing import Iterable

from src.schemas.dmf import DMFChangeEvent, DMFFieldChange, DMFRecord, DMFSingleQuery

FINGERPRINT_VERSION = 1
COMPARABLE_FIELDS = ("applicant_name", "ingredient", "valid_date")


def normalize_text(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").strip()
    return re.sub(r"\s+", " ", normalized).casefold()


def query_fingerprint(query: DMFSingleQuery) -> str:
    payload = {
        "version": FINGERPRINT_VERSION,
        "dmf_no": normalize_text(query.dmf_no),
        "applicant_name": normalize_text(query.applicant_name),
        "ingredient": normalize_text(query.ingredient),
    }
    return _hash_json(payload)


def payload_hash(payload: object) -> str:
    return _hash_json(payload)


def record_business_key(record: DMFRecord) -> str | None:
    dmf_no = normalize_text(record.dmf_no)
    return dmf_no or None


def compare_records(
    previous: Iterable[DMFRecord], current: Iterable[DMFRecord]
) -> tuple[list[DMFChangeEvent], int, list[str]]:
    previous_records = list(previous)
    current_records = list(current)
    previous_counts = Counter(record_business_key(record) for record in previous_records)
    current_counts = Counter(record_business_key(record) for record in current_records)
    ambiguous = sorted(
        key
        for key in set(previous_counts) | set(current_counts)
        if key is not None and (previous_counts[key] > 1 or current_counts[key] > 1)
    )
    ambiguous_set = set(ambiguous)

    previous_map = _eligible_map(previous_records, previous_counts, ambiguous_set)
    current_map = _eligible_map(current_records, current_counts, ambiguous_set)
    events: list[DMFChangeEvent] = []

    for business_key in sorted(current_map.keys() - previous_map.keys()):
        events.append(DMFChangeEvent(change_type="added", business_key=business_key))
    for business_key in sorted(previous_map.keys() - current_map.keys()):
        events.append(DMFChangeEvent(change_type="removed", business_key=business_key))
    for business_key in sorted(previous_map.keys() & current_map.keys()):
        before = previous_map[business_key]
        after = current_map[business_key]
        changes = [
            DMFFieldChange(
                field=field,
                before=getattr(before, field),
                after=getattr(after, field),
            )
            for field in COMPARABLE_FIELDS
            if normalize_text(getattr(before, field)) != normalize_text(getattr(after, field))
        ]
        if changes:
            events.append(
                DMFChangeEvent(
                    change_type="field_changed",
                    business_key=business_key,
                    changes=changes,
                )
            )

    unmatched_count = sum(
        1
        for record in previous_records + current_records
        if record_business_key(record) is None
        or record_business_key(record) in ambiguous_set
    )
    return events, unmatched_count, ambiguous


def _eligible_map(
    records: list[DMFRecord], counts: Counter[str | None], ambiguous: set[str]
) -> dict[str, DMFRecord]:
    return {
        key: record
        for record in records
        if (key := record_business_key(record)) is not None
        and counts[key] == 1
        and key not in ambiguous
    }


def _hash_json(payload: object) -> str:
    serialized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()