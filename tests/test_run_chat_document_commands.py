from run_chat import _document_confirmation_response, _file_command_path


def test_file_command_accepts_quoted_path_with_spaces() -> None:
    path = _file_command_path('/file "C:\\DMF Files\\sample.pdf"')

    assert str(path) == "C:\\DMF Files\\sample.pdf"


def test_file_command_requires_path() -> None:
    try:
        _file_command_path("/file")
    except ValueError as exc:
        assert "用法" in str(exc)
    else:
        raise AssertionError("missing path should fail")


def test_document_confirmation_can_edit_extracted_query() -> None:
    answers = iter(["e", "", "Example Pharma", "Ibuprofen, Naproxen"])
    result = _document_confirmation_response(
        {
            "file_name": "sample.pdf",
            "query": {
                "dmf_no": "123",
                "applicant_name": "",
                "ingredients": ["Ibuprofen"],
            },
        },
        lambda prompt: next(answers),
    )

    assert result == {
        "action": "edit",
        "query": {
            "dmf_no": "123",
            "applicant_name": "Example Pharma",
            "ingredients": ["Ibuprofen", "Naproxen"],
        },
    }


def test_document_confirmation_can_reject() -> None:
    result = _document_confirmation_response(
        {"query": {"ingredients": ["Ibuprofen"]}},
        lambda prompt: "r",
    )

    assert result == {"action": "reject"}