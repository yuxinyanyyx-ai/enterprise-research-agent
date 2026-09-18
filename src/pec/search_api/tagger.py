"""Query / topic 打标签用的 LLM system prompt。"""

from src.pec.search_api.config import (
    CATEGORY_DEFINITIONS,
    INDICATION_LIST,
    TA_DEFINITIONS,
    TA_LIST,
)

TAGGER_SYSTEM_PROMPT = f"""{CATEGORY_DEFINITIONS}

{TA_DEFINITIONS}

你必须以严格的 JSON 格式返回标签，包含以下字段：
- primary_category: string（必填，从预定义类别列表选择）
- secondary_category: string or null（选填，从预定义类别列表选择，或 null）
- ta: list[string]（必填，从 {TA_LIST} 中选择，可多选）
- indication: list[string]（必填，从 {INDICATION_LIST} 中选择，可多选）
- confidence: "High" / "Medium" / "Low"
- reason: string（中文，1-3句话说明分类理由）

只返回 JSON，不要有任何额外说明文字。
"""
