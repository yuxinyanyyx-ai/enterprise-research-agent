"""Transactional persistence for DMF monitor runs and eligible snapshots."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
import logging
from uuid import uuid4

from sqlalchemy import Engine, create_engine, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from src.schemas.dmf import (
    DMFChangeEvent,
    DMFCollectionStatus,
    DMFHistoryResult,
    DMFQueryResult,
    DMFRecord,
)
from src.settings import load_settings

from .comparison import (
    FINGERPRINT_VERSION,
    compare_records,
    normalize_text,
    payload_hash,
    query_fingerprint,
    record_business_key,
)
from .models import (
    Base,
    DMFBaseline,
    DMFChangeEventModel,
    DMFMonitorRun,
    DMFSnapshot,
    DMFSnapshotRecord,
)

DEFAULT_SOURCE = {
    "provider": "taiwan_fda_dmf",
    "endpoint": "/api/public/dr/piq/7000/search",
    "parser_version": 1,
    "collector_version": 1,
}
PUBLIC_HISTORY_ERROR_CODE = "HISTORY_STORAGE_ERROR"
PUBLIC_HISTORY_ERROR_MESSAGE = "历史记录暂时不可用，DMF 查询结果不受影响。"
logger = logging.getLogger(__name__)
SENSITIVE_RAW_KEYS = {
    "authorization",
    "cookie",
    "set-cookie",
    "verifycode",
    "captcha",
    "captchacode",
    "captcha_code",
}


class BaselineConflict(RuntimeError):
    pass


@lru_cache(maxsize=1)
def get_configured_history_repository() -> DMFHistoryRepository | None:
    settings = load_settings(require_token=False)
    if not settings.dmf_history_enabled:
        return None
    return DMFHistoryRepository(settings.database_url)


class DMFHistoryRepository:
    """Store collection facts independently from snapshot processing."""

    def __init__(self, database_url: str, *, engine: Engine | None = None) -> None:
        self.engine = engine or create_engine(database_url)
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False)

    def initialize_schema(self) -> None:
        """Create tables for local/test use; deployed databases use Alembic."""
        Base.metadata.create_all(self.engine)

    def record_query(
        self,
        result: DMFQueryResult,
        *,
        raw_pages: list[dict] | None = None,
        queried_at: datetime | None = None,
        source: dict | None = None,
    ) -> DMFHistoryResult:
        observed_at = queried_at or result.queried_at or result.ended_at or datetime.now(timezone.utc)
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        run_id = str(uuid4())
        fingerprint = query_fingerprint(result.query)

        self._commit_monitor_run(run_id, fingerprint, result, observed_at)

        if result.collection_status == DMFCollectionStatus.SUCCESS_EMPTY:
            try:
                snapshot_id = self._commit_empty_observation(
                    run_id,
                    fingerprint,
                    raw_pages or [],
                    observed_at,
                    source or DEFAULT_SOURCE,
                )
                self._set_run_history_status(run_id, "empty_observation_recorded")
                return DMFHistoryResult(
                    monitor_run_id=run_id,
                    snapshot_id=snapshot_id,
                    history_status="empty_observation_recorded",
                    comparison_status="skipped",
                    skip_reason="empty_result_not_baseline_eligible",
                    warnings=[
                        "FDA 本次返回空结果，已留存观察但未生成变化或更新基线。"
                    ],
                    baseline_snapshot_id=self._baseline_snapshot_id(fingerprint),
                )
            except Exception as exc:
                logger.exception("DMF 空观察保存失败，monitor_run_id=%s", run_id)
                self._set_run_history_status(run_id, "snapshot_failed", str(exc))
                return self._history_failure(run_id, exc)

        if result.collection_status != DMFCollectionStatus.SUCCESS_NONEMPTY:
            self._set_run_history_status(run_id, "not_eligible")
            return DMFHistoryResult(
                monitor_run_id=run_id,
                history_status="not_eligible",
                comparison_status="skipped",
                skip_reason="collection_not_complete_nonempty",
            )

        try:
            history = None
            for attempt in range(3):
                try:
                    history = self._commit_eligible_snapshot(
                        run_id,
                        fingerprint,
                        result.records,
                        raw_pages or [],
                        observed_at,
                        source or DEFAULT_SOURCE,
                    )
                    break
                except (BaselineConflict, IntegrityError):
                    if attempt == 2:
                        raise
            if history is None:
                raise BaselineConflict("baseline 更新重试未完成")
            self._set_run_history_status(run_id, "completed")
            history.monitor_run_id = run_id
            history.history_status = "completed"
            return history
        except Exception as exc:
            logger.exception("DMF snapshot 处理失败，monitor_run_id=%s", run_id)
            self._set_run_history_status(run_id, "snapshot_failed", str(exc))
            return self._history_failure(run_id, exc)

    def _commit_monitor_run(
        self,
        run_id: str,
        fingerprint: str,
        result: DMFQueryResult,
        queried_at: datetime,
    ) -> None:
        with self.session_factory.begin() as session:
            session.add(
                DMFMonitorRun(
                    id=run_id,
                    query_fingerprint=fingerprint,
                    fingerprint_version=FINGERPRINT_VERSION,
                    query_payload=result.query.model_dump(),
                    collection_status=(
                        result.collection_status.value
                        if result.collection_status
                        else DMFCollectionStatus.FAILED.value
                    ),
                    started_at=result.started_at,
                    ended_at=result.ended_at,
                    queried_at=result.queried_at,
                    upstream_total=result.total,
                    record_count=len(result.records),
                    total_pages=result.total_pages,
                    successful_pages=result.successful_pages,
                    failed_page=result.failed_page,
                    error_message=None if result.success else result.message,
                    history_status="pending",
                )
            )

    def _commit_empty_observation(
        self,
        run_id: str,
        fingerprint: str,
        raw_pages: list[dict],
        queried_at: datetime,
        source: dict,
    ) -> str:
        snapshot_id = str(uuid4())
        with self.session_factory.begin() as session:
            session.add(
                self._snapshot_model(
                    snapshot_id,
                    run_id,
                    fingerprint,
                    None,
                    [],
                    raw_pages,
                    queried_at,
                    source,
                    baseline_eligible=False,
                )
            )
        return snapshot_id

    def _commit_eligible_snapshot(
        self,
        run_id: str,
        fingerprint: str,
        records: list[DMFRecord],
        raw_pages: list[dict],
        queried_at: datetime,
        source: dict,
    ) -> DMFHistoryResult:
        with self.session_factory.begin() as session:
            baseline = session.get(DMFBaseline, fingerprint)
            previous_snapshot_id = baseline.snapshot_id if baseline else None
            previous_records = (
                self._load_snapshot_records(session, previous_snapshot_id)
                if previous_snapshot_id
                else []
            )
            snapshot_id = str(uuid4())
            session.add(
                self._snapshot_model(
                    snapshot_id,
                    run_id,
                    fingerprint,
                    previous_snapshot_id,
                    records,
                    raw_pages,
                    queried_at,
                    source,
                    baseline_eligible=True,
                )
            )
            self._add_snapshot_records(session, snapshot_id, records)

            events: list[DMFChangeEvent] = []
            unmatched_count = 0
            ambiguous: list[str] = []
            if previous_snapshot_id:
                events, unmatched_count, ambiguous = compare_records(
                    previous_records, records
                )
                self._add_change_events(
                    session,
                    snapshot_id,
                    previous_snapshot_id,
                    events,
                    previous_records,
                    records,
                )

            if baseline:
                update_result = session.execute(
                    update(DMFBaseline)
                    .where(
                        DMFBaseline.query_fingerprint == fingerprint,
                        DMFBaseline.version == baseline.version,
                    )
                    .values(
                        snapshot_id=snapshot_id,
                        version=baseline.version + 1,
                        updated_at=queried_at,
                    )
                )
                if update_result.rowcount != 1:
                    raise BaselineConflict("baseline 已被并发查询更新")
            else:
                session.add(
                    DMFBaseline(
                        query_fingerprint=fingerprint,
                        snapshot_id=snapshot_id,
                        version=1,
                        updated_at=queried_at,
                    )
                )

            warnings = []
            if unmatched_count:
                warnings.append(
                    f"有 {unmatched_count} 条记录缺少稳定且唯一的 DMF 身份，未自动比较。"
                )
            return DMFHistoryResult(
                snapshot_id=snapshot_id,
                comparison_status="baseline_created" if not previous_snapshot_id else "compared",
                baseline_snapshot_id=snapshot_id,
                baseline_created=previous_snapshot_id is None,
                added_count=sum(event.change_type == "added" for event in events),
                removed_count=sum(event.change_type == "removed" for event in events),
                changed_count=sum(
                    event.change_type == "field_changed" for event in events
                ),
                unmatched_record_count=unmatched_count,
                ambiguous_business_keys=ambiguous,
                events=events,
                warnings=warnings,
            )

    def _snapshot_model(
        self,
        snapshot_id: str,
        run_id: str,
        fingerprint: str,
        previous_baseline_id: str | None,
        records: list[DMFRecord],
        raw_pages: list[dict],
        queried_at: datetime,
        source: dict,
        *,
        baseline_eligible: bool,
    ) -> DMFSnapshot:
        normalized = [record.model_dump() for record in records]
        sanitized_raw_pages = _sanitize_raw_payload(raw_pages)
        return DMFSnapshot(
            id=snapshot_id,
            monitor_run_id=run_id,
            query_fingerprint=fingerprint,
            previous_baseline_id=previous_baseline_id,
            queried_at=queried_at,
            created_at=datetime.now(timezone.utc),
            source=source,
            raw_payload=sanitized_raw_pages,
            raw_content_hash=payload_hash(sanitized_raw_pages),
            normalized_payload_hash=payload_hash(normalized),
            baseline_eligible=baseline_eligible,
        )

    def _add_snapshot_records(
        self, session: Session, snapshot_id: str, records: list[DMFRecord]
    ) -> None:
        counts = Counter(record_business_key(record) for record in records)
        for ordinal, record in enumerate(records):
            business_key = record_business_key(record)
            eligible = business_key is not None and counts[business_key] == 1
            session.add(
                DMFSnapshotRecord(
                    id=str(uuid4()),
                    snapshot_id=snapshot_id,
                    ordinal=ordinal,
                    dmf_no=record.dmf_no,
                    applicant_name=record.applicant_name,
                    ingredient=record.ingredient,
                    valid_date=record.valid_date,
                    business_key=business_key,
                    identity_kind="dmf_no" if business_key else None,
                    match_eligible=eligible,
                    unmatched_reason=(
                        None
                        if eligible
                        else "duplicate_dmf_no"
                        if business_key
                        else "missing_dmf_no"
                    ),
                )
            )

    def _add_change_events(
        self,
        session: Session,
        snapshot_id: str,
        previous_snapshot_id: str,
        events: list[DMFChangeEvent],
        previous_records: list[DMFRecord],
        current_records: list[DMFRecord],
    ) -> None:
        previous_map = {record_business_key(record): record for record in previous_records}
        current_map = {record_business_key(record): record for record in current_records}
        for event in events:
            before = previous_map.get(event.business_key)
            after = current_map.get(event.business_key)
            event_key = payload_hash(
                {
                    "snapshot_id": snapshot_id,
                    "change_type": event.change_type,
                    "business_key": event.business_key,
                }
            )
            session.add(
                DMFChangeEventModel(
                    id=str(uuid4()),
                    event_key=event_key,
                    snapshot_id=snapshot_id,
                    previous_snapshot_id=previous_snapshot_id,
                    change_type=event.change_type,
                    business_key=event.business_key,
                    before_payload=before.model_dump() if before else None,
                    after_payload=after.model_dump() if after else None,
                )
            )

    def _load_snapshot_records(
        self, session: Session, snapshot_id: str
    ) -> list[DMFRecord]:
        rows = session.scalars(
            select(DMFSnapshotRecord)
            .where(DMFSnapshotRecord.snapshot_id == snapshot_id)
            .order_by(DMFSnapshotRecord.ordinal)
        )
        return [
            DMFRecord(
                dmf_no=row.dmf_no,
                applicant_name=row.applicant_name,
                ingredient=row.ingredient,
                valid_date=row.valid_date,
            )
            for row in rows
        ]

    def _baseline_snapshot_id(self, fingerprint: str) -> str | None:
        with self.session_factory() as session:
            baseline = session.get(DMFBaseline, fingerprint)
            return baseline.snapshot_id if baseline else None

    def _set_run_history_status(
        self, run_id: str, status: str, error: str | None = None
    ) -> None:
        with self.session_factory.begin() as session:
            run = session.get(DMFMonitorRun, run_id)
            if run:
                run.history_status = status
                run.history_error = error

    @staticmethod
    def _history_failure(run_id: str, exc: Exception) -> DMFHistoryResult:
        return DMFHistoryResult(
            monitor_run_id=run_id,
            history_status="snapshot_failed",
            comparison_status="failed",
            history_error_code=PUBLIC_HISTORY_ERROR_CODE,
            history_error=PUBLIC_HISTORY_ERROR_MESSAGE,
        )


def _sanitize_raw_payload(value):
    if isinstance(value, dict):
        return {
            key: _sanitize_raw_payload(item)
            for key, item in value.items()
            if key.casefold() not in SENSITIVE_RAW_KEYS
        }
    if isinstance(value, list):
        return [_sanitize_raw_payload(item) for item in value]
    return value