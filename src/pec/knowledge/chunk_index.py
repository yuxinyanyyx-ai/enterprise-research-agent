"""chunk 索引：manifest 增量 + cosine/rerank 检索。"""

from __future__ import annotations

import json
import hashlib
import argparse
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from src.llm.apollo import (
    embed_texts,
    get_access_token,
    load_env,
    rerank_documents,
)

from src.pec.knowledge.chunk_ingest import (
    chunk_draft_to_dict,
    file_sha256,
    ingest_file,
    scan_source_files,
)

from src.pec.knowledge.preview_build import (
    build_file_previews,
    previews_enabled,
    remove_file_previews,
)

from src.pec.knowledge.paths import (
    CHUNKS_FILE,
    EMBEDDINGS_FILE,
    MANIFEST_FILE,
    SOURCES_DIR,
    ensure_dirs,
    normalize_source_scope,
    source_in_scope,
    source_relative,
)


_index_lock = threading.RLock()


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rerank_min_score() -> float:
    """Rerank 相关性下限（0–1），默认 0.4 即 40%。"""
    return float(os.getenv("RERANK_MIN_SCORE", "0.4"))


def _load_manifest() -> dict:
    if not MANIFEST_FILE.is_file():
        return {"version": 1, "files": {}, "embedding_model": "", "updated_at": ""}
    return json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))


def _save_manifest(manifest: dict) -> None:
    MANIFEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_FILE.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_chunks() -> list[dict]:
    if not CHUNKS_FILE.is_file():
        return []
    chunks: list[dict] = []
    for line in CHUNKS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            chunks.append(json.loads(line))
    return chunks


def _save_chunks(chunks: list[dict]) -> None:
    CHUNKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with CHUNKS_FILE.open("w", encoding="utf-8") as fh:
        for item in chunks:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")


def _load_embeddings() -> np.ndarray | None:
    if not EMBEDDINGS_FILE.is_file():
        return None
    return np.load(EMBEDDINGS_FILE)


def _save_embeddings(matrix: np.ndarray) -> None:
    EMBEDDINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    np.save(EMBEDDINGS_FILE, matrix.astype(np.float32))


