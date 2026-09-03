from datetime import date

from src.dmf_watchlist.state import evaluate_observation, parse_valid_date
from src.schemas.dmf import DMFCollectionStatus, DMFRecord
from src.schemas.watchlist import WatchlistEventType, WatchlistRunStatus


def observe(*, current=None, baseline=None, count=0, alerted=False, status=DMFCollectionStatus.SUCCESS_NONEMPTY, expiration_alerted_valid_date=None):
    return evaluate_observation(
        collection_status=status,
        current_record=current,
        current_identity_ambiguous=False,
        baseline_record=baseline,
        consecutive_absent_count=count,
        absence_alerted=alerted,
        observed_date=date(2026, 9, 3),
        expiration_alerted_valid_date=expiration_alerted_valid_date,
    )


def test_first_present_observation_creates_baseline_without_added_event() -> None:
    record = DMFRecord(dmf_no="DMF-001", applicant_name="Company A")

    result = observe(current=record)

    assert result.run_status == WatchlistRunStatus.NO_CHANGE
    assert result.baseline_record == record
    assert result.event_types == []


def test_present_after_initial_absence_is_added() -> None:
    record = DMFRecord(dmf_no="DMF-001", applicant_name="Company A")

    result = observe(current=record, count=1)

    assert result.event_types == [WatchlistEventType.ADDED]


def test_second_complete_absence_is_confirmed_regardless_of_trigger() -> None:
    first = observe()
    second = observe(count=first.consecutive_absent_count, alerted=first.absence_alerted)

    assert first.consecutive_absent_count == 1
    assert first.event_types == []
    assert second.consecutive_absent_count == 2
    assert second.event_types == [WatchlistEventType.ABSENT_CONFIRMED]


def test_failed_observation_does_not_change_absence_state() -> None:
    result = observe(count=1, status=DMFCollectionStatus.FAILED)

    assert result.run_status == WatchlistRunStatus.QUERY_FAILED
    assert result.consecutive_absent_count == 1
    assert result.event_types == []


def test_reappearance_and_field_change_are_both_reported() -> None:
    before = DMFRecord(dmf_no="DMF-001", applicant_name="Company A")
    after = DMFRecord(dmf_no="dmf-001", applicant_name="Company B")

    result = observe(current=after, baseline=before, count=3, alerted=True)

    assert result.consecutive_absent_count == 0
    assert result.event_types == [
        WatchlistEventType.REAPPEARED,
        WatchlistEventType.FIELD_CHANGED,
    ]


def test_first_expired_observation_alerts_without_added() -> None:
    record = DMFRecord(dmf_no="DMF-001", valid_date="2026-09-02")

    result = observe(current=record)

    assert result.event_types == [WatchlistEventType.VALID_DATE_EXPIRED]
    assert result.expiration_alerted_valid_date == "2026-09-02"


def test_expiry_date_is_valid_through_that_day_and_deduplicated() -> None:
    today = observe(current=DMFRecord(dmf_no="DMF-001", valid_date="2026-09-03"))
    repeated = observe(
        current=DMFRecord(dmf_no="DMF-001", valid_date="2026/9/2"),
        expiration_alerted_valid_date="2026-09-02",
    )

    assert today.event_types == []
    assert repeated.event_types == []


def test_gregorian_and_roc_dates_are_normalized() -> None:
    assert parse_valid_date("2026.9.2") == ("2026-09-02", None)
    assert parse_valid_date("115/9/2") == ("2026-09-02", None)
    assert parse_valid_date("民國115年9月2日") == ("2026-09-02", None)


def test_invalid_date_warns_without_clearing_expiry_cursor() -> None:
    result = observe(
        current=DMFRecord(dmf_no="DMF-001", valid_date="2026-02-30"),
        expiration_alerted_valid_date="2025-01-01",
    )

    assert result.event_types == []
    assert result.expiration_alerted_valid_date == "2025-01-01"
    assert result.warnings
