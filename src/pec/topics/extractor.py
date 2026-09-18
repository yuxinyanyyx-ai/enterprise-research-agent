from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from src.llm.apollo import create_apollo_llm, get_access_token, load_env
from src.pec.knowledge.paths import CHUNKS_FILE, source_in_scope
from src.pec.search_api.config import INDICATION_LIST, TA_LIST, VALID_CATEGORIES
from src.pec.topics.schema import TopicRecord, validate_topic_enums

_PROMPT = f"""你是 PEC 会议资料结构化抽取器。只根据给定原文抽取 0 到多个 topic，禁止补写原文没有明确表达的决策或行动项。
输出严格 JSON：{{\"topics\": [{{\"title\":\"\",\"background\":\"\",\"background_summary\":\"\",\"for_endorsement\":\"\",\"decision\":\"\",\"todo\":\"\",\"source\":\"\",\"raw_source_text\":\"\",\"primary_category\":\"\",\"secondary_category\":null,\"ta\":[],\"indication\":[],\"evidence_chunk_ids\":[]}}]}}
只有原文明确出现的 decision/todo 才填写，否则为空字符串。每个 topic 必须引用输入中的 evidence_chunk_ids。合法 category: {sorted(VALID_CATEGORIES)}。合法 ta: {TA_LIST}。合法 indication: {INDICATION_LIST}。"""


def _parse_json(content: Any) -> dict[str, Any]:
    raw = content if isinstance(content, str) else str(content)
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
        raw = re.sub(r"\s*```$", "", raw.strip())
    decoder = json.JSONDecoder()
    for index, char in enumerate(raw):
        if char in "{[":
            try:
                value, _ = decoder.raw_decode(raw[index:])
                if isinstance(value, list):
                    return {"topics": value}
                if isinstance(value, dict):
                    return value
            except json.JSONDecodeError:
                continue
    raise ValueError("Apollo topic 响应不是有效 JSON")


def _sort_key(chunk: dict[str, Any]) -> tuple[str, int, str]:
    match = re.search(r"(?:Slide|Page)\s+(\d+)", str(chunk.get("loc", "")), re.I)
    return (str(chunk.get("source_ref", "")), int(match.group(1)) if match else 0, str(chunk.get("chunk_id", "")))


def _windows(chunks: list[dict[str, Any]], max_chars: int = 12000) -> list[list[dict[str, Any]]]:
    result: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    size = 0
    for chunk in chunks:
        text_size = len(str(chunk.get("text", "")))
        if current and size + text_size > max_chars:
            result.append(current)
            current, size = [], 0
        current.append(chunk)
        size += text_size
    if current:
        result.append(current)
    return result


def _extract_window(window: list[dict[str, Any]], llm: Any) -> list[TopicRecord]:
    source_file = window[0]["source_ref"]
    body = "\n\n".join(
        f"chunk_id: {item['chunk_id']}\nsource: {item.get('loc', '')}\ntext:\n{item.get('text', '')}"
        for item in window
    )
    response = llm.invoke([SystemMessage(content=_PROMPT), HumanMessage(content=f"source_file: {source_file}\n\n{body}")])
    payload = _parse_json(response.content)
    topics: list[TopicRecord] = []
    valid_chunk_ids = {str(item["chunk_id"]) for item in window}
    for raw_index, raw in enumerate(payload.get("topics") or [], start=1):
        if not isinstance(raw, dict):
            continue
        raw["source_file"] = source_file
        raw["topic_id"] = raw_index
        evidence = [item for item in raw.get("evidence_chunk_ids") or [] if str(item) in valid_chunk_ids]
        raw["evidence_chunk_ids"] = evidence
        if not evidence:
            continue
        topic = validate_topic_enums(TopicRecord.model_validate(raw))
        topics.append(topic)
    return topics


def extract_topics(*, scope: str = "", chunks_path: str | Path = CHUNKS_FILE, llm: Any | None = None) -> list[TopicRecord]:
    load_env()
    path = Path(chunks_path)
    if not path.is_file():
        raise FileNotFoundError(f"chunks 文件不存在: {path}")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            chunk = json.loads(line)
            source = str(chunk.get("source_ref", ""))
            if source and source_in_scope(source, scope):
                grouped[source].append(chunk)
    if llm is None:
        llm = create_apollo_llm(get_access_token())
    records: list[TopicRecord] = []
    for source_file in sorted(grouped):
        source_topics: list[TopicRecord] = []
        for window in _windows(sorted(grouped[source_file], key=_sort_key)):
            source_topics.extend(_extract_window(window, llm))
        for topic_id, topic in enumerate(source_topics, start=1):
            topic.topic_id = topic_id
            records.append(topic)
    return records
