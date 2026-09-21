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
    monkeypatch.setattr(knowledge_service, "format_hits", lambda hits: "chunk context")
    monkeypatch.setattr(knowledge_service, "search_topics", topics)
    monkeypatch.setattr(knowledge_service, "format_topic_hits", lambda result: "topic context")
    result = knowledge_service.search_pec_knowledge_service("decision")
    assert result["success"] is success
    assert result.get("partial", False) is partial
    assert "# 回答要求" not in result.get("context", "")


def test_search_service_rejects_empty_query() -> None:
    result = knowledge_service.search_pec_knowledge_service("  ")
    assert result["success"] is False
    assert "不能为空" in result["message"]