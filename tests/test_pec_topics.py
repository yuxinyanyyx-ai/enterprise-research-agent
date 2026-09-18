import json
import sqlite3

from src.pec.topics.extractor import extract_topics
from src.pec.topics.repository import write_topics


class _Response:
    content = json.dumps(
        {
            "topics": [
                {
                    "title": "TENACITY 中国试验",
                    "decision": "按计划开展中国盲法姊妹试验",
                    "todo": "与 CDE 保持沟通",
                    "source": "Slide 12",
                    "raw_source_text": "原文",
                    "primary_category": "China Integration",
                    "secondary_category": "HAI",
                    "ta": ["Inflammation"],
                    "indication": [],
                    "evidence_chunk_ids": ["chunk-1"],
                }
            ]
        },
        ensure_ascii=False,
    )


class _FakeLlm:
    def invoke(self, *args, **kwargs):
        return _Response()


def test_extract_topics_validates_and_writes_sqlite(tmp_path) -> None:
    chunks_path = tmp_path / "chunks.jsonl"
    chunks_path.write_text(
        json.dumps(
            {
                "chunk_id": "chunk-1",
                "source_ref": "PEC1/test.pptx",
                "loc": "Slide 12",
                "text": "TENACITY 中国试验。PersisTENce 按计划开展中国盲法姊妹试验。",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    records = extract_topics(chunks_path=chunks_path, llm=_FakeLlm())
    assert len(records) == 1
    assert records[0].topic_id == 1
    assert records[0].evidence_chunk_ids == ["chunk-1"]

    db_path = tmp_path / "pec_topics.db"
    write_topics(records, db_path)

    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM topics").fetchone()[0] == 1
        row = connection.execute(
            "SELECT decision, todo, source_file FROM topics"
        ).fetchone()

    assert row == (
        "按计划开展中国盲法姊妹试验",
        "与 CDE 保持沟通",
        "PEC1/test.pptx",
    )


def test_extract_topics_discards_unknown_evidence(tmp_path) -> None:
    chunks_path = tmp_path / "chunks.jsonl"
    chunks_path.write_text(
        json.dumps(
            {
                "chunk_id": "chunk-1",
                "source_ref": "PEC1/test.pptx",
                "loc": "Slide 12",
                "text": "meeting text",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    class _NoEvidenceResponse:
        content = json.dumps(
            {
                "topics": [
                    {
                        "title": "unsupported evidence",
                        "evidence_chunk_ids": ["missing"],
                    }
                ]
            }
        )

    class _NoEvidenceLlm:
        def invoke(self, *args, **kwargs):
            return _NoEvidenceResponse()

    assert extract_topics(chunks_path=chunks_path, llm=_NoEvidenceLlm()) == []
