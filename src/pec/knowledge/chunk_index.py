"""chunk 索引：manifest 增量 + cosine/rerank 检索。"""

from __future__ import annotations

import json
import hashlib
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from src.llm.apollo import embed_texts, get_access_token, load_env, rerank_documents
from src.pec.knowledge.chunk_ingest import (
    chunk_draft_to_dict,
    file_sha256,
    ingest_file,
)
from src.pec.knowledge.storage import active_dir, publish, WriterLock
from src.pec.knowledge.sources import LocalSourceProvider, normalize_scope
from src.pec.knowledge.quality import inspect_quality
from src.pec.knowledge.lifecycle import visible, related_evidence
from src.pec.knowledge.retrieval import recall_queries as build_recall_queries, lexical_scores_many, fuse, diverse, excerpt, neighbors
from src.pec.knowledge.enrichment import extract_topics, topic_chunks, recipe, VERSION, quality_report_path
from src.pec.knowledge.meetings import build_meeting_plans, meeting_topics, meeting_evidence
from src.pec.knowledge.markdown import evidence_markdown
from src.pec.knowledge.preview_build import build_file_previews, previews_enabled
from src.observability.tracing import trace_span
from src.pec.knowledge.paths import (
    MANIFEST_FILE,
    INDEX_DIR,
    SOURCES_DIR,
    ensure_dirs,
)


@dataclass
class ChunkHit:
    chunk_id: str
    text: str
    source_ref: str
    folder: str
    loc: str
    method: str
    embed_score: float
    rerank_score: float | None = None
    evidence: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    query: str = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rerank_min_score() -> float:
    """Rerank 相关性下限（0–1），默认 0.4 即 40%。"""
    return float(os.getenv("RERANK_MIN_SCORE", "0.4"))


def _load_manifest(directory: Path | None = None) -> dict:
    path = (directory or active_dir()) / "manifest.json"
    if not path.is_file():
        return {"version": 1, "files": {}, "embedding_model": "", "updated_at": ""}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_chunks(directory: Path | None = None) -> list[dict]:
    path = (directory or active_dir()) / "chunks.jsonl"
    if not path.is_file():
        return []
    chunks: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            chunks.append(json.loads(line))
    return chunks


def _load_embeddings(directory: Path | None = None, *, mmap: bool = False) -> np.ndarray | None:
    path = (directory or active_dir()) / "embeddings.npy"
    if not path.is_file():
        return None
    return np.load(path, allow_pickle=False, mmap_mode="r" if mmap else None)


