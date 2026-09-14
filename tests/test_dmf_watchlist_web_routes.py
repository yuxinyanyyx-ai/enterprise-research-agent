from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from src.dmf_history.models import Base
from src.dmf_watchlist.repository import DMFWatchlistRepository
from src.dmf_watchlist.service import DMFWatchlistService
from src.schemas.dmf import (
    DMFCollectionStatus,
    DMFHistoryResult,
    DMFQuery,
    DMFQueryResult,
    DMFRecord,
    DMFSearchResult,
    DMFSingleQuery,
)
from src.web.routes import watchlist as watchlist_routes


class EmptySearch:
    def __init__(self):
        self.call_count = 0

    def __call__(self, **kwargs):
        self.call_count += 1
        snapshot_id = f"empty-{self.call_count}"
        item = DMFQueryResult(
            success=True,
            message="ok",
            query=DMFSingleQuery(dmf_no="DMF-001"),
            collection_status=DMFCollectionStatus.SUCCESS_EMPTY,
            history=DMFHistoryResult(
                monitor_run_id=f"monitor-{self.call_count}",
                snapshot_id=snapshot_id,
                history_status="empty_observation_recorded",
            ),
        )
        return DMFSearchResult(
            success=True,
            message="ok",
            query=DMFQuery(dmf_no="DMF-001"),
            query_count=1,
            success_count=1,
            results=[item],
        ).model_dump()


class RecordSearch:
    def __init__(self, valid_date):
        self.valid_date = valid_date

    def __call__(self, **kwargs):
        record = DMFRecord(dmf_no="DMF-001", valid_date=self.valid_date)
        item = DMFQueryResult(
            success=True,
            message="ok",
            query=DMFSingleQuery(dmf_no="DMF-001"),
            collection_status=DMFCollectionStatus.SUCCESS_NONEMPTY,
            records=[record],
            history=DMFHistoryResult(
                monitor_run_id="monitor-present",
                snapshot_id="snapshot-present",
                history_status="completed",
            ),
        )
        return DMFSearchResult(
            success=True,
            message="ok",
            query=DMFQuery(dmf_no="DMF-001"),
            query_count=1,
            success_count=1,
            total_records=1,
            results=[item],
        ).model_dump()


