"""PEC topics 结构化检索引擎（不含最终答案生成，仅返回 context）。"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

from langchain_openai import ChatOpenAI

from src.llm.apollo import create_apollo_llm, get_access_token, load_env
from src.pec.search_api.config import PEC_TOPICS_DB
from src.pec.search_api.retriever import (
    QueryTags,
    TopicRow,
    batch_judge_relevance,
    fetch_candidates,
    tag_query,
    topic_row_key,
)

logger = logging.getLogger(__name__)


def fetch_full_topics(
    conn: sqlite3.Connection, topics: list[TopicRow]
) -> dict[tuple[str, int], dict[str, str]]:
    if not topics:
        return {}
    where = " OR ".join("(source_file = ? AND topic_id = ?)" for _ in topics)
    params: list = []
    for t in topics:
        params.extend([t.source_file, t.topic_id])
    rows = conn.execute(
        f"SELECT source_file, topic_id, raw_source_text, background, for_endorsement, "
        f"decision, todo, source FROM topics WHERE {where}",
        params,
    ).fetchall()
    return {
        (r["source_file"], r["topic_id"]): {
            "raw_text": r["raw_source_text"] or "",
            "background": r["background"] or "",
            "for_endorsement": r["for_endorsement"] or "",
            "decision": r["decision"] or "",
            "follow_up": r["todo"] or "",
            "source": r["source"] or "",
        }
        for r in rows
    }


def format_topic_hits(result: dict[str, Any]) -> str:
    """格式化为 Agent 可读的文本 context。"""
    hits = result.get("hits") or []
    if not hits:
        db_path = result.get("db_path", "")
        err = result.get("error")
        if err:
            return f"[PEC Topics 检索不可用: {err}]"
        return (
            "未在 PEC topics 结构化库中找到相关 case。"
            + (f"（数据库: {db_path}）" if db_path else "")
        )

    tags = result.get("query_tags") or {}
    lines = [
        f"query_tags: primary={tags.get('primary_category')}, "
        f"secondary={tags.get('secondary_category')}, "
        f"ta={tags.get('ta')}, indication={tags.get('indication')}",
        f"candidate_count={result.get('candidate_count', 0)}, hits={len(hits)}",
        "",
    ]
    for i, hit in enumerate(hits, start=1):
        lines.append(
            f"### [Topic {i}] {hit.get('source_file', '')}#topic_id={hit.get('topic_id')} · "
            f"{hit.get('title', '')}"
        )
        if hit.get("source"):
            lines.append(f"- source: {hit['source']}")
        if hit.get("background"):
            lines.append(f"- background: {_preview(hit['background'], 800)}")
        if hit.get("for_endorsement"):
            lines.append(f"- for_endorsement: {_preview(hit['for_endorsement'], 600)}")
        if hit.get("decision"):
            lines.append(f"- decision: {_preview(hit['decision'], 1000)}")
        if hit.get("follow_up"):
            lines.append(f"- follow_up: {_preview(hit['follow_up'], 800)}")
        if hit.get("raw_text"):
            lines.append(f"- raw_text: {_preview(hit['raw_text'], 1500)}")
        lines.append("")
    return "\n".join(lines).strip()


def _preview(text: str, max_chars: int) -> str:
    text = (text or "").replace("\n", " ").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…"


def search_topics(
    query: str,
    *,
    db_path: str | None = None,
    llm: ChatOpenAI | None = None,
) -> dict[str, Any]:
    """
    三层 topics 检索，返回 hits 字典（不生成 LLM 答案）。
    数据库不存在或检索失败时返回 hits=[] 并附带 error 字段。
    """
    load_env()
    path = Path(db_path or PEC_TOPICS_DB)
    if not path.is_file():
        return {
            "query": query,
            "db_path": str(path),
            "query_tags": {},
            "candidate_count": 0,
            "hits": [],
            "error": f"PEC topics 数据库不存在: {path}",
        }

    try:
        if llm is None:
            llm = create_apollo_llm(get_access_token())

        query_tags: QueryTags = tag_query(query, llm)

        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        try:
            candidates: list[TopicRow] = fetch_candidates(conn, query_tags)
            candidate_count = len(candidates)
            relevant = batch_judge_relevance(query, candidates, llm)
            full_map = fetch_full_topics(conn, relevant)
        finally:
            conn.close()

        empty = {
            "raw_text": "",
            "background": "",
            "for_endorsement": "",
            "decision": "",
            "follow_up": "",
            "source": "",
        }
        hits = [
            {
                "source_file": t.source_file,
                "topic_id": t.topic_id,
                "title": t.title,
                **full_map.get(topic_row_key(t), empty),
            }
            for t in relevant
        ]

        return {
            "query": query,
            "db_path": str(path),
            "query_tags": {
                "primary_category": query_tags.primary_category,
                "secondary_category": query_tags.secondary_category,
                "ta": query_tags.ta,
                "indication": query_tags.indication,
            },
            "candidate_count": candidate_count,
            "hits": hits,
        }
    except Exception as exc:
        logger.exception("PEC topics 检索失败")
        return {
            "query": query,
            "db_path": str(path),
            "query_tags": {},
            "candidate_count": 0,
            "hits": [],
            "error": str(exc),
        }