def _chunk_fingerprint(chunk: dict) -> str:
    payload = json.dumps(
        {
            "chunk_id": chunk.get("chunk_id"),
            "text": chunk.get("text"),
            "source_ref": chunk.get("source_ref"),
            "loc": chunk.get("loc"),
            "method": chunk.get("method"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_matrix(matrix: np.ndarray, expected_rows: int) -> bool:
    return (
        matrix.ndim == 2
        and len(matrix) == expected_rows
        and (expected_rows == 0 or matrix.shape[1] > 0)
        and bool(np.isfinite(matrix).all())
    )


def _chunks_digest(chunks: list[dict]) -> str:
    payload = "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in chunks)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _matrix_digest(matrix: np.ndarray) -> str:
    normalized = np.ascontiguousarray(matrix.astype(np.float32))
    return hashlib.sha256(normalized.tobytes()).hexdigest()


def _atomic_write_text(path: Path, content: str, token: str) -> Path:
    temporary = path.with_name(f".{path.name}.{token}.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    return temporary


def _commit_snapshot(chunks: list[dict], matrix: np.ndarray, manifest: dict) -> None:
    if not _validate_matrix(matrix, len(chunks)):
        raise RuntimeError("embedding 矩阵与 chunk 快照不一致")
    token = uuid.uuid4().hex
    chunk_text = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in chunks)
    chunk_tmp = _atomic_write_text(CHUNKS_FILE, chunk_text, token)
    manifest_tmp = _atomic_write_text(
        MANIFEST_FILE,
        json.dumps(manifest, ensure_ascii=False, indent=2),
        token,
    )
    embedding_tmp = EMBEDDINGS_FILE.with_name(f".{EMBEDDINGS_FILE.name}.{token}.tmp")
    try:
        EMBEDDINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with embedding_tmp.open("wb") as handle:
            np.save(handle, matrix.astype(np.float32))
            handle.flush()
            os.fsync(handle.fileno())
        chunk_tmp.replace(CHUNKS_FILE)
        embedding_tmp.replace(EMBEDDINGS_FILE)
        manifest_tmp.replace(MANIFEST_FILE)
    finally:
        for temporary in (chunk_tmp, embedding_tmp, manifest_tmp):
            temporary.unlink(missing_ok=True)


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


def _embeddings_ready(manifest: dict, chunks: list[dict], matrix: np.ndarray | None) -> bool:
    chunk_count = len(chunks)
    if chunk_count == 0:
        return bool(manifest.get("embeddings_ready", False))
    if matrix is None or not _validate_matrix(matrix, chunk_count):
        return False
    configured_model = (os.getenv("APOLLO_EMBEDDING_MODEL") or "").strip()
    if configured_model and manifest.get("embedding_model") != configured_model:
        return False
    if manifest.get("version", 1) >= 2 and (
        manifest.get("chunks_sha256") != _chunks_digest(chunks)
        or manifest.get("embeddings_sha256") != _matrix_digest(matrix)
    ):
        return False
    return bool(manifest.get("embeddings_ready", False))


def _build_incremental_embeddings(
    chunks: list[dict],
    old_chunks: list[dict],
    old_matrix: np.ndarray | None,
    *,
    reuse_old: bool,
) -> tuple[np.ndarray, int]:
    vectors: dict[str, np.ndarray] = {}
    if reuse_old and old_matrix is not None:
        for index, chunk in enumerate(old_chunks):
            fingerprint = _chunk_fingerprint(chunk)
            if fingerprint in vectors:
                raise RuntimeError("旧索引包含重复 chunk 内容标识")
            vectors[fingerprint] = old_matrix[index]

    missing = [chunk for chunk in chunks if _chunk_fingerprint(chunk) not in vectors]
    if missing:
        embedded = _batch_embed([chunk["text"] for chunk in missing])
        if len(embedded) != len(missing):
            raise RuntimeError("embedding 返回数量与新增 chunk 数量不一致")
        for chunk, vector in zip(missing, embedded):
            vectors[_chunk_fingerprint(chunk)] = np.asarray(vector, dtype=np.float32)

    if not chunks:
        return np.zeros((0, 0), dtype=np.float32), len(missing)
    dimensions = {len(vector) for vector in vectors.values()}
    if len(dimensions) != 1:
        raise RuntimeError("新旧 embedding 向量维度不一致，请执行全量重建")
    matrix = np.stack([vectors[_chunk_fingerprint(chunk)] for chunk in chunks]).astype(np.float32)
    if not _validate_matrix(matrix, len(chunks)):
        raise RuntimeError("生成的 embedding 矩阵无效")
    return matrix, len(missing)


def rebuild_embeddings_only() -> dict:
    """仅根据已有 chunks.jsonl 重算向量（ingest 已完成、embed 中断时用）。"""
    load_env()
    ensure_dirs()
    embedding_model = os.getenv("APOLLO_EMBEDDING_MODEL", "openai-text-embedding-3-large")

    with _index_lock:
        chunks = _load_chunks()
        if not chunks:
            raise RuntimeError("chunks.jsonl 为空，请先运行完整索引（勿加 --embed-only）。")

        print(f"重算 embedding：{len(chunks)} 个 chunk ...")
        matrix = _rebuild_embeddings(chunks)
        manifest = _load_manifest()
        manifest.update({
            "version": 2,
            "generation": uuid.uuid4().hex,
            "embedding_model": embedding_model,
            "chunk_count": len(chunks),
            "chunks_sha256": _chunks_digest(chunks),
            "embeddings_sha256": _matrix_digest(matrix),
            "embeddings_ready": True,
            "updated_at": _now_iso(),
            "source_root": str(SOURCES_DIR),
        })
        _commit_snapshot(chunks, matrix, manifest)

    return {
        "added": [],
        "updated": [],
        "removed": [],
        "skipped": [],
        "chunk_count": len(chunks),
        "new_chunks": 0,
        "scope": "(embed-only)",
        "mode": "embed_only",
    }

#索引到底怎么建、怎么存在磁盘、怎么更新
def update_knowledge_index(
    scope: str = "", *, force: bool = False, embed_only: bool = False
) -> dict:
    """增量更新索引。通过 manifest 里的 sha256 判断文件是否变化。"""
    if embed_only:
        return rebuild_embeddings_only()

    load_env()
    ensure_dirs()
    embedding_model = os.getenv("APOLLO_EMBEDDING_MODEL", "openai-text-embedding-3-large")
    normalized_scope, _ = normalize_source_scope(scope)

    with _index_lock:
        manifest = _load_manifest()
        old_chunks = _load_chunks()
        old_matrix = _load_embeddings()
        chunks = list(old_chunks)
        files_meta: dict = dict(manifest.get("files", {}))

        disk_files = scan_source_files(normalized_scope)
        disk_map = {source_relative(f): f for f in disk_files}

        added: list[str] = []
        updated: list[str] = []
        removed: list[str] = []
        skipped: list[str] = []
        new_chunk_count = 0
        stale_previews: list[str] = []
        for source_ref in list(files_meta.keys()):
            if not source_in_scope(source_ref, normalized_scope):
                continue
            if source_ref not in disk_map:
                chunks = _remove_file_chunks(chunks, source_ref)
                prev_digest = (files_meta.get(source_ref) or {}).get("sha256")
                if prev_digest:
                    stale_previews.append(prev_digest)
                removed.append(source_ref)
                del files_meta[source_ref]

        for source_ref, path in disk_map.items():
            digest = file_sha256(path)
            stat = path.stat()
            prev = files_meta.get(source_ref)
            if not force and prev and prev.get("sha256") == digest and prev.get("size") == stat.st_size:
                skipped.append(source_ref)
                continue

            preview_meta: dict = {"enabled": False}
            if previews_enabled():
                try:
                    preview_meta = build_file_previews(path, source_ref, digest)
                except Exception as exc:
                    print(f"    预览生成失败: {exc}")
                    preview_meta = {"enabled": True, "error": str(exc), "pages": []}

                    chunks = _remove_file_chunks(chunks, source_ref)
                    print(f"  抽取: {source_ref}")
                    drafts = ingest_file(path)
                    chunks.extend(chunk_draft_to_dict(draft) for draft in drafts)
                    new_chunk_count += len(drafts)

            files_meta[source_ref] = {
                "sha256": digest,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "chunk_count": len(drafts),
                "preview_pages": len(preview_meta.get("pages", [])),
                "preview_type": preview_meta.get("file_type", ""),
                "preview_error": preview_meta.get("error", ""),
                "indexed_at": _now_iso(),
            }
            if prev:
                updated.append(source_ref)
                previous_digest = prev.get("sha256")
                if previous_digest and previous_digest != digest:
                    stale_previews.append(previous_digest)
            else:
                added.append(source_ref)

        old_ready = (
            manifest.get("embedding_model") == embedding_model
            and _embeddings_ready(manifest, old_chunks, old_matrix)
        )
        changed = bool(added or updated or removed)
        if not changed and old_ready:
            return {
                "added": [], "updated": [], "removed": [], "skipped": skipped,
                "chunk_count": len(old_chunks), "new_chunks": 0, "embedded_chunks": 0,
                "preview_pages": 0, "scope": normalized_scope or "(all)",
            }

        print(f"开始向量化：{len(chunks)} 个 chunk ...")
        matrix, embedded_count = _build_incremental_embeddings(
            chunks, old_chunks, old_matrix, reuse_old=old_ready,
        )
        manifest.update({
            "version": 2,
            "generation": uuid.uuid4().hex,
            "embedding_model": embedding_model,
            "files": files_meta,
            "chunk_count": len(chunks),
            "chunks_sha256": _chunks_digest(chunks),
            "embeddings_sha256": _matrix_digest(matrix),
            "source_root": str(SOURCES_DIR),
            "embeddings_ready": True,
            "updated_at": _now_iso(),
        })
        _commit_snapshot(chunks, matrix, manifest)
        for digest in set(stale_previews):
            remove_file_previews(digest)

    preview_pages = 0
    for ref in added + updated:
        preview_pages += int(files_meta.get(ref, {}).get("preview_pages") or 0)

    return {
        "added": added,
        "updated": updated,
        "removed": removed,
        "skipped": skipped,
        "chunk_count": len(chunks),
        "new_chunks": new_chunk_count,
        "embedded_chunks": embedded_count,
        "preview_pages": preview_pages,
        "scope": normalized_scope or "(all)",
    }


def get_index_status() -> dict:
    ensure_dirs()
    with _index_lock:
        manifest = _load_manifest()
        chunks = _load_chunks()
        matrix = _load_embeddings()
    chunk_count = len(chunks)
    return {
        "sources_dir": str(SOURCES_DIR),
        "index_dir": str(MANIFEST_FILE.parent),
        "chunk_count": chunk_count,
        "file_count": len(manifest.get("files", {})),
        "embedding_model": manifest.get("embedding_model", ""),
        "updated_at": manifest.get("updated_at", ""),
        "manifest_exists": MANIFEST_FILE.is_file(),
        "embeddings_ready": _embeddings_ready(manifest, chunks, matrix),
    }


def search_chunks(
    query: str,
    *,
    top_n: int = 5,
    recall_top_k: int = 30,
    folder: str = "",
) -> list[ChunkHit]:
    load_env()
    with _index_lock:
        all_chunks = _load_chunks()
        matrix = _load_embeddings()
        manifest = _load_manifest()
    if not all_chunks:
        return []
    if not _embeddings_ready(manifest, all_chunks, matrix):
        raise RuntimeError(
            "向量索引未完成（embeddings 缺失或与 chunk 数量不一致）。"
            "请运行: python -m src.pec.knowledge.chunk_index update --embed-only"
        )

    if folder.strip():
        folder_name = folder.strip()
        id_to_idx = {c["chunk_id"]: i for i, c in enumerate(all_chunks)}
        chunks = [c for c in all_chunks if c.get("folder") == folder_name]
        if not chunks:
            return []
        indices = [id_to_idx[c["chunk_id"]] for c in chunks]
        matrix = matrix[indices]
    else:
        chunks = all_chunks

    token = get_access_token()
    query_vec = np.array(embed_texts([query], access_token=token)[0], dtype=np.float32)
    scores = _cosine(query_vec, matrix)
    order = np.argsort(scores)[::-1][:recall_top_k]

    candidates: list[tuple[dict, float]] = []
    for idx in order:
        candidates.append((chunks[int(idx)], float(scores[int(idx)])))

    docs = [c[0]["text"] for c in candidates]
    reranked = rerank_documents(query, docs, top_n=len(docs), access_token=token)
    min_score = _rerank_min_score()

    hits: list[ChunkHit] = []
    for row in reranked:
        score = float(row.get("relevance_score", 0.0))
        if score < min_score:
            continue
        item, embed_score = candidates[row["index"]]
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
            )
        )
        if len(hits) >= top_n:
            break
    return hits


def format_hits(hits: list[ChunkHit]) -> str:
    if not hits:
        return (
            f"未找到 rerank 相关性 ≥ {_rerank_min_score():.0%} 的 chunk。"
            "请先调用 update_knowledge_index 建立/更新索引。"
        )
    lines: list[str] = []
    for i, hit in enumerate(hits, start=1):
        lines.append(f"### [{i}] {hit.source_ref} · {hit.loc}")
        lines.append(f"- folder: {hit.folder}")
        lines.append(f"- method: {hit.method}")
        lines.append(f"- rerank_score: {hit.rerank_score:.4f}")
        preview = hit.text.replace("\n", " ")
        if len(preview) > 1500:
            preview = preview[:1500] + "..."
        lines.append(f"- text: {preview}")
        lines.append("")
    return "\n".join(lines).strip()


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
        f"- 本次计算向量: {result.get('embedded_chunks', result['new_chunks'])}\n"
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


def main() -> None:
    parser = argparse.ArgumentParser(description="PEC chunk 索引管理")
    subparsers = parser.add_subparsers(dest="command", required=True)
    update = subparsers.add_parser("update", help="建立或增量更新索引")
    update.add_argument("--scope", default="", help="sources 下的目录或文件")
    update.add_argument("--force", action="store_true", help="强制重新抽取 scope 内文件")
    update.add_argument("--embed-only", action="store_true", help="仅重算已有 chunks 的向量")
    subparsers.add_parser("status", help="显示索引状态")
    args = parser.parse_args()
    if args.command == "status":
        print(json.dumps(get_index_status(), ensure_ascii=False, indent=2))
        return
    print(format_update_result(update_knowledge_index(
        scope=args.scope,
        force=args.force,
        embed_only=args.embed_only,
    )))


if __name__ == "__main__":
    main()
