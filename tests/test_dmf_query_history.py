from pathlib import Path

from sqlalchemy import select

from src.dmf_history.models import DMFMonitorRun, DMFSnapshot
from src.dmf_history.repository import (
    DMFHistoryRepository,
    PUBLIC_HISTORY_ERROR_CODE,
    PUBLIC_HISTORY_ERROR_MESSAGE,
)
from src.dmf_query import multi_query_service as service


class FakeSession:
    def close(self) -> None:
        pass


class FakeSolver:
    def solve(self, captcha_path: Path) -> str:
        return "1234"


def configure_query_fakes(monkeypatch, raw_result: dict) -> None:
    monkeypatch.setattr(service.requests, "Session", FakeSession)
    monkeypatch.setattr(
        service,
        "get_captcha",
        lambda session: {
            "captcha_path": Path("captcha.jpg"),
            "verify_code": "verify-token",
        },
    )
    monkeypatch.setattr(service, "MinerUCaptchaSolver", FakeSolver)
    monkeypatch.setattr(service, "search_all_dmf", lambda **kwargs: raw_result.copy())


def test_query_result_is_persisted_and_raw_payload_is_sanitized(
    monkeypatch, tmp_path
) -> None:
    repository = DMFHistoryRepository(f"sqlite:///{tmp_path / 'history.db'}")
    repository.initialize_schema()
    configure_query_fakes(
        monkeypatch,
        {
            "success": True,
            "message": "success",
            "collection_status": "SUCCESS_NONEMPTY",
            "total": 1,
            "total_pages": 1,
            "successful_pages": 1,
            "started_at": "2026-09-03T11:59:58+00:00",
            "ended_at": "2026-09-03T12:00:00+00:00",
            "queried_at": "2026-09-03T12:00:00+00:00",
            "raw_pages": [
                {
                    "data": [{"dmfNo": "DMF-001"}],
                    "verifyCode": "must-not-be-stored",
                    "Cookie": "must-not-be-stored",
                }
            ],
            "records": [
                {
                    "dmf_no": "DMF-001",
                    "applicant_name": "Company A",
                    "ingredient": "Ibuprofen",
                    "valid_date": "2028-01-01",
                }
            ],
        },
    )

    response = service.search_dmf_queries(
        ingredients=["Ibuprofen"], history_repository=repository
    )

    history = response["results"][0]["history"]
    assert response["success"] is True
    assert history["baseline_created"] is True
    assert history["history_status"] == "completed"
    with repository.session_factory() as session:
        run = session.scalar(select(DMFMonitorRun))
        assert run.history_status == "completed"
        assert run.started_at.isoformat() == "2026-09-03T11:59:58"
        assert run.ended_at.isoformat() == "2026-09-03T12:00:00"
        snapshot = session.scalar(select(DMFSnapshot))
        assert snapshot.raw_payload == [{"data": [{"dmfNo": "DMF-001"}]}]
        assert snapshot.source["provider"] == "taiwan_fda_dmf"


def test_history_failure_does_not_change_query_success(monkeypatch) -> None:
    class FailingRepository:
        def record_query(self, *args, **kwargs):
            raise RuntimeError("database unavailable")

    configure_query_fakes(
        monkeypatch,
        {
            "success": True,
            "message": "success",
            "total": 1,
            "total_pages": 1,
            "records": [{"dmf_no": "DMF-001"}],
        },
    )

    response = service.search_dmf_queries(
        dmf_no="DMF-001", history_repository=FailingRepository()
    )

    assert response["success"] is True
    assert response["results"][0]["success"] is True
    assert response["results"][0]["history"]["history_status"] == "monitor_run_failed"
    assert response["results"][0]["history"]["history_error_code"] == PUBLIC_HISTORY_ERROR_CODE
    assert response["results"][0]["history"]["history_error"] == PUBLIC_HISTORY_ERROR_MESSAGE
    assert "database" not in response["results"][0]["history"]["history_error"]


def test_captcha_failure_records_not_executed_monitor_run(monkeypatch, tmp_path) -> None:
    repository = DMFHistoryRepository(f"sqlite:///{tmp_path / 'history.db'}")
    repository.initialize_schema()
    monkeypatch.setattr(service.requests, "Session", FakeSession)
    monkeypatch.setattr(
        service,
        "get_captcha",
        lambda session: (_ for _ in ()).throw(RuntimeError("captcha unavailable")),
    )

    response = service.search_dmf_queries(
        ingredients=["Ibuprofen", "Metformin"], history_repository=repository
    )

    assert response["success"] is False
    assert response["query_count"] == 2
    assert all(
        result["collection_status"] == "NOT_EXECUTED"
        for result in response["results"]
    )
    with repository.session_factory() as session:
        runs = list(session.scalars(select(DMFMonitorRun)))
        assert len(runs) == 2
        assert {run.collection_status for run in runs} == {"NOT_EXECUTED"}


def test_mid_batch_captcha_failure_marks_remaining_queries_not_executed(
    monkeypatch, tmp_path
) -> None:
    repository = DMFHistoryRepository(f"sqlite:///{tmp_path / 'history.db'}")
    repository.initialize_schema()
    monkeypatch.setattr(service.requests, "Session", FakeSession)
    monkeypatch.setattr(
        service,
        "get_captcha",
        lambda session: {
            "captcha_path": Path("captcha.jpg"),
            "verify_code": "verify-token",
        },
    )
    monkeypatch.setattr(service, "MinerUCaptchaSolver", FakeSolver)
    calls = []

    def fake_search_all_dmf(**kwargs):
        ingredient = kwargs["ingredient"]
        calls.append(ingredient)
        if ingredient == "Metformin":
            return {
                "success": False,
                "message": "验证码错误",
                "collection_status": "FAILED",
                "records": [],
            }
        return {
            "success": True,
            "message": "success",
            "collection_status": "SUCCESS_NONEMPTY",
            "total": 1,
            "records": [{"dmf_no": "DMF-001", "ingredient": ingredient}],
        }

    monkeypatch.setattr(service, "search_all_dmf", fake_search_all_dmf)

    response = service.search_dmf_queries(
        ingredients=["Ibuprofen", "Metformin", "Aspirin"],
        history_repository=repository,
    )

    assert calls == ["Ibuprofen", "Metformin"]
    assert response["query_count"] == 3
    assert response["success_count"] == 1
    assert response["failed_count"] == 1
    assert response["not_executed_count"] == 1
    assert response["results"][2]["collection_status"] == "NOT_EXECUTED"
    with repository.session_factory() as session:
        runs = list(session.scalars(select(DMFMonitorRun)))
        assert len(runs) == 3
        skipped = next(run for run in runs if run.collection_status == "NOT_EXECUTED")
        assert skipped.started_at is None
        assert skipped.ended_at is None