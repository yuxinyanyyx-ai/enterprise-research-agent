"""Web API for knowledge-source inventory, uploads, and ingestion jobs."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import stat
from typing import Literal
from zipfile import BadZipFile, LargeZipFile, ZipFile

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from src.pec.knowledge import paths
from src.pec.knowledge.chunk_index import (
    _load_chunks,
    _load_manifest,
    get_index_status,
)
from src.pec.knowledge.jobs import list_jobs as list_knowledge_jobs
from src.pec.knowledge.jobs import start_job as start_knowledge_job
from src.pec.knowledge.lifecycle import visible
from src.pec.knowledge.meetings import meeting_topics
from src.pec.knowledge.paths import source_in_scope
from src.pec.knowledge.sources import LocalSourceProvider, normalize_scope
from src.pec.knowledge.storage import WriterLock, active_dir
from src.pec.knowledge.uploads import (
    MAX_BATCH_BYTES,
    MAX_BATCH_FILES,
    SourceUploadBatch,
    UploadLimitError,
)
from src.settings import get_settings

router = APIRouter(prefix="/api/knowledge", tags=["Meeting Knowledge"])


class KnowledgeJobRequest(BaseModel):
    scope: str = Field(default="", max_length=512)
    kind: Literal["check", "index"]
    confirmed: bool = False
    force: bool = False


def _scope(value: str) -> str:
    try:
        return normalize_scope(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="资料路径不合法") from exc


def _safe_filename(name: str) -> bool:
    reserved = {"CON", "PRN", "AUX", "NUL"} | {
        f"{prefix}{number}"
        for prefix in ("COM", "LPT")
        for number in range(1, 10)
    }
    return (
        bool(name)
        and not any(character in name for character in '/\\:<>"|?*\x00')
        and not name.endswith((" ", "."))
        and name.split(".")[0].upper() not in reserved
    )


def _uploaded_size(upload: UploadFile) -> int:
    if upload.size is not None:
        return upload.size
    stream = upload.file
    position = stream.tell()
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(position)
    return size


def _stage_zip(batch: SourceUploadBatch, upload: UploadFile, folder: str) -> list[str]:
    uploaded = []
    upload.file.seek(0)
    try:
        with ZipFile(upload.file) as archive:
            members = archive.infolist()
            if len(members) > MAX_BATCH_FILES:
                raise UploadLimitError("ZIP 文件最多包含 2000 项")
            for member in members:
                raw_path = member.filename.replace("\\", "/")
                parts = raw_path.rstrip("/").split("/")
                if (
                    not raw_path
                    or raw_path.startswith("/")
                    or (len(raw_path) >= 2 and raw_path[1] == ":")
                    or not parts
                    or any(part in {"", ".", ".."} or not _safe_filename(part) for part in parts)
                ):
                    raise ValueError("ZIP 中包含不合法路径或文件名")
                if stat.S_ISLNK(member.external_attr >> 16):
                    raise ValueError("ZIP 中不允许包含符号链接")
                if member.is_dir():
                    continue
                relative = "/".join([folder, *parts]) if folder else "/".join(parts)
                if Path(parts[-1]).suffix.lower() not in paths.SUPPORTED_SUFFIXES:
                    raise ValueError("ZIP 中包含不支持的资料格式")
                if member.file_size > MAX_BATCH_BYTES - batch.size:
                    raise UploadLimitError("ZIP 解压后超过 500 MB 批次限制")
                with archive.open(member) as stream:
                    batch.add_stream(relative, stream, max_bytes=MAX_BATCH_BYTES - batch.size)
                uploaded.append(relative)
    except (BadZipFile, LargeZipFile, NotImplementedError, RuntimeError, EOFError) as exc:
        raise ValueError("ZIP 文件损坏或压缩格式不支持") from exc
    if not uploaded:
        raise ValueError("ZIP 中没有可入库的资料文件")
    return uploaded


def _save_uploads(folder: str, files: list[UploadFile], file_size_limit: int) -> dict:
    root = paths.SOURCES_DIR
    root.mkdir(parents=True, exist_ok=True)
    relative_paths = []
    with WriterLock():
        with SourceUploadBatch(root) as batch:
            for upload in files:
                name = upload.filename or ""
                if name.lower().endswith(".zip"):
                    relative_paths.extend(_stage_zip(batch, upload, folder))
                    continue
                relative = f"{folder}/{name}" if folder else name
                upload.file.seek(0)
                batch.add_stream(relative, upload.file, max_bytes=file_size_limit)
                relative_paths.append(relative)
            batch.commit()
    return {"uploaded": relative_paths}


@router.get("/status")
def knowledge_status() -> dict:
    status = get_index_status()
    status["updating"] = any(job["status"] == "running" for job in list_knowledge_jobs())
    return status


@router.get("/sources")
def list_sources(scope: str = "") -> dict:
    normalized = _scope(scope)
    paths.SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    metadata = get_index_status().get("files", {})
    indexed = {
        reference: item
        for reference, item in metadata.items()
        if source_in_scope(reference, normalized)
    }
    _, scope_root = paths.normalize_source_scope(normalized)
    documents = LocalSourceProvider().discover(normalized) if scope_root.is_dir() else []
    disk = {document.source_ref: document.path for document in documents}
    references = sorted(set(disk) | set(indexed))
    sources = []
    for reference in references:
        path = disk.get(reference)
        metadata = indexed.get(reference, {})
        state = metadata.get("state", "pending") if path is not None else "missing"
        if path is not None and metadata and state != "withdrawn":
            state = "indexed" if metadata.get("sha256") == _file_sha256(path) else "changed"
        sources.append(
            {
                "path": reference,
                "filename": Path(reference).name,
                "format": Path(reference).suffix[1:].upper(),
                "state": state,
                "chunk_count": metadata.get("chunk_count", 0),
                "indexed_at": metadata.get("indexed_at", ""),
            }
        )
    scopes = sorted(
        {
            parent.as_posix()
            for reference in references
            for parent in Path(reference).parents
            if parent.as_posix() != "."
        }
    )
    return {"sources": sources, "scopes": scopes}


def _file_sha256(path: Path) -> str:
    from src.pec.knowledge.chunk_ingest import file_sha256

    return file_sha256(path)


@router.post("/sources", status_code=201)
async def upload_sources(
    files: list[UploadFile] = File(...),
    folder: str = Form(""),
) -> dict:
    normalized = _scope(folder)
    if not 1 <= len(files) <= MAX_BATCH_FILES:
        raise HTTPException(status_code=400, detail="单次上传需为 1 至 2000 个文件")
    limit = get_settings(require_token=False).max_file_size_bytes
    for upload in files:
        name = upload.filename or ""
        if (
            not _safe_filename(name)
            or (
                Path(name).suffix.lower() not in paths.SUPPORTED_SUFFIXES
                and Path(name).suffix.lower() != ".zip"
            )
        ):
            raise HTTPException(
                status_code=400,
                detail="文件名不合法或格式不支持（PPTX、DOCX、PDF、XLSX、ZIP）",
            )
        size = _uploaded_size(upload)
        if not size:
            raise HTTPException(status_code=413, detail="文件为空")
        if size > limit:
            raise HTTPException(status_code=413, detail="文件超过单文件上传大小限制")
    try:
        return await run_in_threadpool(_save_uploads, normalized, files, limit)
    except UploadLimitError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        if "已有入库任务运行" in str(exc):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise
    except OSError as exc:
        raise HTTPException(status_code=503, detail="文件保存失败，请稍后重试") from exc


@router.get("/jobs")
def get_jobs() -> dict:
    return {"jobs": deepcopy(list_knowledge_jobs())}


@router.post("/jobs", status_code=202)
def start_job(payload: KnowledgeJobRequest) -> dict:
    scope = _scope(payload.scope)
    if payload.kind == "index" and not payload.confirmed:
        raise HTTPException(status_code=400, detail="开始入库前必须确认处理范围")
    try:
        paths.ensure_dirs()
        if not LocalSourceProvider().discover(scope):
            raise HTTPException(status_code=400, detail="范围内没有可解析资料")
        job = start_knowledge_job(
            scope=scope,
            force=payload.force,
            inspect_only=payload.kind == "check",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        if "已有入库任务运行" in str(exc):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise
    return {"job": job}


@router.get("/topics")
def list_topics(
    level: Literal["file", "meeting"] = "file",
    scope: str = "",
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
) -> dict:
    normalized = _scope(scope)
    directory = active_dir()
    manifest = _load_manifest(directory)
    file_topics_path = directory / "topics.json"
    file_topics = (
        json.loads(file_topics_path.read_text(encoding="utf-8"))
        if file_topics_path.is_file()
        else []
    )
    if level == "meeting":
        meeting_topics_path = directory / "meeting_topics.json"
        topics = (
            json.loads(meeting_topics_path.read_text(encoding="utf-8"))
            if meeting_topics_path.is_file()
            else meeting_topics(file_topics, manifest, normalized)
        )
        topics = [
            topic
            for topic in topics
            if (
                not normalized
                or topic.get("meeting_scope") == normalized
                or topic.get("meeting_scope", "").startswith(normalized + "/")
            )
            and any(
                visible(manifest, reference)
                for reference in topic.get("source_refs", [topic.get("source_ref", "")])
            )
        ]
    else:
        topics = [
            topic
            for topic in file_topics
            if visible(manifest, topic["source_ref"])
            and source_in_scope(topic["source_ref"], normalized)
        ]
    evidence = {
        chunk["chunk_id"]: {
            "chunk_id": chunk["chunk_id"],
            "source_ref": chunk["source_ref"],
            "loc": chunk.get("loc", ""),
            "method": chunk.get("method", ""),
            "text": chunk["text"],
        }
        for chunk in _load_chunks(directory)
        if chunk.get("method") != "topic" and visible(manifest, chunk["source_ref"])
    }
    rows = []
    for topic in topics:
        row = dict(topic)
        row["level"] = level
        row["evidence"] = [
            evidence[identifier]
            for identifier in topic.get("evidence_ids", [])
            if identifier in evidence
        ]
        rows.append(row)
    total = len(rows)
    return {
        "topics": rows[offset : offset + limit],
        "total": total,
        "offset": offset,
        "limit": limit,
        "level": level,
    }
