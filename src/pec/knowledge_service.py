"""PEC 知识库业务层：索引更新、索引状态、知识检索。"""

from __future__ import annotations

import json
import logging

from src.pec.knowledge.chunk_index import (
    format_update_result,
    get_index_status,
    search_chunks,
    update_knowledge_index,
)
from src.pec.knowledge.paths import (
    SOURCES_DIR,
    ensure_dirs,
)
logger = logging.getLogger(__name__)
DEFAULT_CHUNK_TOP_N = 5
MAX_INLINE_RESULT_BYTES = 20_000


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


def _build_evidence(chunk_hits: list) -> list[dict]:
    return [_chunk_evidence(hit) for hit in chunk_hits]


def _inline_size(result: dict) -> int:
    return len(json.dumps(result, ensure_ascii=False, default=str).encode("utf-8"))


def _bounded_result(result: dict, evidence: list[dict]) -> dict:
    fields = {
        "kind", "evidence_type", "chunk_id", "source_file", "location", "title",
        "text", "extraction_method", "provenance", "complete", "truncated",
    }
    selected: list[dict] = []

    def update(items: list[dict]) -> None:
        omitted = len(evidence) - len(items)
        limited = omitted > 0 or any(item.get("truncated") for item in items)
        result.update({
            "context_summary": (
                f"PEC 检索命中 {len(evidence)} 条证据，展示 {len(items)} 条。"
                + ("受大小限制，部分证据未展示或正文被截断。" if limited else "")
            ),
            "evidence": items,
            "evidence_count": len(evidence),
            "returned_evidence_count": len(items),
            "truncated_evidence_count": omitted,
        })

    update(selected)
    for item in evidence:
        candidate = {key: value for key, value in item.items() if key in fields}
        update([*selected, candidate])
        if _inline_size(result) <= MAX_INLINE_RESULT_BYTES:
            selected.append(candidate)
            continue
        if selected:
            continue
        text = str(candidate.get("text") or "")
        candidate.update(text="", complete=False, truncated=True)
        update([candidate])
        if _inline_size(result) > MAX_INLINE_RESULT_BYTES:
            continue
        lower, upper = 0, len(text)
        while lower < upper:
            midpoint = (lower + upper + 1) // 2
            candidate["text"] = text[:midpoint]
            if _inline_size(result) <= MAX_INLINE_RESULT_BYTES:
                lower = midpoint
            else:
                upper = midpoint - 1
        if lower:
            candidate["text"] = text[:lower]
            selected.append(candidate)
    update(selected)
    return result


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
    """检索已发布的 PEC 统一索引，不查询独立 Topics SQLite 库。"""

    query = query.strip()

    if not query:
        return {"success": False, "message": "PEC 检索失败：query 不能为空。"}

    try:
        chunk_hits = search_chunks(
            query,
            top_n=DEFAULT_CHUNK_TOP_N,
            folder=folder.strip(),
        )
        evidence = _build_evidence(chunk_hits)
        return _bounded_result({
            "success": True,
            "message": "PEC 知识检索完成。",
            "decision_found": any(
                item["evidence_type"] == "decision" for item in evidence
            ),
            "available_sources": ["chunks"],
            "partial": False,
            "chunk": {"hit_count": len(chunk_hits), "error": ""},
            "topics": {"hit_count": 0, "error": "", "enabled": False},
        }, evidence)

    except Exception as exc:
        logger.warning("PEC retrieval failed: %s", type(exc).__name__)
        return _bounded_result({
            "success": False,
            "message": "PEC 检索失败，请检查索引和模型配置。",
            "available_sources": [],
            "partial": False,
            "chunk": {"hit_count": 0, "error": "chunk 检索不可用。"},
            "topics": {"hit_count": 0, "error": "", "enabled": False},
        }, [])