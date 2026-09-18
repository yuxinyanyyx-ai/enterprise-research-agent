from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path

from src.pec.topics.schema import TopicRecord

_SCHEMA = """
CREATE TABLE topics (
    source_file TEXT NOT NULL,
    topic_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    background TEXT NOT NULL DEFAULT '',
    background_summary TEXT NOT NULL DEFAULT '',
    for_endorsement TEXT NOT NULL DEFAULT '',
    decision TEXT NOT NULL DEFAULT '',
    todo TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    raw_source_text TEXT NOT NULL DEFAULT '',
    primary_category TEXT NOT NULL DEFAULT '',
    secondary_category TEXT,
    ta TEXT NOT NULL DEFAULT '[]',
    indication TEXT NOT NULL DEFAULT '[]',
    evidence_chunk_ids TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (source_file, topic_id)
);
CREATE INDEX idx_topics_primary_category ON topics(primary_category);
CREATE INDEX idx_topics_source_file ON topics(source_file);
"""


def _row_to_record(row: sqlite3.Row) -> TopicRecord:
    return TopicRecord(
        source_file=row["source_file"], topic_id=row["topic_id"], title=row["title"],
        background=row["background"], background_summary=row["background_summary"],
        for_endorsement=row["for_endorsement"], decision=row["decision"],
        todo=row["todo"], source=row["source"], raw_source_text=row["raw_source_text"],
        primary_category=row["primary_category"], secondary_category=row["secondary_category"],
        ta=json.loads(row["ta"] or "[]"), indication=json.loads(row["indication"] or "[]"),
        evidence_chunk_ids=json.loads(row["evidence_chunk_ids"] or "[]"),
    )


def _read_existing(db_path: Path, excluded_sources: set[str]) -> list[TopicRecord]:
    if not db_path.is_file():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM topics").fetchall()
        return [_row_to_record(row) for row in rows if row["source_file"] not in excluded_sources]
    finally:
        conn.close()


def write_topics(records: list[TopicRecord], db_path: str | Path, *, replace_sources: set[str] | None = None) -> Path:
    """Write a complete SQLite snapshot, preserving out-of-scope sources when requested."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sources = replace_sources or {record.source_file for record in records}
    all_records = _read_existing(path, sources) + records if replace_sources is not None else records
    fd, temp_name = tempfile.mkstemp(prefix=f"{path.stem}.", suffix=".tmp", dir=path.parent)
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        conn = sqlite3.connect(str(temp_path))
        try:
            conn.executescript(_SCHEMA)
            conn.executemany(
                "INSERT INTO topics (source_file, topic_id, title, background, background_summary, "
                "for_endorsement, decision, todo, source, raw_source_text, primary_category, "
                "secondary_category, ta, indication, evidence_chunk_ids) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(
                    r.source_file, r.topic_id, r.title, r.background, r.background_summary,
                    r.for_endorsement, r.decision, r.todo, r.source, r.raw_source_text,
                    r.primary_category, r.secondary_category, json.dumps(r.ta, ensure_ascii=False),
                    json.dumps(r.indication, ensure_ascii=False),
                    json.dumps(r.evidence_chunk_ids, ensure_ascii=False),
                ) for r in all_records],
            )
            conn.commit()
        finally:
            conn.close()
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return path


def topic_count(db_path: str | Path) -> int:
    path = Path(db_path)
    if not path.is_file():
        return 0
    with sqlite3.connect(str(path)) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0])
