from src.schemas.dmf import DMFRecord, DMFSearchResult
from src.schemas.supplier import SupplierInfo


def test_dmf_record_accepts_normalized_record() -> None:
    record = DMFRecord(
        dmf_no="DMF-001",
        applicant_name="Example Pharma",
        ingredient="Ibuprofen",
        valid_date="2026-01-01",
    )
    assert record.ingredient == "Ibuprofen"


def test_dmf_search_result_has_safe_list_defaults() -> None:
    result = DMFSearchResult(
        success=False,
        message="no query",
        query={"dmf_no": "", "applicant_name": "", "ingredients": []},
    )
    assert result.results == []
    assert result.total_records == 0


def test_supplier_info_has_independent_list_defaults() -> None:
    first = SupplierInfo(company_name="A")
    second = SupplierInfo(company_name="B")
    first.certificates.append("GMP")
    assert second.certificates == []
