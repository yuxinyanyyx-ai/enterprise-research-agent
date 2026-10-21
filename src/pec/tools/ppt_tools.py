"""Session-scoped tools for extracting uploaded presentation content."""

from pathlib import Path
from typing import Annotated

from langchain_core.tools import InjectedToolArg, tool

from src.pec.ppt_service import parse_pptx_structure_service
from src.settings import load_settings
from src.tools.registry import ToolContext, ToolRegistry, ToolRisk, register_tool


@tool(
    "parse_pptx_structure",
    description=(
        "解析当前 Agent 会话已上传的 PPTX 或图片，提取幻灯片文本或图片视觉识别结果。"
        "document_id 必须来自当前会话的上传文档。"
    ),
)
def parse_pptx_structure(
    document_id: str,
    documents: Annotated[dict | None, InjectedToolArg] = None,
) -> str:
    artifact = (documents or {}).get(document_id)
    if not isinstance(artifact, dict):
        raise FileNotFoundError("当前 Agent 会话中找不到该上传文件")

    settings = load_settings(require_token=False)
    source_path = Path(str(artifact.get("source_path") or "")).resolve()
    upload_root = settings.upload_dir.resolve()
    if not source_path.is_relative_to(upload_root) or not source_path.is_file():
        raise FileNotFoundError("上传文件不存在或不属于当前 Agent 会话")

    suffix = source_path.suffix.lower()
    if suffix == ".pptx":
        return parse_pptx_structure_service(str(source_path))
    if suffix in {".png", ".jpg", ".jpeg"}:
        markdown_path = Path(str(artifact.get("markdown_path") or "")).resolve()
        result_root = settings.result_dir.resolve()
        if not markdown_path.is_relative_to(result_root) or not markdown_path.is_file():
            raise FileNotFoundError("当前会话的图片 OCR 结果不存在")
        return markdown_path.read_text(encoding="utf-8-sig")
    raise ValueError("结构化解析仅支持 PPTX、PNG、JPG 和 JPEG 文件")


def register_ppt_tools(registry: ToolRegistry) -> None:
    register_tool(
        parse_pptx_structure,
        registry=registry,
        risk=ToolRisk.READ_ONLY,
        contexts={ToolContext.GENERAL},
        parallel_safe=True,
        state_arguments={"documents": "document_artifacts"},
    )
