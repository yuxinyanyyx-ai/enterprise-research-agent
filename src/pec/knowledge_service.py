"""PEC 知识库业务层：索引更新、索引状态、知识检索。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from src.pec.knowledge.chunk_index import (
    format_hits,
    format_update_result,
    get_index_status,
    search_chunks,
    update_knowledge_index,
)
from src.pec.knowledge.paths import (
    SOURCES_DIR,
    ensure_dirs,
)
from src.pec.search_api.engine import (
    format_topic_hits,
    search_topics,
)


DEFAULT_CHUNK_TOP_N = 5


def _chunk_evidence(hit) -> dict:
    method = getattr(hit, "method", "") or "text"
    evidence_type = {
        "text": "source_text",
        "table": "source_table",
        "vision_page": "source_visual",
    }.get(method, "source_text")
    return {
        "kind": "chunk",
        "evidence_type": evidence_type,
        "chunk_id": getattr(hit, "chunk_id", ""),
        "method": method,
        "render_mode": getattr(hit, "render_mode", ""),
        "vision_model": getattr(hit, "vision_model", ""),
        "vision_prompt_version": getattr(hit, "vision_prompt_version", ""),
        "source_digest": getattr(hit, "source_digest", ""),
        "source_file": getattr(hit, "source_ref", ""),
        "location": getattr(hit, "loc", ""),
        "text": getattr(hit, "text", ""),
        "embed_score": getattr(hit, "embed_score", None),
        "rerank_score": getattr(hit, "rerank_score", None),
        "extraction_method": "vision" if method == "vision_page" else "native",
        "complete": True,
        "truncated": False,
    }


def _topic_evidence(hit: dict, field: str, evidence_type: str) -> dict | None:
    text = str(hit.get(field) or "").strip()
    if not text:
        return None
    return {
        "kind": "topic",
        "evidence_type": evidence_type,
        "source_file": hit.get("source_file", ""),
        "location": hit.get("source", ""),
        "title": hit.get("title", ""),
        "text": text,
        "evidence_chunk_ids": hit.get("evidence_chunk_ids", "[]"),
        "provenance": "topic_extraction",
        "complete": True,
        "truncated": False,
    }


def _build_evidence(chunk_hits: list, topic_hits: list[dict]) -> list[dict]:
    evidence: list[dict] = []
    for hit in topic_hits:
        for field, evidence_type in (
            ("decision", "decision"),
            ("follow_up", "follow_up"),
            ("for_endorsement", "recommendation"),
            ("background", "background"),
            ("raw_text", "source_text"),
        ):
            item = _topic_evidence(hit, field, evidence_type)
            if item:
                evidence.append(item)
    evidence.extend(_chunk_evidence(hit) for hit in chunk_hits)
    return evidence


def update_meeting_index_service(
    scope: str = "",
    force: bool = False,
    embed_only: bool = False,
) -> str:
    """更新 PEC 会议知识库索引。"""

    ensure_dirs()

    if embed_only:
        result = update_knowledge_index(
            scope=scope,
            force=force,
            embed_only=True,
        )
        return format_update_result(result)

    if not SOURCES_DIR.exists() or not any(SOURCES_DIR.iterdir()):
        return (
            f"sources 目录为空: {SOURCES_DIR}\n"
            "请将会议原始文件放入 sources 目录后再更新索引。"
        )

    result = update_knowledge_index(
        scope=scope,
        force=force,
    )

    return format_update_result(result)


def get_meeting_index_status_service() -> str:
    """获取 PEC 会议知识库索引状态。"""

    ensure_dirs()

    status = get_index_status()

    lines = [
        f"sources: {status['sources_dir']}",
        f"index: {status['index_dir']}",
        f"chunk_count: {status['chunk_count']}",
        f"indexed_files: {status['file_count']}",
        f"embedding_model: {status['embedding_model'] or '(none)'}",
        f"updated_at: {status['updated_at'] or '(never)'}",
        f"embeddings_ready: {status['embeddings_ready']}",
    ]

    if status["chunk_count"] == 0:
        lines.append(
            "提示: 索引为空，请调用 update_meeting_index。"
        )

    elif not status["embeddings_ready"]:
        lines.append(
            "提示: chunk 已存在但向量未就绪，"
            "请调用 update_meeting_index(embed_only=True)。"
        )

    return "\n".join(lines)


def search_pec_knowledge_service(
    query: str,
    folder: str = "",
) -> dict:
    """并行检索 PEC chunk 向量库和 topics 结构化库。"""

    query = query.strip()

    if not query:
        return {"success": False, "message": "PEC 检索失败：query 不能为空。", "query": ""}

    chunk_text = ""
    topic_text = ""
    chunk_err: str | None = None
    topic_err: str | None = None

    def run_chunks() -> tuple[list, str, str | None]:
        try:
            hits = search_chunks(
                query,
                top_n=DEFAULT_CHUNK_TOP_N,
                folder=folder.strip(),
            )

            return (
                hits,
                format_hits(hits),
                None,
            )

        except RuntimeError as exc:
            return [], str(exc), str(exc)

        except Exception as exc:
            return (
                [],
                f"chunk 检索异常: {exc}",
                str(exc),
            )

    def run_topics() -> tuple[list[dict], str, str | None]:
        try:
            result = search_topics(query)

            hits = result.get("hits") or []

            return (
                hits,
                format_topic_hits(result),
                result.get("error"),
            )

        except Exception as exc:
            return (
                [],
                f"[PEC Topics 检索不可用: {exc}]",
                str(exc),
            )

    try:
        # 两套知识库并行查询
        with ThreadPoolExecutor(max_workers=2) as pool:
            chunk_future = pool.submit(run_chunks)
            topic_future = pool.submit(run_topics)

            chunk_hits, chunk_text, chunk_err = (
                chunk_future.result()
            )

            topic_hits, topic_text, topic_err = (
                topic_future.result()
            )

        sections = [
            "# Retrieved PEC Context",
            "",
            "## A. Chunk 向量检索（页码级原文片段）",
            chunk_text or "（无 chunk 命中）",
            "",
            "## B. PEC Topics 结构化检索（case / decision 级）",
            topic_text or "（无 topic 命中）",
        ]

        if chunk_err and not chunk_hits:
            sections.extend(
                [
                    "",
                    f"（chunk 检索提示: {chunk_err}）",
                ]
            )

        available_sources = [
            name for name, error in (("chunks", chunk_err), ("topics", topic_err)) if not error
        ]
        success = bool(available_sources)
        evidence = _build_evidence(chunk_hits, topic_hits)
        return {
            "success": success,
            "message": (
                "PEC 知识检索完成。"
                if success
                else "PEC chunk 与 topics 检索均不可用，请检查索引和模型配置。"
            ),
            "query": query,
            "context": "\n".join(sections).strip(),
            "evidence": evidence,
            "evidence_count": len(evidence),
            "truncated_evidence_count": 0,
            "decision_found": any(
                item["evidence_type"] == "decision" for item in evidence
            ),
            "available_sources": available_sources,
            "partial": success and len(available_sources) < 2,
            "chunk": {"hit_count": len(chunk_hits), "error": chunk_err or ""},
            "topics": {"hit_count": len(topic_hits), "error": topic_err or ""},
        }

    except Exception as exc:
        return {
            "success": False,
            "message": f"PEC 检索失败: {exc}",
            "query": query,
        }