# retriever.py — 三重递进检索：标签过滤 → 相关性判断 → 上下文生成
from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from src.pec.search_api.config import VALID_CATEGORIES
from src.pec.search_api.tagger import TAGGER_SYSTEM_PROMPT


logger = logging.getLogger(__name__)


# ── 工具函数 ──────────────────────────────────────────────────────────

def _parse_json(raw) -> Any:
    """从 LLM 输出中提取第一个 JSON value（object 或 array），兼容 markdown fence 和前后噪声文字。"""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    elif not isinstance(raw, str):
        raw = str(raw)
    raw = raw.strip()
    # strip markdown fence（兼容 ```json / ```JSON / ``` 三种写法）
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
        raw = re.sub(r"\s*```$", "", raw.strip())
    decoder = json.JSONDecoder()
    # 先尝试整体解析
    try:
        obj, _ = decoder.raw_decode(raw)
        return obj
    except json.JSONDecodeError:
        pass
    # 从第一个 { 或 [ 开始逐字符尝试解析，兼容前后噪声文字
    for i, ch in enumerate(raw):
        if ch in "{[":
            try:
                obj, _ = decoder.raw_decode(raw[i:])
                return obj
            except json.JSONDecodeError:
                continue
    raise json.JSONDecodeError("No valid JSON found", raw, 0)


def _as_list(value) -> list[str]:
    """将 LLM 返回的 ta / indication 字段规范化为 list[str]，兼容 null / 单字符串 / 含 None 元素的列表。"""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if v is not None and str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _as_bool01(value) -> bool:
    """兼容 1/0、true/false、'1'/'0'、'true'/'false'，规范化 relevant 字段。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return False


def _as_int(value):
    """尽量把 topic_id 转成 int，失败返回 None。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ── 数据结构 ──────────────────────────────────────────────────────────

@dataclass
class TopicRow:
    """从数据库读取的单条 topic（唯一键为 source_file + topic_id）。"""
    source_file: str
    topic_id: int
    title: str
    background: str
    background_summary: str
    decision: str
    primary_category: str
    secondary_category: Optional[str]
    ta: list[str]
    indication: list[str]


def topic_row_key(row: TopicRow) -> tuple[str, int]:
    return (row.source_file, row.topic_id)


@dataclass
class QueryTags:
    """LLM 对用户 query 打出的标签（复用 tagger SYSTEM_PROMPT，最多两个 category）。"""
    primary_category: str
    secondary_category: Optional[str]
    ta: list[str]
    indication: list[str]


# ── 第一层：为 Query 打标签（复用 TAGGER_SYSTEM_PROMPT）────────────────

_QUERY_TAG_USER_TEMPLATE = """请为以下用户的检索问题打标签，以便在 PEC 会议数据库中精准检索：

用户问题：{query}

请根据问题的核心业务意图，判断最匹配的 primary_category、secondary_category、ta、indication。
返回 JSON，字段同 topic 打标签：primary_category, secondary_category, ta, indication, confidence, reason。
"""


def tag_query(query: str, llm: ChatOpenAI) -> QueryTags:
    """
    第一层：直接复用 tagger.SYSTEM_PROMPT 为 query 打标签。
    只取 primary_category / secondary_category / ta / indication 四个字段用于检索。
    confidence / reason 字段忽略。
    """
    messages = [
        SystemMessage(content=TAGGER_SYSTEM_PROMPT),
        HumanMessage(content=_QUERY_TAG_USER_TEMPLATE.format(query=query)),
    ]
    response = llm.invoke(messages)
    data = _parse_json(response.content)
    tags = QueryTags(
        primary_category=str(data.get("primary_category") or "").strip(),
        secondary_category=(str(data.get("secondary_category")).strip()
                            if data.get("secondary_category") else None),
        ta=_as_list(data.get("ta")),
        indication=_as_list(data.get("indication")),
    )
    logger.info(
        "第一层 Query 标签: primary=%s, secondary=%s, ta=%s, indication=%s",
        tags.primary_category, tags.secondary_category, tags.ta, tags.indication,
    )
    return tags


# ── 第二层：SQL 过滤 ──────────────────────────────────────────────────

