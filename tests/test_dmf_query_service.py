from pathlib import Path

from src.dmf_query import multi_query_service as service


class FakeSession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeSolver:
    def solve(self, captcha_path: Path) -> str:
        assert captcha_path == Path("captcha.jpg")
        return "1234"


def test_search_dmf_queries_rejects_empty_conditions() -> None:
    result = service.search_dmf_queries(
        dmf_no="  ",
        applicant_name="",
        ingredients=["", "  "],
    )

    assert result["success"] is False
    assert result["query_count"] == 0
    assert result["query"]["ingredients"] == []


def test_search_dmf_queries_normalizes_and_aggregates(monkeypatch) -> None:
    fake_session = FakeSession()

    monkeypatch.setattr(service.requests, "Session", lambda: fake_session)
    monkeypatch.setattr(
        service,
        "get_captcha",
        lambda session: {
            "captcha_path": Path("captcha.jpg"),
            "verify_code": "verify-token",
        },
    )
    monkeypatch.setattr(service, "MinerUCaptchaSolver", FakeSolver)

    calls: list[str] = []

    def fake_search_all_dmf(**kwargs):
        ingredient = kwargs["ingredient"]
        calls.append(ingredient)
        return {
            "success": True,
            "message": "success",
            "total": 1,
            "total_pages": 1,
            "records": [
                {
                    "dmf_no": f"DMF-{len(calls):03d}",
                    "applicant_name": "Example Pharma",
                    "ingredient": ingredient,
                    "valid_date": "2026-01-01",
                }
            ],
        }

    monkeypatch.setattr(service, "search_all_dmf", fake_search_all_dmf)

    result = service.search_dmf_queries(
        ingredients=[" Ibuprofen ", "Ibuprofen", "Metformin"],
    )

    assert calls == ["Ibuprofen", "Metformin"]
    assert result["success"] is True
    assert result["query_count"] == 2
    assert result["success_count"] == 2
    assert result["failed_count"] == 0
    assert result["total_records"] == 2
    assert result["query"]["ingredients"] == ["Ibuprofen", "Metformin"]
    assert result["results"][0]["records"][0]["ingredient"] == "Ibuprofen"
    assert fake_session.closed is True
