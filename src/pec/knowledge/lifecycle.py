"""Reversible source withdrawal and conservative cross-document associations."""
import json
import re
import unicodedata
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import PurePosixPath

from src.pec.knowledge.paths import resolve_source_path
from src.pec.knowledge.sources import normalize_scope
from src.pec.knowledge.storage import active_dir, publish, WriterLock


def visible(manifest, ref):
    return manifest.get("files", {}).get(ref, {}).get("state", "active") != "withdrawn"


def topic_groups(topics, manifest):
    candidates = defaultdict(list)
    for topic in topics:
        if not visible(manifest, topic["source_ref"]):
            continue
        title = re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", topic["title"]).casefold())
        if len(title) < 6 or title in {"background", "decision", "approval", "nextsteps", "introduction", "conclusion"}:
            continue
        # A directory is a boundary, not a guessed meeting date or project identity.
        key = (str(PurePosixPath(topic["source_ref"]).parent), title)
        candidates[key].append(topic)
    return [members for members in candidates.values() if len({t["source_ref"] for t in members}) > 1]


def related_evidence(item, chunks, topics, manifest, scope=""):
    if not item.get("topic_id"):
        return []
    members = next((g for g in topic_groups(topics, manifest) if any(t["topic_id"] == item["topic_id"] for t in g)), [])
    by_id = {c["chunk_id"]: c for c in chunks}
    result = []
    for topic in members:
        if topic["source_ref"] == item["source_ref"] or (scope and not topic["source_ref"].startswith(scope + "/")):
            continue
        for field in ("for_endorsement", "decision", "todo", "background"):
            for fact in topic.get(field, []):
                evidence = by_id.get(fact["evidence_id"])
                if evidence and visible(manifest, evidence["source_ref"]):
                    result.append({**evidence, "text": f"[同名议题关联候选，未独立重排；不代表同一决议] {field}: {fact['quote']}"})
                if len(result) >= 6:
                    return _combine(result)
    # Combine facts sharing the same evidence ID to keep citation keys unique.
    return _combine(result)


def _combine(rows):
    unique = {}
    for row in rows:
        if row["chunk_id"] in unique:
            unique[row["chunk_id"]]["text"] += "\n" + row["text"]
        else:
            unique[row["chunk_id"]] = dict(row)
    return list(unique.values())


def source_inventory(scope=""):
    from src.pec.knowledge.chunk_index import _load_manifest
    manifest = _load_manifest()
    scope = normalize_scope(scope)
    return [{"source_ref": ref, "state": meta.get("state", "active"),
             "missing": not resolve_source_path(ref).is_file(), "chunk_count": meta.get("chunk_count", 0)}
            for ref, meta in manifest.get("files", {}).items() if not scope or ref.startswith(scope + "/")]


def change_source_state(ref, action):
    """Never delete bytes. A new generation atomically changes retrieval visibility."""
    from src.pec.knowledge.chunk_index import _load_manifest, _load_chunks, _load_embeddings
    from src.pec.knowledge.chunk_ingest import file_sha256
    if action not in {"withdraw", "restore", "sync_missing"}:
        raise ValueError("不支持的文件状态操作")
    ref = normalize_scope(ref)
    with WriterLock():
        directory = active_dir()
        manifest = _load_manifest(directory)
        if ref not in manifest.get("files", {}):
            raise ValueError("该文件尚未入库")
        path = resolve_source_path(ref)
        if action == "sync_missing" and path.exists():
            raise ValueError("文件仍然存在，不执行缺失同步")
        if action == "restore" and (not path.is_file() or file_sha256(path) != manifest["files"][ref].get("sha256")):
            raise ValueError("恢复前请放回与入库版本一致的原文件；新版本请使用不同文件名上传入库")
        manifest["files"][ref]["state"] = "active" if action == "restore" else "withdrawn"
        manifest["files"][ref]["state_reason"] = action
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        chunks = _load_chunks(directory)
        matrix = _load_embeddings(directory)
        if matrix is None:
            raise ValueError("索引缺少向量，请先完成入库")
        path = directory / "topics.json"
        topics = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        publish(manifest, chunks, matrix, topics)
        return {"source_ref": ref, "state": manifest["files"][ref]["state"]}


def list_source_files(scope="", query="", offset=0, limit=100):
    """List bytes under the knowledge source directory, including files never indexed."""
    from src.pec.knowledge import paths
    from src.pec.knowledge.chunk_index import _load_manifest
    scope = normalize_scope(scope)
    if offset < 0 or not 1 <= limit <= 200:
        raise ValueError("分页参数无效")
    if scope:
        resolve_source_path(scope)
    root = paths.SOURCES_DIR / scope
    if not root.is_dir():
        return {"files": [], "total": 0}
    indexed = _load_manifest().get("files", {})
    needle = query.casefold().strip()
    root_resolved = paths.SOURCES_DIR.resolve()
    rows = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.startswith("~$"):
            continue
        try:
            resolved = path.resolve()
            rel = paths.source_relative(path)
        except (OSError, ValueError):
            continue
        if resolved != root_resolved and root_resolved not in resolved.parents:
            continue
        if needle and needle not in rel.casefold():
            continue
        meta = indexed.get(rel, {})
        suffix = path.suffix.lower()
        rows.append({
            "source_ref": rel,
            "size": path.stat().st_size,
            "indexed": bool(meta),
            "state": meta.get("state", ""),
            "package_ok": zipfile.is_zipfile(path) if suffix in {".pptx", ".docx", ".xlsx"} else None,
        })
    return {"files": rows[offset:offset + limit], "total": len(rows)}


def delete_source_file(ref):
    """Remove one source file inside the knowledge directory. Indexed files leave retrieval."""
    ref = normalize_scope(ref)
    if not ref:
        raise ValueError("请指定要删除的文件")
    path = resolve_source_path(ref)
    if path.is_dir() or not path.is_file():
        raise ValueError("只能删除资料目录中的单个文件")
    try:
        path.unlink()
    except OSError as exc:
        raise ValueError("文件正在使用或无法删除，请稍后重试") from exc
    withdrawn = False
    try:
        change_source_state(ref, "sync_missing")
        withdrawn = True
    except ValueError as exc:
        if "尚未入库" not in str(exc) and "索引缺少向量" not in str(exc):
            raise
    return {"ok": True, "source_ref": ref, "index_withdrawn": withdrawn}
