"""knowledge/ 目录：原始资料 + 索引产物。"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

_configured_root = Path(os.getenv("PEC_KNOWLEDGE_ROOT", "data/pec")).expanduser()
KNOWLEDGE_ROOT = (_configured_root if _configured_root.is_absolute() else ROOT / _configured_root).resolve()

SOURCES_DIR = KNOWLEDGE_ROOT / "sources"

INDEX_DIR = KNOWLEDGE_ROOT / "index"

MANIFEST_FILE = INDEX_DIR / "manifest.json"
CHUNKS_FILE = INDEX_DIR / "chunks.jsonl"
EMBEDDINGS_FILE = INDEX_DIR / "embeddings.npy"

SUPPORTED_SUFFIXES = {".pptx", ".docx", ".pdf", ".xlsx"}


def ensure_dirs() -> None:
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_DIR.mkdir(parents=True, exist_ok=True)


def resolve_source_path(relative: str) -> Path:
    """只允许访问 sources 目录内的路径。"""
    rel = relative.strip().replace("\\", "/")
    if Path(rel).is_absolute() or (len(rel) >= 2 and rel[1] == ":"):
        raise ValueError(f"路径越界: {relative}")
    candidate = (SOURCES_DIR / rel).resolve()
    sources_root = SOURCES_DIR.resolve()
    if candidate != sources_root and sources_root not in candidate.parents:
        raise ValueError(f"路径越界: {relative}")
    return candidate


def normalize_source_scope(scope: str) -> tuple[str, Path]:
    path = resolve_source_path(scope)
    return source_relative(path) if path != SOURCES_DIR.resolve() else "", path


def source_in_scope(source_ref: str, scope: str) -> bool:
    normalized = scope.strip().replace("\\", "/").strip("/")
    reference = source_ref.replace("\\", "/").strip("/")
    return not normalized or reference == normalized or reference.startswith(normalized + "/")


def source_relative(path: Path) -> str:
    """相对 sources 的路径（junction 下避免 resolve 不一致）。"""
    try:
        return path.relative_to(SOURCES_DIR).as_posix()
    except ValueError:
        return path.resolve().relative_to(SOURCES_DIR.resolve()).as_posix()
