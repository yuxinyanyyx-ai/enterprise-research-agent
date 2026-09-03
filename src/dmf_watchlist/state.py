"""Pure state transitions for one watched DMF observation."""

from __future__ import annotations

from datetime import date
import re
import unicodedata

from src.dmf_history.comparison import compare_records
from src.schemas.dmf import DMFCollectionStatus, DMFRecord
from src.schemas.watchlist import (
    WatchlistEventType,
    WatchlistObservation,
    WatchlistRunStatus,
)


def evaluate_observation(
    *,
    collection_status: DMFCollectionStatus,
    current_record: DMFRecord | None,
    current_identity_ambiguous: bool,
    baseline_record: DMFRecord | None,
    consecutive_absent_count: int,
    absence_alerted: bool,
    observed_date: date,
    expiration_alerted_valid_date: str | None = None,
) -> WatchlistObservation:
    """Apply one query observation without depending on its trigger source."""

    if collection_status not in {
        DMFCollectionStatus.SUCCESS_NONEMPTY,
        DMFCollectionStatus.SUCCESS_EMPTY,
    }:
        return WatchlistObservation(
            run_status=WatchlistRunStatus.QUERY_FAILED,
            consecutive_absent_count=consecutive_absent_count,
            absence_alerted=absence_alerted,
            baseline_record=baseline_record,
            expiration_alerted_valid_date=expiration_alerted_valid_date,
        )

    if current_identity_ambiguous:
        return WatchlistObservation(
            run_status=WatchlistRunStatus.IDENTITY_AMBIGUOUS,
            consecutive_absent_count=consecutive_absent_count,
            absence_alerted=absence_alerted,
            baseline_record=baseline_record,
            expiration_alerted_valid_date=expiration_alerted_valid_date,
        )

    if current_record is None:
        absent_count = consecutive_absent_count + 1
        newly_confirmed = absent_count >= 2 and not absence_alerted
        return WatchlistObservation(
            run_status=(
                WatchlistRunStatus.CHANGED
                if newly_confirmed
                else WatchlistRunStatus.NO_CHANGE
            ),
            consecutive_absent_count=absent_count,
            absence_alerted=absence_alerted or newly_confirmed,
            baseline_record=baseline_record,
            expiration_alerted_valid_date=expiration_alerted_valid_date,
            event_types=(
                [WatchlistEventType.ABSENT_CONFIRMED] if newly_confirmed else []
            ),
        )

    events: list[WatchlistEventType] = []
    if absence_alerted:
        events.append(WatchlistEventType.REAPPEARED)
    elif baseline_record is None and consecutive_absent_count > 0:
        events.append(WatchlistEventType.ADDED)
    if baseline_record is not None:
        changes, _, ambiguous = compare_records([baseline_record], [current_record])
        if not ambiguous and any(event.change_type == "field_changed" for event in changes):
            events.append(WatchlistEventType.FIELD_CHANGED)

    normalized_valid_date, warning = parse_valid_date(current_record.valid_date)
    warnings = [warning] if warning else []
    next_expiration_alert = expiration_alerted_valid_date
    if normalized_valid_date is not None:
        valid_date = date.fromisoformat(normalized_valid_date)
        if valid_date < observed_date:
            if normalized_valid_date != expiration_alerted_valid_date:
                events.append(WatchlistEventType.VALID_DATE_EXPIRED)
            next_expiration_alert = normalized_valid_date
        else:
            next_expiration_alert = None

    return WatchlistObservation(
        run_status=(WatchlistRunStatus.CHANGED if events else WatchlistRunStatus.NO_CHANGE),
        consecutive_absent_count=0,
        absence_alerted=False,
        baseline_record=current_record,
        expiration_alerted_valid_date=next_expiration_alert,
        normalized_valid_date=normalized_valid_date,
        event_types=events,
        warnings=warnings,
    )


def parse_valid_date(value: str | None) -> tuple[str | None, str | None]:
    """Normalize supported Gregorian and ROC dates to ISO without guessing."""

    normalized = unicodedata.normalize("NFKC", value or "").strip()
    if not normalized or normalized.casefold() in {"-", "none", "null", "無", "未提供"}:
        return None, "FDA 有效日期为空，无法判断是否已过期。"

    chinese_match = re.fullmatch(
        r"(?:民國)?\s*(\d{2,4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?",
        normalized,
    )
    separated_match = re.fullmatch(
        r"(\d{2,4})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})",
        normalized,
    )
    match = chinese_match or separated_match
    if match is None:
        return None, f"FDA 有效日期格式无法解析：{normalized}"

    year, month, day = (int(part) for part in match.groups())
    if year < 1000:
        year += 1911
    try:
        return date(year, month, day).isoformat(), None
    except ValueError:
        return None, f"FDA 有效日期不是有效日历日期：{normalized}"
