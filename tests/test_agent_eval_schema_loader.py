from pathlib import Path

import pytest

from tests.agent_eval.loader import CaseLoadError, load_cases


VALID_CASE = """
cases:
  - id: CASE-001
    description: Query one ingredient
    tags: [dmf]
    fixtures:
      intent:
        kind: llm
        value:
          data_source: dmf
      result:
        kind: dmf_result
        value:
          success: true
    turns:
      - user_query: Query Ibuprofen
        scripted_llm: [intent]
        dmf_result: result
        expected:
          route: dmf_query
          answer:
            contains: [Ibuprofen]
          call_deltas:
            llm: 1
            dmf: 1
"""


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.mark.agent_eval
def test_load_cases_sorts_by_file_then_case_id(tmp_path: Path) -> None:
    _write(tmp_path / "b.yaml", VALID_CASE.replace("CASE-001", "CASE-003"))
    _write(
        tmp_path / "a.yaml",
        VALID_CASE.replace("CASE-001", "CASE-002")
        + VALID_CASE.replace("cases:\n", "", 1).replace("CASE-001", "CASE-001"),
    )

    assert [case.id for case in load_cases(tmp_path)] == [
        "CASE-001",
        "CASE-002",
        "CASE-003",
    ]


@pytest.mark.agent_eval
def test_loader_rejects_unknown_fields_with_file_and_case(tmp_path: Path) -> None:
    path = tmp_path / "invalid.yaml"
    _write(path, VALID_CASE.replace("    tags:", "    surprise: true\n    tags:"))

    with pytest.raises(CaseLoadError) as error:
        load_cases(tmp_path)

    message = str(error.value)
    assert str(path) in message
    assert "CASE-001" in message
    assert "surprise" in message


@pytest.mark.agent_eval
def test_loader_rejects_unknown_fixture_reference(tmp_path: Path) -> None:
    _write(tmp_path / "invalid.yaml", VALID_CASE.replace("dmf_result: result", "dmf_result: absent"))

    with pytest.raises(CaseLoadError, match="unknown fixture reference.*absent"):
        load_cases(tmp_path)


@pytest.mark.agent_eval
def test_loader_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    _write(tmp_path / "a.yaml", VALID_CASE)
    _write(tmp_path / "b.yaml", VALID_CASE)

    with pytest.raises(CaseLoadError, match="duplicate case id 'CASE-001'"):
        load_cases(tmp_path)


@pytest.mark.agent_eval
def test_case_filter_selects_exact_ids(tmp_path: Path) -> None:
    _write(tmp_path / "a.yaml", VALID_CASE)
    _write(tmp_path / "b.yaml", VALID_CASE.replace("CASE-001", "CASE-002"))

    assert [case.id for case in load_cases(tmp_path, case_filter="CASE-002")] == [
        "CASE-002"
    ]

    with pytest.raises(CaseLoadError, match="unknown case.*CASE-404"):
        load_cases(tmp_path, case_filter="CASE-404")
