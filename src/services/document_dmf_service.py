"""Service boundary for parsing documents and running confirmed DMF queries."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.Apollo.workflow_client import extract_dmf_query_params
from src.dmf_query.multi_query_service import search_dmf_queries
from src.mineru.services.mineru_client import BatchParseResult, MinerUClient
from src.schemas.document_dmf import DocumentArtifact, ExtractedDMFQuery
from src.settings import Settings, get_settings


class DocumentDMFError(RuntimeError):
	"""Document workflow input or processing failed."""


class DocumentDMFService:
	"""Coordinate MinerU, Apollo Studio Flow, and confirmed DMF searches."""

	def __init__(
		self,
		settings: Settings | None = None,
		*,
		mineru_factory: Callable[[Settings], MinerUClient] = MinerUClient,
		extractor: Callable[[str], dict[str, Any]] = extract_dmf_query_params,
		searcher: Callable[..., dict[str, Any]] = search_dmf_queries,
	) -> None:
		self.settings = settings or get_settings()
		self._mineru_factory = mineru_factory
		self._extractor = extractor
		self._searcher = searcher

	def parse_document(self, file_path: str | Path) -> DocumentArtifact:
		path = Path(file_path).expanduser().resolve()
		if not path.is_file():
			raise DocumentDMFError(f"文件不存在：{path}")
		if path.stat().st_size == 0:
			raise DocumentDMFError(f"文件为空：{path.name}")
		if path.stat().st_size > self.settings.max_file_size_bytes:
			raise DocumentDMFError(f"文件超过大小限制：{path.name}")

		suffix = path.suffix.lower()
		if suffix in {".md", ".txt"}:
			return self._store_text_document(path)
		if suffix not in self.settings.allowed_extensions:
			raise DocumentDMFError(f"不支持的文件格式：{path.name}")

		with self._mineru_factory(self.settings) as client:
			result = client.parse_files([path])
		return self._artifact_from_mineru(path, result)

	def extract_query(
		self,
		artifact: DocumentArtifact | dict[str, Any],
	) -> ExtractedDMFQuery:
		validated_artifact = DocumentArtifact.model_validate(artifact)
		markdown_path = self._trusted_markdown_path(validated_artifact.markdown_path)
		try:
			markdown = markdown_path.read_text(encoding="utf-8-sig")
		except OSError as exc:
			raise DocumentDMFError(f"读取 Markdown 失败：{exc}") from exc
		if not markdown.strip():
			raise DocumentDMFError("Markdown 内容为空")

		try:
			return ExtractedDMFQuery.model_validate(self._extractor(markdown))
		except Exception as exc:
			raise DocumentDMFError(f"Flow 提取 DMF 条件失败：{exc}") from exc

	def execute_confirmed_query(self, query: ExtractedDMFQuery) -> dict[str, Any]:
		validated = ExtractedDMFQuery.model_validate(query)
		return self._searcher(
			dmf_no=validated.dmf_no,
			applicant_name=validated.applicant_name,
			ingredients=validated.ingredients,
		)

	def _store_text_document(self, path: Path) -> DocumentArtifact:
		document_id = uuid4().hex
		document_dir = self.settings.result_dir / "documents" / document_id
		document_dir.mkdir(parents=True, exist_ok=False)
		markdown_path = document_dir / "document.md"
		try:
			text = path.read_text(encoding="utf-8-sig")
		except (OSError, UnicodeError) as exc:
			shutil.rmtree(document_dir, ignore_errors=True)
			raise DocumentDMFError(f"读取文档失败：{exc}") from exc
		if not text.strip():
			shutil.rmtree(document_dir, ignore_errors=True)
			raise DocumentDMFError("文档内容为空")
		markdown_path.write_text(text, encoding="utf-8")
		return DocumentArtifact(
			document_id=document_id,
			file_name=path.name,
			source_path=str(path),
			markdown_path=str(markdown_path.resolve()),
		)

	def _artifact_from_mineru(
		self,
		path: Path,
		result: BatchParseResult,
	) -> DocumentArtifact:
		if not result.succeeded:
			error = result.files[0].error if result.files else "MinerU 未返回文件结果"
			raise DocumentDMFError(error or "MinerU 文档解析失败")
		parsed = result.succeeded[0]
		if parsed.markdown_path is None:
			raise DocumentDMFError("MinerU 未生成 Markdown 文件")
		markdown_path = parsed.markdown_path.resolve()
		self._trusted_markdown_path(markdown_path)
		return DocumentArtifact(
			document_id=parsed.data_id,
			file_name=path.name,
			source_path=str(path),
			markdown_path=str(markdown_path),
		)

	def _trusted_markdown_path(self, value: str | Path) -> Path:
		path = Path(value).resolve()
		root = self.settings.result_dir.resolve()
		if not path.is_relative_to(root):
			raise DocumentDMFError("拒绝读取结果目录之外的 Markdown")
		if not path.is_file():
			raise DocumentDMFError(f"Markdown 文件不存在：{path}")
		return path