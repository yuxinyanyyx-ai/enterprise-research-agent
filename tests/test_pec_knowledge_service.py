import json

import pytest
from types import SimpleNamespace

from src.pec import knowledge_service


@pytest.mark.parametrize(
    "method,expected_type,expected_extraction",
    [
        ("text", "source_text", "native"),
        ("table", "source_table", "native"),
        ("vision_page", "source_visual", "vision"),
    ],
)
def test_chunk_evidence_preserves_extraction_provenance(
    method, expected_type, expected_extraction,
) -> None:
    evidence = knowledge_service._chunk_evidence(
        SimpleNamespace(
            chunk_id="chunk-1",
            method=method,
            render_mode="powerpoint" if method == "vision_page" else "",
            vision_model="vision-model" if method == "vision_page" else "",
            vision_prompt_version="2" if method == "vision_page" else "",
            source_digest="digest",
            source_ref="PEC1/meeting.pptx",
            loc="Slide 12",
            text="content",
            embed_score=0.8,
            rerank_score=0.9,
        )
    )

    assert evidence["evidence_type"] == expected_type
    assert evidence["extraction_method"] == expected_extraction
    assert evidence["chunk_id"] == "chunk-1"
    if method == "vision_page":
        assert evidence["render_mode"] == "powerpoint"
        assert evidence["vision_model"] == "vision-model"


@pytest.mark.parametrize(
    "chunk_error,topic_error,success,partial",
    [
        (None, None, True, False),
        ("chunk down", None, True, True),
        (None, "topics down", True, True),
        ("chunk down", "topics down", False, False),
    ],
    ids=["both", "topics-only", "chunks-only", "failed"],
)
def test_search_service_reports_backend_availability(
    monkeypatch, chunk_error, topic_error, success, partial,
) -> None:
    def chunks(*args, **kwargs):
        if chunk_error:
            raise RuntimeError(chunk_error)
        return [object()]

    def topics(query):
        return {
            "hits": [] if topic_error else [{"topic_id": 1}],
            **({"error": topic_error} if topic_error else {}),
        }

    monkeypatch.setattr(knowledge_service, "search_chunks", chunks)
    monkeypatch.setattr(knowledge_service, "search_topics", topics)
    result = knowledge_service.search_pec_knowledge_service("decision")
    assert result["success"] is success
    assert result.get("partial", False) is partial
    assert "context" not in result
    assert "# 回答要求" not in result["context_summary"]


def test_search_service_rejects_empty_query() -> None:
    result = knowledge_service.search_pec_knowledge_service("  ")
    assert result["success"] is False
    assert "不能为空" in result["message"]


def test_search_service_separates_summary_and_bounded_evidence(monkeypatch) -> None:
    hits = [
        SimpleNamespace(
            chunk_id=f"chunk-{index}", method="text", render_mode="",
            vision_model="", vision_prompt_version="", source_digest="digest",
            source_ref="meeting.pptx", loc=f"Slide {index}",
            text="important fact " * 100, embed_score=0.8, rerank_score=0.9,
        )
        for index in range(10)
    ]
    monkeypatch.setattr(knowledge_service, "search_chunks", lambda *args, **kwargs: hits)
    monkeypatch.setattr(
        knowledge_service,
        "search_topics",
        lambda query: {"hits": [], "error": None},
    )

    result = knowledge_service.search_pec_knowledge_service("decision")

    assert "context" not in result
    assert result["context_summary"]
    assert result["evidence_count"] == 10
    assert result["returned_evidence_count"] == 10
    assert result["truncated_evidence_count"] == 0
    assert all(item["text"] == hit.text for item, hit in zip(result["evidence"], hits))
    assert all(not item["truncated"] for item in result["evidence"])
    assert 5000 < len(json.dumps(result, ensure_ascii=False, default=str).encode("utf-8")) <= 20000


def _search_result(monkeypatch, texts, **hit_fields):
    hits = [SimpleNamespace(
        chunk_id=f"chunk-{index}", text=text,
        **{"source_ref": "meeting.pptx", "loc": f"Slide {index}", **hit_fields},
    ) for index, text in enumerate(texts)]
    monkeypatch.setattr(knowledge_service, "search_chunks", lambda *args, **kwargs: hits)
    monkeypatch.setattr(knowledge_service, "search_topics", lambda query: {"hits": []})
    return knowledge_service.search_pec_knowledge_service("private-query" * 10000)