def _candidate_select_sql(conn: sqlite3.Connection) -> str:
    """兼容有无 background_summary 列的 topics 表。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(topics)").fetchall()}
    bg_col = "background_summary" if "background_summary" in cols else "background AS background_summary"
    return (
        "SELECT source_file, topic_id, title, background, "
        f"{bg_col}, decision, primary_category, secondary_category, ta, indication "
        "FROM topics"
    )


def fetch_candidates(
    conn: sqlite3.Connection,
    tags: QueryTags,
) -> list[TopicRow]:
    """
    第二层：用 SQL 真正过滤，不做全表扫描。

    过滤规则：
    - category（必须）：primary_category 或 secondary_category 命中 query 的两个 category 之一
    - ta（可选）：非空时追加 AND，ta 列 LIKE 匹配任一值
    - indication（可选）：非空时追加 AND，indication 列 LIKE 匹配任一值

    query 无 category（两者均为空）时返回全部，交给第三层判断。

    注意：SELECT 不取 raw_source_text，减少 I/O；
          raw_source_text 在第三层确认相关后按 (source_file, topic_id) 查询。
    """
    _select = _candidate_select_sql(conn)
    categories = [
        c for c in (tags.primary_category, tags.secondary_category)
        if c and c in VALID_CATEGORIES
    ]

    # 无合法 category（含 Unclear / 空）→ 不做第二层过滤，全部交给第三层
    if not categories:
        rows = conn.execute(_select).fetchall()
        logger.info("第二层 SQL 过滤：无合法 category，返回全部 %d 条候选", len(rows))
        return [_row_to_topic(r) for r in rows]

    # ── 动态构建 SQL ──────────────────────────────────────────────
    # category 条件（必须）
    # category 条件：primary 和 secondary 用 OR 连接
    # 即：topic 的 primary_category 或 secondary_category 命中 query 的任意一个 category
    where_clauses: list[str] = []
    params: list = []

    if categories:
        cat_placeholders = ",".join("?" * len(categories))
        where_clauses.append(
            f"(primary_category IN ({cat_placeholders}) "
            f"OR secondary_category IN ({cat_placeholders}))"
        )
        params += categories + categories

    # ta 条件（可选，OR 连接多个 LIKE）
    # TODO: 以后启用 ta 过滤时取消注释
    # if tags.ta:
    #     ta_likes = " OR ".join("ta LIKE ?" for _ in tags.ta)
    #     where_clauses.append(f"({ta_likes})")
    #     params += [f'%"{v}"%' for v in tags.ta]

    # indication 条件（可选，OR 连接多个 LIKE）
    # TODO: 以后启用 indication 过滤时取消注释
    # if tags.indication:
    #     ind_likes = " OR ".join("indication LIKE ?" for _ in tags.indication)
    #     where_clauses.append(f"({ind_likes})")
    #     params += [f'%"{v}"%' for v in tags.indication]

    sql = _select + " WHERE " + " AND ".join(where_clauses)
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        rows = conn.execute(_select).fetchall()
        logger.info(
            "第二层 SQL 过滤：category=%s 无命中，回退全表 %d 条候选",
            categories,
            len(rows),
        )
    else:
        logger.info("第二层 SQL 过滤：命中 %d 条候选", len(rows))
    return [_row_to_topic(r) for r in rows]


def _row_to_topic(r) -> TopicRow:
    """将 sqlite3.Row 转为 TopicRow（raw_source_text 暂为空，第三层按需补填）。"""
    background = r["background"] or ""
    background_summary = r["background_summary"] or background
    return TopicRow(
        source_file=r["source_file"] or "",
        topic_id=r["topic_id"],
        title=r["title"],
        background=background,
        background_summary=background_summary,
        decision=r["decision"] or "",
        primary_category=r["primary_category"] or "",
        secondary_category=r["secondary_category"],
        ta=json.loads(r["ta"] or "[]"),
        indication=json.loads(r["indication"] or "[]"),
    )


# ── 第三层 A：批量相关性判断（并发分批 LLM 调用）────────────────────

_RELEVANCE_SYSTEM = """你是一名 PEC 会议内容专家。
给定用户的检索问题和若干 PEC topic 摘要，逐条独立判断每个 topic 是否与问题高度相关。

【relevant=1 的条件】
topic 的核心主题与用户问题直接对应，且 topic 中有实质内容（决策、考量、风险、建议、时机判断）能回答用户的问题。
"核心主题对应"的含义：topic 本身就在讨论用户问题所问的那件事，而不只是同一药物、同一适应症或背景相似。

