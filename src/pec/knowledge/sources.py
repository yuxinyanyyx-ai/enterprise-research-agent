"""Acquisition boundary: providers materialize files under SOURCES_DIR.

A future SharePoint provider should download by drive/item/version, retain its
remote identity in provenance, and return local files through this contract.
Parsing/indexing never depends on Microsoft Graph or browser uploads.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from src.pec.knowledge.paths import SOURCES_DIR, SUPPORTED_SUFFIXES, resolve_source_path, source_relative


@dataclass(frozen=True)
class SourceDocument:
    path: Path
    source_ref: str
    provider: str
    external_id: str


class SourceProvider(Protocol):
    def discover(self, scope: str = "") -> list[SourceDocument]: ...


def normalize_scope(scope: str) -> str:
    scope = scope.strip().replace("\\", "/")
    if scope.startswith("/") or ":" in scope or ".." in scope.split("/"):
        raise ValueError("范围必须是资料目录下的相对路径")
    return scope.strip("/")


class LocalSourceProvider:
    def discover(self, scope: str = "") -> list[SourceDocument]:
        scope = normalize_scope(scope)
        root = resolve_source_path(scope)
        if not root.is_dir():
            raise ValueError("资料目录不存在，请先上传文件或检查服务器资料路径")
        result = []
        # Keep lexical paths so configured source-root junctions remain usable.
        for path in sorted((SOURCES_DIR / scope).rglob("*")):
            if path.is_file() and not path.name.startswith('~$') and path.suffix.lower() in SUPPORTED_SUFFIXES:
                ref = source_relative(path)
                resolve_source_path(ref)  # reject symlinks escaping the configured root
                result.append(SourceDocument(path, ref, "local", ref))
        return result