def _byte_size(result):
    return len(json.dumps(result, ensure_ascii=False, default=str).encode("utf-8"))


@pytest.mark.parametrize("unit", ["a", "中文", "\U0001f600", '"\\\n'], ids=["ascii", "chinese", "emoji", "escapes"])
def test_oversize_evidence_uses_serialized_utf8_budget(monkeypatch, unit):
    text = unit * 25000
    result = _search_result(monkeypatch, [text])

    assert 19980 <= _byte_size(result) <= 20000
    assert "context" not in result and "query" not in result
    assert len(result["context_summary"].encode("utf-8")) <= 300
    evidence = result["evidence"][0]
    assert text.startswith(evidence["text"])
    assert evidence["truncated"] is True
    assert evidence["complete"] is False
    assert evidence["source_file"] == "meeting.pptx"
    assert result["evidence_count"] == result["returned_evidence_count"] == 1
    assert result["truncated_evidence_count"] == 0
    next_character = text[len(evidence["text"])]
    evidence["text"] += next_character
    assert _byte_size(result) > 20000


def test_exact_20kb_boundary_preserves_complete_text(monkeypatch):
    overhead = _byte_size(_search_result(monkeypatch, [""]))
    text = "x" * (20000 - overhead)
    exact = _search_result(monkeypatch, [text])
    assert _byte_size(exact) == 20000
    assert exact["evidence"][0]["text"] == text
    assert exact["evidence"][0]["complete"] is True
    over = _search_result(monkeypatch, [text + "x"])
    assert _byte_size(over) <= 20000
    assert over["evidence"][0]["truncated"] is True


def test_whole_evidence_selected_without_changing_order_or_sources(monkeypatch):
    texts = ["first " * 1200, "too large " * 3000, "last " * 500]
    result = _search_result(monkeypatch, texts)
    assert [item["text"] for item in result["evidence"]] == [texts[0], texts[2]]
    assert [item["location"] for item in result["evidence"]] == ["Slide 0", "Slide 2"]
    assert all(item["complete"] for item in result["evidence"])
    assert result["returned_evidence_count"] == 2
    assert result["truncated_evidence_count"] == 1
    assert _byte_size(result) <= 20000


def test_oversize_source_is_omitted_not_rewritten(monkeypatch):
    result = _search_result(monkeypatch, ["fact"], source_ref="long-source" * 10000)
    assert result["evidence"] == []
    assert result["evidence_count"] == result["truncated_evidence_count"] == 1
    assert result["returned_evidence_count"] == 0
    assert "未展示" in result["context_summary"]
    assert _byte_size(result) <= 20000


@pytest.mark.parametrize("mode", ["empty", "chunks", "topics", "both", "unexpected", "blank"])
def test_all_service_paths_are_bounded_without_backend_details(monkeypatch, mode):
    private = "private-path-and-error" * 10000

    def chunks(*args, **kwargs):
        if mode in {"chunks", "both"}:
            raise RuntimeError(private)
        return []

    def topics(query):
        return {"hits": [], "error": private if mode in {"topics", "both"} else None}

    def broken_evidence(*args):
        raise RuntimeError(private)

    monkeypatch.setattr(knowledge_service, "search_chunks", chunks)
    monkeypatch.setattr(knowledge_service, "search_topics", topics)
    if mode == "unexpected":
        monkeypatch.setattr(knowledge_service, "_build_evidence", broken_evidence)
    result = knowledge_service.search_pec_knowledge_service(" " if mode == "blank" else private)
    assert _byte_size(result) <= 20000
    assert "private-path" not in json.dumps(result)
    assert "context" not in result and "query" not in result
    assert result["success"] is (mode not in {"both", "unexpected", "blank"})


def test_inline_fields_preserve_evidence_provenance_without_large_metadata(monkeypatch):
    result = _search_result(monkeypatch, ["fact"], method="vision_page", vision_model="large" * 10000)
    evidence = result["evidence"][0]
    assert evidence["extraction_method"] == "vision"
    assert evidence["evidence_type"] == "source_visual"
    assert evidence["chunk_id"] == "chunk-0"
    assert "vision_model" not in evidence and "rerank_score" not in evidence
    assert _byte_size(result) <= 20000