【必须判 relevant=0】
以下任一情形直接判 0，无论是否含有相关关键词：
- topic 核心是另一件事（如项目终止、入组进展、PI 选择、时间线更新），用户问题关注的内容仅一笔带过
- topic 只陈述已完成的事后结果，没有涉及决策过程或考量因素
- 需要推断或间接联系才能与用户问题挂钩

规则：
- 逐条独立判断，宁可少召回，不召回弱相关
- 相关性来自 topic 的实质内容，不来自关键词是否出现

严格按以下 JSON 数组格式返回，只返回 JSON，不要任何额外文字：
[{"source_file": <str>, "topic_id": <int>, "relevant": <1或0>}, ...]

每个输入的 source_file + topic_id 组合必须在返回数组中出现。"""

_RELEVANCE_MAX_WORKERS = 3  # 并发线程数：candidates 均分为 N 份同时发给 LLM


def _judge_one_batch(
    query: str,
    batch: list[TopicRow],
    llm: ChatOpenAI,
    batch_idx: int,
) -> set[tuple[str, int]]:
    """对单批候选做一次 LLM 相关性判断，返回该批中 relevant=1 的 (source_file, topic_id) 集合。"""
    topics_text = "\n\n".join(
        f"source_file: {t.source_file}\n"
        f"topic_id: {t.topic_id}\n"
        f"标题: {t.title}\n"
        f"Background: {t.background}\n"
        f"Decision: {t.decision}"
        for t in batch
    )
    user_msg = (
        f"用户问题：{query}\n\n"
        f"以下是候选 topics：\n\n{topics_text}\n\n"
        "请对每个 topic 判断是否与用户问题相关，返回 JSON 数组。"
        "每条必须包含 source_file、topic_id、relevant 三个字段。"
    )
    messages = [
        SystemMessage(content=_RELEVANCE_SYSTEM),
        HumanMessage(content=user_msg),
    ]
    try:
        response = llm.invoke(messages)
        results = _parse_json(response.content)
    except Exception as e:
        logger.warning("第三层批次 %d 判断失败，跳过该批：%s", batch_idx, e)
        return set()

    if not isinstance(results, list):
        logger.warning("第三层批次 %d 返回格式非数组，跳过", batch_idx)
        return set()

    relevant_keys: set[tuple[str, int]] = set()
    for item in results:
        if not isinstance(item, dict):
            continue
        topic_id = _as_int(item.get("topic_id"))
        source_file = str(item.get("source_file") or "").strip()
        if topic_id is None or not source_file:
            continue
        if _as_bool01(item.get("relevant")):
            relevant_keys.add((source_file, topic_id))

    logger.info("第三层批次 %d：%d 条 → %d 条相关", batch_idx, len(batch), len(relevant_keys))
    return relevant_keys


def batch_judge_relevance(
    query: str,
    candidates: list[TopicRow],
    llm: ChatOpenAI,
    max_workers: int = _RELEVANCE_MAX_WORKERS,
) -> list[TopicRow]:
    """
    第三层 A：将 candidates 均分为 max_workers 份，并发调用 LLM 判断相关性。

    - candidates 按 max_workers 均分（不足时实际批数 = len(candidates)）
    - 每批条数 = ceil(len(candidates) / max_workers)，无上限
    - 合并所有批次结果，返回 relevant=1 的 TopicRow 列表（保持原顺序）
    - 任意批次异常 fail-close，跳过该批，不影响其他批次
    """
    if not candidates:
        return []

    n = len(candidates)
    workers = min(max_workers, n)
    batch_size = math.ceil(n / workers)
    batches = [candidates[i: i + batch_size] for i in range(0, n, batch_size)]

    logger.info(
        "第三层：%d 条候选均分为 %d 批（每批约 %d 条），并发 %d 线程",
        n, len(batches), batch_size, workers,
    )

    relevant_keys: set[tuple[str, int]] = set()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_judge_one_batch, query, batch, llm, idx): idx
            for idx, batch in enumerate(batches)
        }
        for future in as_completed(futures):
            relevant_keys |= future.result()

    logger.info(
        "第三层汇总：%d 条候选 → %d 条相关，relevant_keys=%s",
        n, len(relevant_keys), relevant_keys,
    )
    return [t for t in candidates if topic_row_key(t) in relevant_keys]