def make_client(tmp_path, monkeypatch, search=None) -> TestClient:
    tmp_path.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'watchlist-api.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    repository = DMFWatchlistRepository(engine)
    service = DMFWatchlistService(
        repository,
        object(),
        search=search or EmptySearch(),
        now=lambda: datetime(2026, 9, 3, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(watchlist_routes, "_get_service", lambda: service)
    app = FastAPI()
    app.include_router(watchlist_routes.router)
    return TestClient(app)


def test_watchlist_crud_and_duplicate_conflict(tmp_path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)

    created = client.post("/api/watchlists", json={"dmf_no": "DMF-001"})
    duplicate = client.post("/api/watchlists", json={"dmf_no": " dmf-001 "})
    watchlist_id = created.json()["id"]
    paused = client.patch(
        f"/api/watchlists/{watchlist_id}", json={"status": "paused"}
    )
    resumed = client.patch(
        f"/api/watchlists/{watchlist_id}", json={"status": "active", "interval_hours": 48}
    )

    assert created.status_code == 201
    assert duplicate.status_code == 409
    assert paused.json()["status"] == "paused"
    assert resumed.json()["status"] == "active"
    assert resumed.json()["interval_hours"] == 48


def test_manual_runs_create_absence_event_and_acknowledge_it(tmp_path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    watchlist_id = client.post(
        "/api/watchlists", json={"dmf_no": "DMF-001"}
    ).json()["id"]

    first = client.post(
        f"/api/watchlists/{watchlist_id}/runs",
        headers={"Idempotency-Key": "manual:first"},
    )
    second = client.post(
        f"/api/watchlists/{watchlist_id}/runs",
        headers={"Idempotency-Key": "manual:second"},
    )
    events = client.get(f"/api/watchlists/{watchlist_id}/events")
    event_id = events.json()[0]["id"]
    acknowledged = client.post(
        f"/api/watchlists/{watchlist_id}/events/{event_id}/ack"
    )
    acknowledged_again = client.post(
        f"/api/watchlists/{watchlist_id}/events/{event_id}/ack"
    )

    assert first.json()["status"] == "no_change"
    assert second.json()["status"] == "changed"
    assert events.json()[0]["event_type"] == "absent_confirmed"
    assert acknowledged.json()["status"] == "acknowledged"
    assert acknowledged_again.json()["acknowledged_at"] == acknowledged.json()["acknowledged_at"]


def test_expired_event_and_invalid_date_warning_are_exposed(tmp_path, monkeypatch) -> None:
    expired_client = make_client(tmp_path, monkeypatch, RecordSearch("115/9/2"))
    watchlist_id = expired_client.post(
        "/api/watchlists", json={"dmf_no": "DMF-001"}
    ).json()["id"]

    expired_run = expired_client.post(f"/api/watchlists/{watchlist_id}/runs")
    expired_events = expired_client.get(f"/api/watchlists/{watchlist_id}/events")

    assert expired_run.json()["status"] == "changed"
    assert expired_run.json()["warnings"] == []
    assert expired_events.json()[0]["event_type"] == "valid_date_expired"
    assert "raw_payload" not in expired_events.json()[0]

    invalid_client = make_client(
        tmp_path / "invalid", monkeypatch, RecordSearch("not-a-date")
    )
    invalid_id = invalid_client.post(
        "/api/watchlists", json={"dmf_no": "DMF-001"}
    ).json()["id"]
    invalid_run = invalid_client.post(f"/api/watchlists/{invalid_id}/runs")

    assert invalid_run.json()["status"] == "no_change"
    assert "无法解析" in invalid_run.json()["warnings"][0]


def test_watchlist_notification_config_web_routes(tmp_path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)

    # 1. 创建带有格式化和规范化邮箱的关注项
    res = client.post(
        "/api/watchlists",
        json={
            "dmf_no": "DMF-001",
            "notification_enabled": True,
            "notification_emails": ["  USER1@example.com  ", "user1@example.com", "USER2@example.com"],
            "notification_mode": "weekly_digest",
        },
    )
    assert res.status_code == 201
    body = res.json()
    assert body["notification_enabled"] is True
    assert body["notification_emails"] == ["user1@example.com", "user2@example.com"]
    assert body["notification_mode"] == "weekly_digest"
    watchlist_id = body["id"]

    # 2. 带有非法邮箱返回 422
    invalid_email_res = client.post(
        "/api/watchlists",
        json={
            "dmf_no": "DMF-002",
            "notification_enabled": True,
            "notification_emails": ["not-an-email"],
        },
    )
    assert invalid_email_res.status_code == 422

    # 3. 开启通知但无邮箱返回 422
    empty_email_res = client.post(
        "/api/watchlists",
        json={
            "dmf_no": "DMF-003",
            "notification_enabled": True,
            "notification_emails": [],
        },
    )
    assert empty_email_res.status_code == 422

    # 4. PATCH 更新通知配置
    patch_disable = client.patch(
        f"/api/watchlists/{watchlist_id}",
        json={"notification_enabled": False},
    )
    assert patch_disable.status_code == 200
    assert patch_disable.json()["notification_enabled"] is False
    assert patch_disable.json()["notification_emails"] == ["user1@example.com", "user2@example.com"]

    patch_emails = client.patch(
        f"/api/watchlists/{watchlist_id}",
        json={"notification_emails": ["updated@example.com"]},
    )
    assert patch_emails.status_code == 200
    assert patch_emails.json()["notification_emails"] == ["updated@example.com"]

    # 5. 软删除与重新添加不带通知配置时的重置行为
    client.delete(f"/api/watchlists/{watchlist_id}")
    restore_res = client.post("/api/watchlists", json={"dmf_no": "DMF-001"})
    assert restore_res.status_code == 201
    restore_body = restore_res.json()
    assert restore_body["id"] == watchlist_id
    assert restore_body["notification_enabled"] is False
    assert restore_body["notification_emails"] == ["updated@example.com"]
