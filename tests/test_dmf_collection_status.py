from src.dmf_query import dmf_service
from src.dmf_query.dmf_errors import DmfClientError


def raw_page(*, total: int, total_pages: int, records: list[dict]) -> dict:
    return {
        "response": {"state": {"code": 1, "msgSubject": "success"}},
        "page": {
            "totalDatas": total,
            "totalPages": total_pages,
            "currentPage": 1,
            "pageItems": len(records),
        },
        "data": records,
    }


def test_complete_empty_query_has_non_baseline_status(monkeypatch) -> None:
    monkeypatch.setattr(
        dmf_service,
        "search_dmf_page",
        lambda **kwargs: raw_page(total=0, total_pages=0, records=[]),
    )

    result = dmf_service.search_all_dmf(
        object(), captcha_code="1234", verify_code="token", ingredient="Unknown"
    )

    assert result["success"] is True
    assert result["collection_status"] == "SUCCESS_EMPTY"
    assert result["successful_pages"] == 0
    assert len(result["raw_pages"]) == 1


def test_later_page_failure_is_partial(monkeypatch) -> None:
    def fake_search_page(**kwargs):
        if kwargs["page"] == 2:
            raise DmfClientError("timeout")
        return raw_page(
            total=2,
            total_pages=2,
            records=[
                {
                    "dmfNo": "DMF-001",
                    "applicantName": "Company A",
                    "ingredientsDesc": "Ibuprofen",
                    "validDate": "2028-01-01",
                }
            ],
        )

    monkeypatch.setattr(dmf_service, "search_dmf_page", fake_search_page)

    result = dmf_service.search_all_dmf(
        object(), captcha_code="1234", verify_code="token", ingredient="Ibuprofen"
    )

    assert result["success"] is False
    assert result["collection_status"] == "PARTIAL"
    assert result["successful_pages"] == 1
    assert result["failed_page"] == 2
    assert len(result["records"]) == 1
    assert len(result["raw_pages"]) == 1