def _cosine(query: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    q = query / (np.linalg.norm(query) + 1e-12)
    m = matrix / (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-12)
    return m @ q


def _batch_embed(texts: list[str], batch_size: int = 16) -> list[list[float]]:
    vectors: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        vectors.extend(embed_texts(texts[i : i + batch_size]))
    return vectors


def _rebuild_embeddings(chunks: list[dict]) -> np.ndarray:
    if not chunks:
        return np.zeros((0, 0), dtype=np.float32)
    texts = [c["text"] for c in chunks]
    vectors = _batch_embed(texts)
    return np.array(vectors, dtype=np.float32)


def _remove_file_chunks(chunks: list[dict], source_ref: str) -> list[dict]:
    return [c for c in chunks if c.get("source_ref") != source_ref]


def _embeddings_ready(manifest: dict, chunk_count: int, matrix: np.ndarray | None) -> bool:
    if chunk_count == 0:
        return True
    if matrix is None or matrix.ndim != 2 or len(matrix) == 0 or matrix.shape[1] == 0:
        return False
    if len(matrix) != chunk_count:
        return False
    return manifest.get("embeddings_ready", True)


def rebuild_embeddings_only() -> dict:
    return update_knowledge_index(embed_only=True)


def update_knowledge_index(scope: str = "", *, force: bool = False,
                           embed_only: bool = False, tags_only: bool = False, progress=None,
                           inspect_only: bool = False, _locked: bool = False,
                           provider=None) -> dict:
    """Independent ingestion service. Publish only after every stage succeeds."""
    if not _locked:
        with WriterLock():
            return update_knowledge_index(scope, force=force, embed_only=embed_only,
                progress=progress, inspect_only=inspect_only, tags_only=tags_only, _locked=True, provider=provider)
    if tags_only:
        raise ValueError("已取消标签补充")
    if embed_only and inspect_only:
        raise ValueError("入库模式不能同时启用")
    load_env()
    ensure_dirs()
    scope = normalize_scope(scope)
    report = progress or (lambda **event: None)
    directory = active_dir()  # pin a coherent version for this operation
    manifest = _load_manifest(directory)
    old_chunks = _load_chunks(directory)
    old_matrix = _load_embeddings(directory)
    chunks = list(old_chunks)
    files_meta = dict(manifest.get("files", {}))
    topics_path = directory / "topics.json"
    topics = json.loads(topics_path.read_text(encoding="utf-8")) if topics_path.is_file() else []
    model = os.getenv("APOLLO_EMBEDDING_MODEL", "openai-text-embedding-3-large")
    added, updated, skipped = [], [], []
    diagnostics = []
    new_count = 0
    report(stage="scan", message="扫描本地资料")
    before_tags = json.dumps(topics, sort_keys=True)
    before_meetings = json.dumps(manifest.get("meeting_plans", {}), sort_keys=True)
    if not embed_only and not tags_only:
        documents = (provider or LocalSourceProvider()).discover(scope)
        if not documents:
            raise ValueError("所选范围没有支持的文件，请先上传 PPTX / DOCX / PDF / XLSX")
        for i, document in enumerate(documents, 1):
            path, ref = document.path, document.source_ref
            report(stage="parse", message=f"解析 {ref}", completed=i-1, total=len(documents))
            prev = files_meta.get(ref)
            if prev and prev.get("state") == "withdrawn":
                skipped.append(ref)
                diagnostics.append({"source_ref": ref, "status": "withdrawn", "chunk_count": 0, "warnings": [{"loc": "文件", "reason": "已撤销，需先在文件管理中恢复"}]})
                continue
            digest = file_sha256(path)
            if not force and not inspect_only and prev and prev.get("sha256") == digest and prev.get("parser_version") == VERSION and prev.get("recipe") == recipe():
                skipped.append(ref)
                diagnostics.append({"source_ref": ref, "status": prev.get("parse_status", "unchanged"), "chunk_count": prev.get("chunk_count", 0), "warnings": prev.get("warnings", []), "topic_count": prev.get("topic_count", 0), "vision_pages": 0})
                continue
            parsed = [chunk_draft_to_dict(c) for c in ingest_file(path)]
            warnings = inspect_quality(path, parsed)
            if digest != file_sha256(path):
                raise RuntimeError(f"处理期间文件发生变化，请重新入库: {ref}")
            diagnostics.append({"source_ref": ref, "status": "needs_review" if warnings else "parsed", "chunk_count": len(parsed), "warnings": warnings})
            report(stage="parse", message=f"已解析 {ref}", completed=i, total=len(documents), files=diagnostics)
            preview_meta = {}
            file_topics = []
            vision_pages = 0
            markdown_path = None
            if not inspect_only:
                markdown_path = "parsed/" + hashlib.sha256((ref + digest + recipe()).encode()).hexdigest() + ".md"
                md_file = INDEX_DIR / markdown_path
                md_file.parent.mkdir(parents=True, exist_ok=True)
                md_file.write_text(evidence_markdown(parsed), encoding="utf-8")
                diagnostics[-1].update(chunk_count=len(parsed), vision_pages=0, warnings=warnings,
                                      status="extracting_topics" if parsed else "skipped_no_text")
                report(stage="parse_done", message=f"原生文本解析完成 {ref}，已清理图片标记", files=diagnostics)
                if previews_enabled() and not vision_pages:
                    report(stage="preview", message=f"生成预览 {ref}", files=diagnostics)
                    try:
                        preview_meta = build_file_previews(path, ref, digest)
                    except Exception as exc:
                        warnings.append({"loc": "预览", "reason": f"生成失败：{exc}"})
                file_topics = extract_topics(ref, digest, parsed, report, force) if parsed else []
                topics = [t for t in topics if t["source_ref"] != ref] + file_topics
                parsed += topic_chunks(file_topics)
                if digest != file_sha256(path):
                    raise RuntimeError(f"处理期间文件发生变化，请重新入库：{ref}")
            diagnostics[-1].update(chunk_count=len(parsed), topic_count=len(file_topics), vision_pages=vision_pages, warnings=warnings,
                                   topics=[{"title": t["title"], "topic_id": t["topic_id"]} for t in file_topics],
                                   status=("processed" if parsed else "skipped_no_text") if not inspect_only else "inspected")
            report(stage="file_done", message=f"完成 {ref}：{len(file_topics)} 个 topic" if parsed else f"跳过 {ref}：无可提取文本", files=diagnostics)
            chunks = _remove_file_chunks(chunks, ref) + parsed
            new_count += len(parsed)
            files_meta[ref] = {"sha256": digest, "size": path.stat().st_size,
                "chunk_count": len(parsed), "parser_version": VERSION, "recipe": recipe(), "topic_count": len(file_topics), "vision_pages": vision_pages, "indexed_at": _now_iso(),
                "provider": document.provider, "external_id": document.external_id,
                "parse_status": "processed" if parsed else "skipped_no_text", "markdown_path": markdown_path,
                "quality_report": quality_report_path(ref, digest) if parsed and not inspect_only else None,
                "warnings": warnings, "preview_pages": len(preview_meta.get("pages", [])),
                "preview_type": preview_meta.get("file_type", "")}
            (updated if prev else added).append(ref)
    if not inspect_only and not embed_only and not tags_only:
        manifest["meeting_plans"] = build_meeting_plans(topics, report, force, chunks, scope, manifest.get("meeting_plans"))
    if not inspect_only and not embed_only:
        chunks = [c for c in chunks if c.get("method") != "topic"] + topic_chunks(topics)
        from collections import Counter
        counts = Counter(c["source_ref"] for c in chunks)
        files_meta = {ref:dict(meta, chunk_count=counts.get(ref,0)) for ref,meta in files_meta.items()}
    metadata_changed = before_tags != json.dumps(topics, sort_keys=True) or before_meetings != json.dumps(manifest.get("meeting_plans", {}), sort_keys=True)
    result = {"added": added, "updated": updated, "removed": [], "skipped": skipped,
        "topic_count": len(topics), "chunk_count": len(chunks), "new_chunks": new_count, "scope": scope or "(all)",
        "meeting_topic_count": len(meeting_topics(topics, {**manifest, "files": files_meta})),
        "files": diagnostics, "tags_pending": 0, "mode": "inspect" if inspect_only else "embed_only" if embed_only else "update"}
    if inspect_only:
        report(stage="inspected", message="本地解析检查完成，未调用模型、未发布索引", files=diagnostics)
        return result
    cache = {}
    if not embed_only and old_matrix is not None and _embeddings_ready(manifest, len(old_chunks), old_matrix) and manifest.get("embedding_model") == model:
        cache = {c["text"]: old_matrix[i] for i, c in enumerate(old_chunks)}
    missing = list(dict.fromkeys(c["text"] for c in chunks if c["text"] not in cache))
    for offset in range(0, len(missing), 16):
        batch = missing[offset:offset+16]
        report(stage="embed", message=f"生成向量 {offset}/{len(missing)}", completed=offset, total=len(missing))
        vectors = _batch_embed(batch)
        if len(vectors) != len(batch):
            raise RuntimeError("模型返回向量数量不匹配")
        cache.update(zip(batch, vectors))
    matrix = np.asarray([cache[c["text"]] for c in chunks], dtype=np.float32) if chunks else np.empty((0, 0), dtype=np.float32)
    result["embedded_texts"] = len(missing)
    missing_set = set(missing)
    result["reused_chunks"] = sum(c["text"] not in missing_set for c in chunks)
    if not added and not updated and not missing and not metadata_changed and manifest.get("version") == VERSION:
        report(stage="unchanged", message="资料和向量均未变化，保留当前版本")
        return result
    report(stage="publish", message="发布完整索引版本")
    manifest.update(version=VERSION, topic_count=len(topics), files=files_meta, chunk_count=len(chunks),
        embedding_model=model, embeddings_ready=True, updated_at=_now_iso(), source_root=str(SOURCES_DIR))
    publish(manifest, chunks, matrix, topics if not embed_only or topics_path.exists() else None)
    return result


def get_index_status() -> dict:
    ensure_dirs()
    directory = active_dir()
    manifest = _load_manifest(directory)
    chunks = _load_chunks(directory)
    # Status polling needs only the array header, not a full vector copy in RAM.
    matrix = _load_embeddings(directory, mmap=True)
    chunk_count = sum(visible(manifest, c["source_ref"]) for c in chunks)
    topics_path = directory / "topics.json"
    topic_count = sum(visible(manifest, t["source_ref"]) for t in json.loads(topics_path.read_text(encoding="utf-8"))) if topics_path.is_file() else 0
    return {
        "sources_dir": str(SOURCES_DIR),
        "index_dir": str(MANIFEST_FILE.parent),
        "chunk_count": chunk_count,
        "file_count": sum(visible(manifest, ref) for ref in manifest.get("files", {})),
        "withdrawn_count": sum(not visible(manifest, ref) for ref in manifest.get("files", {})),
        "topic_count": topic_count,
        "embedding_model": manifest.get("embedding_model", ""),
        "updated_at": manifest.get("updated_at", ""),
        "manifest_exists": (directory / "manifest.json").is_file(),
        "files": manifest.get("files", {}),
        "embeddings_ready": _embeddings_ready(manifest, len(chunks), matrix),
    }


def _search_chunks_impl(
    query: str,
    *,
    top_n: int = 8,
    recall_top_k: int = 40,
    folder: str = "",
    _directory: Path | None = None,
    tag_filters: dict | None = None,
    recall_queries: list[str] | None = None,
    original_query: str = "",
) -> list[ChunkHit]:
    load_env()
    directory = _directory or active_dir()
    all_chunks = _load_chunks(directory)
    matrix = _load_embeddings(directory)
    manifest = _load_manifest(directory)
    if not all_chunks:
        return []
    from src.pec.knowledge.tagging import normalize_filters, matches
    tag_filters = normalize_filters(tag_filters)
    topic_path = directory / "topics.json"
    filtered_topics = json.loads(topic_path.read_text(encoding="utf-8")) if topic_path.is_file() else []
    allowed = {identifier for t in filtered_topics if matches(t,tag_filters) for identifier in [t["topic_id"], *t.get("evidence_ids",[])]}
    scope = normalize_scope(folder)
    indices = [i for i, c in enumerate(all_chunks) if visible(manifest, c["source_ref"]) and (not scope or c["source_ref"].startswith(scope + "/")) and (not tag_filters or c.get("topic_id", c["chunk_id"]) in allowed)]
    chunks = [all_chunks[i] for i in indices]
    if not chunks or not query.strip():
        return []
    top_n = max(1, min(top_n, 20))
    recall_top_k = max(top_n, min(recall_top_k, 100))
    warnings = []
    ranking_query = original_query.strip() or query
    queries = build_recall_queries(query, recall_queries)
    lexical_orders = [sorted((i for i, score in enumerate(lexical) if score > 0), key=lambda i: (-lexical[i], i))[:recall_top_k]
                      for lexical in lexical_scores_many(queries, chunks)]
    vector_order = []
    scores = np.zeros(len(chunks))
    try:
        if not _embeddings_ready(manifest, len(all_chunks), matrix):
            raise RuntimeError("向量索引未就绪")
        if manifest.get("embedding_model") != os.getenv("APOLLO_EMBEDDING_MODEL", "openai-text-embedding-3-large"):
            raise RuntimeError("当前 embedding 模型与入库模型不一致，请在知识库重算向量")
        query_vec = np.array(embed_texts([query])[0], dtype=np.float32)
        if matrix.shape[1] != len(query_vec) or not np.isfinite(query_vec).all():
            raise RuntimeError("查询向量维度或数值异常")
        scores = _cosine(query_vec, matrix[indices])
        vector_order = np.argsort(scores)[::-1][:recall_top_k].tolist()
    except Exception as exc:
        warnings.append(f"向量召回不可用，已降级为关键词召回：{exc}")
    order = fuse(vector_order, lexical_orders[0], chunks, min(60, recall_top_k * 2), lexical_orders[1:])
    if not order:
        if warnings:
            raise RuntimeError("；".join(warnings) + "；关键词也未找到候选证据")
        return []
    docs = [chunks[i]["source_ref"] + " · " + chunks[i].get("loc", "") + "\n" + chunks[i]["text"] for i in order]
    try:
        reranked = rerank_documents(ranking_query, docs, top_n=len(docs))
        rows = []
        seen = set()
        for row in reranked:
            index = row["index"]
            score = float(row.get("relevance_score", 0.))
            if not isinstance(index, int) or not 0 <= index < len(order) or not np.isfinite(score):
                raise ValueError("重排返回非法结果")
            if index not in seen and score >= _rerank_min_score():
                rows.append((order[index], score))
                seen.add(index)
        rows.sort(key=lambda row: -row[1])
    except Exception as exc:
        warnings.append(f"重排不可用，使用融合排名；相关性尚未确认：{exc}")
        rows = [(i, None) for i in order]
    selected = diverse(rows, chunks, top_n)
    if not selected and warnings:
        raise RuntimeError("；".join(warnings) + "；未找到达到相关性阈值的证据")

    hits: list[ChunkHit] = []
    topics_path = directory / "topics.json"
    topics = json.loads(topics_path.read_text(encoding="utf-8")) if topics_path.is_file() else []
    context_chunks = chunks if tag_filters else all_chunks
    topics = [t for t in topics if matches(t,tag_filters)]
    evidence_by_id = {c["chunk_id"]: c for c in context_chunks if visible(manifest, c["source_ref"])}
    meetings = meeting_topics(topics, manifest, scope, tag_filters)
    for index, score in selected:
        item = chunks[index]
        embed_score = float(scores[index])
        hits.append(
            ChunkHit(
                chunk_id=item["chunk_id"],
                text=item["text"],
                source_ref=item["source_ref"],
                folder=item.get("folder", ""),
                loc=item.get("loc", ""),
                method=item.get("method", ""),
                embed_score=embed_score,
                rerank_score=score,
                warnings=warnings,
                query=ranking_query,
                evidence=[{**evidence_by_id[eid], "text": "\n".join(item.get("evidence_quotes", {}).get(eid, [evidence_by_id[eid]["text"]]))}
                          for eid in item.get("evidence_ids", []) if eid in evidence_by_id]
                         + meeting_evidence(item, meetings, evidence_by_id)
                         + neighbors(item, context_chunks) + related_evidence(item, context_chunks, topics, manifest, scope),
            )
        )
        if len(hits) >= top_n:
            break
    return hits


def search_chunks(
    query: str,
    *,
    top_n: int = 8,
    recall_top_k: int = 40,
    folder: str = "",
    _directory: Path | None = None,
    tag_filters: dict | None = None,
    recall_queries: list[str] | None = None,
    original_query: str = "",
) -> list[ChunkHit]:
    with trace_span(
        "retrieval.search_chunks",
        "pec.knowledge.retrieval",
        metadata={"top_n": top_n, "recall_top_k": recall_top_k},
    ) as span:
        hits = _search_chunks_impl(
            query,
            top_n=top_n,
            recall_top_k=recall_top_k,
            folder=folder,
            _directory=_directory,
            tag_filters=tag_filters,
            recall_queries=recall_queries,
            original_query=original_query,
        )
        span.metadata["result_count"] = len(hits)
        return hits


def format_hits(hits: list[ChunkHit]) -> str:
    if not hits:
        return "在当前检索范围内未找到足够相关的证据。不要据此推断会议从未讨论；也不要编造答案。"
    budget = max(4000, min(int(os.getenv("RETRIEVAL_CONTEXT_CHARS", "24000")), 60000))
    lines = []
    used = 0
    allowance = budget // len(hits)
    for hit in hits:
        header = f"### [{hit.chunk_id}] {hit.source_ref} · {hit.loc} ({hit.method})\n"
        score = f"rerank: {hit.rerank_score:.4f}" if hit.rerank_score is not None else "融合召回：未完成相关性重排"
        supporting = "\n".join(f"[{e['chunk_id']}] {e['source_ref']} · {e['loc']} ({e['method']}): {e['text']}" for e in hit.evidence)
        remaining = max(0, allowance - len(header) - len(score) - 50)
        body = excerpt(hit.text, remaining // 2 if supporting else remaining, hit.query)
        if supporting:
            body += "\n支撑证据：\n" + excerpt(supporting, remaining // 2)
        block = header + score + "\n" + body
        if used + len(block) > budget:
            break
        lines.append(block)
        used += len(block) + 2
    return "\n\n".join(lines)


def format_update_result(result: dict) -> str:
    if result.get("mode") == "embed_only":
        return (
            f"向量重算完成。\n"
            f"- 总 chunk 数: {result['chunk_count']}\n"
            f"- 模式: 仅 embedding（跳过 ingest / Vision）"
        )
    return (
        f"索引更新完成（scope={result['scope']}）。\n"
        f"- 总 chunk 数: {result['chunk_count']}\n"
        f"- 本次新增 chunk: {result['new_chunks']}\n"
        f"- 新增文件: {len(result['added'])}\n"
        f"- 更新文件: {len(result['updated'])}\n"
        f"- 删除文件: {len(result['removed'])}\n"
        f"- 跳过未变文件: {len(result['skipped'])}\n"
        + (
            f"- 页面预览: {result['preview_pages']} 页\n"
            if result.get("preview_pages")
            else ""
        )
        + (f"- added: {', '.join(result['added'][:10])}\n" if result["added"] else "")
        + (f"- updated: {', '.join(result['updated'][:10])}\n" if result["updated"] else "")
    )
