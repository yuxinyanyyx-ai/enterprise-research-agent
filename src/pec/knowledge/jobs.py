"""Durable job reports; execution is independent of the chat agent/request.

This first version uses one background thread and an OS writer lock. A process
restart leaves an interrupted job for explicit retry; it never claims success.
"""
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone

from src.pec.knowledge.paths import INDEX_DIR
from src.pec.knowledge.sources import normalize_scope
from src.pec.knowledge.storage import WriterLock, retry_file_operation

logger = logging.getLogger(__name__)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _save(job):
    root = INDEX_DIR / "jobs"
    root.mkdir(parents=True, exist_ok=True)
    target = root / (job["id"] + ".json")
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    retry_file_operation(lambda: os.replace(temporary, target))


def _normalize_report(job):
    """Older reports may omit arrays; malformed fields must not crash the UI."""
    for field, default in (("files", []), ("events", []), ("scope", ""), ("message", "")):
        job.setdefault(field, default)
    job.setdefault("kind", "check" if job.get("inspect_only") else "index")
    if not isinstance(job["scope"], str) or not isinstance(job["message"], str):
        raise ValueError("Invalid job text")
    if job["kind"] not in {"check", "index"}:
        raise ValueError("Invalid job kind")
    if not isinstance(job["events"], list) or not isinstance(job["files"], list):
        raise ValueError("Invalid job arrays")
    if job.get("result") is not None and not isinstance(job["result"], dict):
        raise ValueError("Invalid job result")
    for field in ("embedded_texts", "reused_chunks"):
        if (job.get("result") or {}).get(field) is not None and type(job["result"][field]) is not int:
            raise ValueError("Invalid job result count")
    for event in job["events"]:
        if not isinstance(event, dict) or not isinstance(event.get("message"), str):
            raise ValueError("Invalid job event")
    for file in job["files"]:
        if not isinstance(file, dict) or not isinstance(file.get("source_ref"), str) or type(file.get("chunk_count")) is not int:
            raise ValueError("Invalid file report")
        file.setdefault("warnings", [])
        if file.get("topic_count") is not None and type(file["topic_count"]) is not int:
            raise ValueError("Invalid file topic count")
        if not isinstance(file["warnings"], list):
            raise ValueError("Invalid file warnings")
        for warning in file["warnings"]:
            if not isinstance(warning, dict) or not isinstance(warning.get("reason"), str) or not isinstance(warning.get("loc", ""), str):
                raise ValueError("Invalid file warning")
        if file.get("topics") is not None and (not isinstance(file["topics"], list) or any(
                not isinstance(topic, dict) or not isinstance(topic.get("title"), str)
                for topic in file["topics"])):
            raise ValueError("Invalid file topics")
    return job


def list_jobs():
    # If no process owns the writer lock, any persisted running job was interrupted.
    try:
        lock = WriterLock()
    except RuntimeError:
        lock = None
    try:
        jobs = []
        for path in (INDEX_DIR / "jobs").glob("*.json"):
            try:
                job = json.loads(retry_file_operation(lambda: path.read_text(encoding="utf-8")))
                if not isinstance(job, dict) or job.get("id") != path.stem or not isinstance(job.get("created_at"), str) or not isinstance(job.get("status"), str) or job.get("status") not in {"running", "failed", "succeeded", "interrupted"}:
                    raise ValueError("Invalid job report")
                job = _normalize_report(job)
            except (ValueError, UnicodeError, OSError):
                # Keep the damaged report for diagnosis; do not block all future jobs.
                logger.warning("Skipping damaged ingestion report: %s", path.name)
                continue
            if lock and job["status"] == "running":
                job.update(status="interrupted", message="服务已重启，任务中断；可重新提交，已发布版本仍可用", updated_at=_now())
                _save(job)
            jobs.append(job)
        return sorted(jobs, key=lambda j: j["created_at"], reverse=True)[:20]
    finally:
        if lock:
            lock.close()


def start_job(*, scope="", force=False, embed_only=False, inspect_only=False, tags_only=False):
    if tags_only:
        raise ValueError("已取消标签补充")
    if embed_only and inspect_only:
        raise ValueError("入库模式不能同时启用")
    scope = normalize_scope(scope)
    list_jobs()  # reconcile reports from previous processes
    lock = WriterLock()
    job = {"id": uuid.uuid4().hex, "status": "running", "stage": "queued",
        "kind": "check" if inspect_only else "index",
        "message": "等待处理", "scope": scope, "inspect_only": inspect_only, "tags_only": tags_only,
        "created_at": _now(), "updated_at": _now(), "files": [], "events": []}
    def report(**event):
        job.update(event, updated_at=_now())
        job["events"] = (job["events"] + [{"at": _now(), "stage": event["stage"], "message": event["message"]}])[-100:]
        _save(job)

    def run():
        try:
            from src.pec.knowledge.chunk_index import update_knowledge_index
            result = update_knowledge_index(scope, force=force, embed_only=embed_only,
                inspect_only=inspect_only, tags_only=tags_only, progress=report, _locked=True)
            job.update(status="succeeded", result=result, files=result["files"],
                       message="解析检查完成（未入库）" if inspect_only else "入库完成", updated_at=_now())
        except Exception as exc:
            logger.exception("知识库入库失败")
            job.update(status="failed", message=str(exc), updated_at=_now())
        finally:
            job["finished_at"] = _now()
            try:
                _save(job)
            finally:
                lock.close()
    try:
        _save(job)
        response = dict(job)
        threading.Thread(target=run, name="knowledge-ingestion", daemon=True).start()
        return response
    except Exception:
        lock.close()
        raise
