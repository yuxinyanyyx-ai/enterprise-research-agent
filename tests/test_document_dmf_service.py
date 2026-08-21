from pathlib import Path

import pytest

from src.mineru.services.mineru_client import BatchParseResult, ParseResult
from src.schemas.document_dmf import ExtractedDMFQuery
from src.services.document_dmf_service import DocumentDMFError, DocumentDMFService
from src.settings import load_settings


def _settings(tmp_path: Path):
	return load_settings(
		{
			"MINERU_TOKEN": "test-token",
			"RESULT_DIR": str(tmp_path / "results"),
			"UPLOAD_DIR": str(tmp_path / "uploads"),
			"TEMP_DIR": str(tmp_path / "temp"),
		},
	)


def test_markdown_document_is_stored_and_extracted(tmp_path: Path) -> None:
	source = tmp_path / "sample.md"
	source.write_text("原料 Ibuprofen", encoding="utf-8")
	seen: list[str] = []
	service = DocumentDMFService(
		_settings(tmp_path),
		extractor=lambda markdown: (
			seen.append(markdown)
			or {"ingredients": [" Ibuprofen ", "ibuprofen", ""]}
		),
	)

	artifact = service.parse_document(source)
	query = service.extract_query(artifact)

	assert Path(artifact.markdown_path).is_file()
	assert Path(artifact.markdown_path).is_relative_to(tmp_path / "results")
	assert seen == ["原料 Ibuprofen"]
	assert query.ingredients == ["Ibuprofen"]


def test_service_rejects_markdown_outside_result_directory(tmp_path: Path) -> None:
	outside = tmp_path / "outside.md"
	outside.write_text("Ibuprofen", encoding="utf-8")
	service = DocumentDMFService(_settings(tmp_path), extractor=lambda text: {})

	with pytest.raises(DocumentDMFError, match="结果目录之外"):
		service.extract_query(
			{
				"document_id": "outside",
				"file_name": outside.name,
				"source_path": str(outside),
				"markdown_path": str(outside),
				"status": "parsed",
			}
		)


def test_confirmed_query_is_the_only_dmf_execution_boundary(tmp_path: Path) -> None:
	calls: list[dict] = []
	service = DocumentDMFService(
		_settings(tmp_path),
		searcher=lambda **kwargs: calls.append(kwargs) or {"success": True},
	)
	query = ExtractedDMFQuery(ingredients=["Ibuprofen"])

	assert calls == []
	assert service.execute_confirmed_query(query) == {"success": True}
	assert calls == [
		{"dmf_no": "", "applicant_name": "", "ingredients": ["Ibuprofen"]}
	]


def test_empty_flow_conditions_are_rejected(tmp_path: Path) -> None:
	source = tmp_path / "sample.md"
	source.write_text("没有查询条件", encoding="utf-8")
	service = DocumentDMFService(_settings(tmp_path), extractor=lambda text: {})
	artifact = service.parse_document(source)

	with pytest.raises(DocumentDMFError, match="未提取到可用"):
		service.extract_query(artifact)


def test_pdf_is_parsed_by_mineru_and_returns_trusted_artifact(tmp_path: Path) -> None:
	settings = _settings(tmp_path)
	source = tmp_path / "sample.pdf"
	source.write_bytes(b"%PDF-test")
	markdown = settings.result_dir / "mineru-id" / "document.md"
	markdown.parent.mkdir(parents=True)
	markdown.write_text("Ibuprofen", encoding="utf-8")
	seen: list[Path] = []

	class FakeMinerU:
		def __init__(self, configured_settings) -> None:
			assert configured_settings is settings

		def __enter__(self):
			return self

		def __exit__(self, exc_type, exc, traceback) -> None:
			return None

		def parse_files(self, paths):
			seen.extend(paths)
			return BatchParseResult(
				batch_id="batch-1",
				files=(
					ParseResult(
						data_id="mineru-id",
						original_name=source.name,
						state="done",
						markdown_path=markdown,
					),
				),
			)

	service = DocumentDMFService(settings, mineru_factory=FakeMinerU)
	artifact = service.parse_document(source)

	assert seen == [source.resolve()]
	assert artifact.document_id == "mineru-id"
	assert artifact.markdown_path == str(markdown.resolve())


def test_mineru_failure_stops_document_pipeline(tmp_path: Path) -> None:
	settings = _settings(tmp_path)
	source = tmp_path / "sample.pdf"
	source.write_bytes(b"%PDF-test")

	class FailingMinerU:
		def __init__(self, configured_settings) -> None:
			pass

		def __enter__(self):
			return self

		def __exit__(self, exc_type, exc, traceback) -> None:
			return None

		def parse_files(self, paths):
			return BatchParseResult(
				batch_id="batch-1",
				files=(
					ParseResult(
						data_id="mineru-id",
						original_name=source.name,
						state="failed",
						error="MinerU 测试失败",
					),
				),
			)

	service = DocumentDMFService(settings, mineru_factory=FailingMinerU)

	with pytest.raises(DocumentDMFError, match="MinerU 测试失败"):
		service.parse_document(source)