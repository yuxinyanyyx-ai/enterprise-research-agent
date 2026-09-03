from datetime import datetime, timezone

from sqlalchemy import func, select

from src.dmf_history.comparison import compare_records, query_fingerprint
from src.dmf_history.models import (
    DMFBaseline,
    DMFChangeEventModel,
    DMFMonitorRun,
    DMFSnapshot,
    DMFSnapshotRecord,
)
from src.dmf_history.repository import (
    DMFHistoryRepository,
    PUBLIC_HISTORY_ERROR_CODE,
    PUBLIC_HISTORY_ERROR_MESSAGE,
)
from src.schemas.dmf import (
    DMFCollectionStatus,
    DMFQueryResult,
    DMFRecord,
    DMFSingleQuery,
)


def make_result(
    records: list[DMFRecord],
    status: DMFCollectionStatus = DMFCollectionStatus.SUCCESS_NONEMPTY,
) -> DMFQueryResult:
    return DMFQueryResult(
        success=True,
        message="success",
        query=DMFSingleQuery(ingredient="Ibuprofen"),
        collection_status=status,
        total=len(records),
        total_pages=1,
        successful_pages=1,
        records=records,
    )


def test_empty_observation_does_not_advance_baseline(tmp_path) -> None:
    repository = DMFHistoryRepository(f"sqlite:///{tmp_path / 'history.db'}")
    repository.initialize_schema()
    queried_at = datetime(2026, 9, 3, tzinfo=timezone.utc)
    initial = make_result(
        [DMFRecord(dmf_no="DMF-001", applicant_name="Company A")]
    )

    initial_history = repository.record_query(initial, queried_at=queried_at)
    empty_history = repository.record_query(
        make_result([], DMFCollectionStatus.SUCCESS_EMPTY), queried_at=queried_at
    )

    assert initial_history.baseline_created is True
    assert initial_history.snapshot_id == initial_history.baseline_snapshot_id
    assert empty_history.comparison_status == "skipped"
    assert empty_history.skip_reason == "empty_result_not_baseline_eligible"
    assert empty_history.snapshot_id is not None
    assert empty_history.snapshot_id != initial_history.snapshot_id
    assert empty_history.baseline_snapshot_id == initial_history.baseline_snapshot_id
    with repository.session_factory() as session:
        fingerprint = query_fingerprint(initial.query)
        baseline = session.get(DMFBaseline, fingerprint)
        assert baseline is not None
        assert baseline.snapshot_id == initial_history.baseline_snapshot_id
        assert session.scalar(select(func.count()).select_from(DMFMonitorRun)) == 2
        assert session.scalar(select(func.count()).select_from(DMFSnapshot)) == 2


def test_stable_identity_detects_field_change() -> None:
    events, unmatched_count, ambiguous = compare_records(
        [DMFRecord(dmf_no="DMF-001", applicant_name="Company A")],
        [DMFRecord(dmf_no=" dmf-001 ", applicant_name="Company B")],
    )

    assert unmatched_count == 0
    assert ambiguous == []
    assert len(events) == 1
    assert events[0].change_type == "field_changed"
    assert events[0].changes[0].field == "applicant_name"


def test_missing_and_duplicate_identities_are_not_matched() -> None:
    events, unmatched_count, ambiguous = compare_records(
        [
            DMFRecord(dmf_no=None, applicant_name="No ID"),
            DMFRecord(dmf_no="DMF-002", applicant_name="A"),
            DMFRecord(dmf_no="DMF-002", applicant_name="B"),
        ],
        [
            DMFRecord(dmf_no=None, applicant_name="Changed"),
            DMFRecord(dmf_no="DMF-002", applicant_name="C"),
        ],
    )

    assert events == []
    assert unmatched_count == 5
    assert ambiguous == ["dmf-002"]


def test_snapshot_failure_keeps_monitor_run(monkeypatch, tmp_path) -> None:
    repository = DMFHistoryRepository(f"sqlite:///{tmp_path / 'history.db'}")
    repository.initialize_schema()
    result = make_result([DMFRecord(dmf_no="DMF-001")])

    def fail_snapshot(*args, **kwargs):
        raise RuntimeError("snapshot write failed")

    monkeypatch.setattr(repository, "_commit_eligible_snapshot", fail_snapshot)

    history = repository.record_query(result)

    assert history.history_status == "snapshot_failed"
    assert history.history_error_code == PUBLIC_HISTORY_ERROR_CODE
    assert history.history_error == PUBLIC_HISTORY_ERROR_MESSAGE
    with repository.session_factory() as session:
        run = session.scalar(select(DMFMonitorRun))
        assert run is not None
        assert run.history_status == "snapshot_failed"
        assert session.scalar(select(func.count()).select_from(DMFSnapshot)) == 0


def test_consecutive_snapshots_persist_added_removed_and_changed(tmp_path) -> None:
    repository = DMFHistoryRepository(f"sqlite:///{tmp_path / 'history.db'}")
    repository.initialize_schema()
    repository.record_query(
        make_result(
            [
                DMFRecord(dmf_no="DMF-001", applicant_name="Company A"),
                DMFRecord(dmf_no="DMF-002", applicant_name="Company B"),
            ]
        )
    )

    history = repository.record_query(
        make_result(
            [
                DMFRecord(dmf_no="DMF-001", applicant_name="Company A2"),
                DMFRecord(dmf_no="DMF-003", applicant_name="Company C"),
            ]
        )
    )

    assert history.added_count == 1
    assert history.removed_count == 1
    assert history.changed_count == 1
    assert {event.change_type for event in history.events} == {
        "added",
        "removed",
        "field_changed",
    }
    with repository.session_factory() as session:
        stored_types = set(session.scalars(select(DMFChangeEventModel.change_type)))
        assert stored_types == {"added", "removed", "field_changed"}


def test_mid_transaction_failure_rolls_back_snapshot_records_events_and_baseline(
    monkeypatch, tmp_path
) -> None:
    repository = DMFHistoryRepository(f"sqlite:///{tmp_path / 'history.db'}")
    repository.initialize_schema()
    initial = make_result([DMFRecord(dmf_no="DMF-001", applicant_name="Company A")])
    initial_history = repository.record_query(initial)
    fingerprint = query_fingerprint(initial.query)

    def fail_after_flush(session, *args, **kwargs):
        session.flush()
        raise RuntimeError("failure after snapshot and records flush")

    monkeypatch.setattr(repository, "_add_change_events", fail_after_flush)
    changed = make_result(
        [
            DMFRecord(dmf_no="DMF-001", applicant_name="Company B"),
            DMFRecord(dmf_no="DMF-002", applicant_name="Company C"),
        ]
    )

    history = repository.record_query(changed)

    assert history.history_status == "snapshot_failed"
    with repository.session_factory() as session:
        baseline = session.get(DMFBaseline, fingerprint)
        assert baseline.snapshot_id == initial_history.baseline_snapshot_id
        assert baseline.version == 1
        assert session.scalar(select(func.count()).select_from(DMFMonitorRun)) == 2
        assert session.scalar(select(func.count()).select_from(DMFSnapshot)) == 1
        assert session.scalar(select(func.count()).select_from(DMFSnapshotRecord)) == 1
        assert session.scalar(select(func.count()).select_from(DMFChangeEventModel)) == 0
        failed_run = session.scalar(
            select(DMFMonitorRun).where(DMFMonitorRun.history_status == "snapshot_failed")
        )
        assert failed_run is not None
        assert "failure after snapshot" in failed_run.history_error