"""Immutable index generations with one atomic publication pointer."""
import json
import os
import uuid
import time
from pathlib import Path

import numpy as np

from src.pec.knowledge.paths import INDEX_DIR


def retry_file_operation(operation):
    # Windows readers can briefly deny rename/open while another handle closes.
    for attempt in range(20):
        try:
            return operation()
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(.01)


def active_dir() -> Path:
    pointer = INDEX_DIR / "CURRENT"
    if not pointer.exists():
        return INDEX_DIR  # existing installation, before its first successful update
    name = retry_file_operation(lambda: pointer.read_text(encoding="ascii")).strip()
    if len(name) != 32 or any(c not in "0123456789abcdef" for c in name):
        raise RuntimeError("索引版本指针损坏")
    return INDEX_DIR / "generations" / name


def publish(manifest: dict, chunks: list[dict], matrix: np.ndarray, topics: list[dict] | None = None) -> None:
    with np.errstate(over="ignore", invalid="ignore"):
        matrix = matrix.astype(np.float32)
    if matrix.ndim != 2 or len(matrix) != len(chunks) or (chunks and matrix.shape[1] == 0) or not np.isfinite(matrix).all():
        raise ValueError("向量与原文不一致，未发布索引")
    evidence_ids = {c["chunk_id"] for c in chunks if c.get("method") != "topic"}
    if topics is not None and any(not set(t["evidence_ids"]).issubset(evidence_ids) for t in topics):
        raise ValueError("topic 引用不属于本次索引版本，未发布索引")
    name = uuid.uuid4().hex
    published_manifest = {**manifest, "generation": name}
    target = INDEX_DIR / "generations" / name
    target.mkdir(parents=True)
    (target / "manifest.json").write_text(json.dumps(published_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (target / "chunks.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in chunks), encoding="utf-8")
    if topics is not None:
        (target / "topics.json").write_text(json.dumps(topics, ensure_ascii=False, indent=2), encoding="utf-8")
        from src.pec.knowledge.meetings import meeting_topics
        meetings = meeting_topics(topics, published_manifest)
        if any(not set(t["evidence_ids"]).issubset(evidence_ids) for t in meetings):
            raise ValueError("会议 topic 引用不属于本次索引版本，未发布")
        (target / "meeting_topics.json").write_text(json.dumps(meetings, ensure_ascii=False, indent=2), encoding="utf-8")
    np.save(target / "embeddings.npy", matrix)
    pointer = INDEX_DIR / (name + ".tmp")
    pointer.write_text(name, encoding="ascii")
    retry_file_operation(lambda: os.replace(pointer, INDEX_DIR / "CURRENT"))


class WriterLock:
    """OS lock, released automatically on process termination; one PVC writer."""
    def __init__(self):
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        self.file = (INDEX_DIR / "writer.lock").open("a+b")
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError("已有入库任务运行，请等待完成") from None

    def close(self):
        self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
