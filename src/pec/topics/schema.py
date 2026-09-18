from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.pec.search_api.config import INDICATION_LIST, TA_LIST, VALID_CATEGORIES


class TopicRecord(BaseModel):
    """A topic row compatible with the existing PEC search engine."""

    model_config = ConfigDict(extra="ignore")

    source_file: str = Field(min_length=1)
    topic_id: int = Field(ge=1)
    title: str = Field(min_length=1)
    background: str = ""
    background_summary: str = ""
    for_endorsement: str = ""
    decision: str = ""
    todo: str = ""
    source: str = ""
    raw_source_text: str = ""
    primary_category: str = ""
    secondary_category: str | None = None
    ta: list[str] = Field(default_factory=list)
    indication: list[str] = Field(default_factory=list)
    evidence_chunk_ids: list[str] = Field(default_factory=list)

    @field_validator("source_file", "title", "background", "background_summary",
                     "for_endorsement", "decision", "todo", "source", "raw_source_text",
                     mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> str:
        return "" if value is None else str(value).strip()

    @field_validator("ta", "indication", "evidence_chunk_ids", mode="before")
    @classmethod
    def normalize_list(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []


def validate_topic_enums(topic: TopicRecord) -> TopicRecord:
    if topic.primary_category and topic.primary_category not in VALID_CATEGORIES:
        raise ValueError(f"非法 primary_category: {topic.primary_category}")
    if topic.secondary_category and topic.secondary_category not in VALID_CATEGORIES:
        raise ValueError(f"非法 secondary_category: {topic.secondary_category}")
    invalid_ta = set(topic.ta) - set(TA_LIST)
    if invalid_ta:
        raise ValueError(f"非法 ta: {sorted(invalid_ta)}")
    invalid_indications = set(topic.indication) - set(INDICATION_LIST)
    if invalid_indications:
        raise ValueError(f"非法 indication: {sorted(invalid_indications)}")
    return topic
