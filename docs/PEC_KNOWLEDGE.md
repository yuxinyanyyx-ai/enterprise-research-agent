# PEC 知识检索

PEC 查询能力通过普通 Agent Tool `search_pec_knowledge` 注册。索引更新属于离线管理操作，不向模型开放写工具。

## 配置

必需的 Apollo 配置：

- `APOLLO_CLIENT_ID`、`APOLLO_CLIENT_SECRET`、`APOLLO_TOKEN_URL`
- `APOLLO_BASE_URL`：OpenAI-compatible LLM、Embedding 和 Rerank base URL
- `APOLLO_MODEL`
- `APOLLO_EMBEDDING_MODEL`
- `APOLLO_RERANK_MODEL`

可选配置：

- `APOLLO_VISION_MODEL`：未配置时使用 `APOLLO_MODEL`
- `APOLLO_RERANK_URL`：可选覆盖；默认使用 `${APOLLO_BASE_URL}/rerank`
- `APOLLO_VERIFY_SSL`：`true`、`false` 或 CA bundle 路径
- `PEC_KNOWLEDGE_ROOT`：默认 `data/pec`
- `PEC_TOPICS_DB`：默认 `<PEC_KNOWLEDGE_ROOT>/index/pec_topics.db`
- `PREVIEW_ON_INDEX`：默认开启；预览失败不阻断文本索引
- `RERANK_MIN_SCORE`：默认 `0.4`

## 建立索引

将 PPTX、DOCX、PDF 或 XLSX 文件放入 `data/pec/sources`，然后执行：

```powershell
python -m src.pec.knowledge.chunk_index update
python -m src.pec.knowledge.chunk_index update --scope PEC1
python -m src.pec.knowledge.chunk_index status
```

索引产物为 `manifest.json`、`chunks.jsonl` 和 `embeddings.npy`。未变化 chunk 会复用已有向量；embedding 模型变化会触发全量重建。更新失败时保留上一份完整快照。

页面预览依赖 PyMuPDF、Pillow，以及可选的 PowerPoint/Word 或 LibreOffice。没有这些系统组件时，文本索引和查询仍可运行。

## 构建 Topics 结构化检索

Topics 是轻量结构化补充，不需要人工审核或额外治理流程。先完成 chunk 索引，再从现有 `chunks.jsonl` 按文件分组调用 Apollo LLM 抽取会议主题、决策和行动项，经过 Pydantic 校验后直接生成 SQLite。

```powershell
& 'C:\BITrusted\work\python.exe' -m src.pec.topics.cli build --scope PEC1
& 'C:\BITrusted\work\python.exe' -m src.pec.topics.cli status
```

不指定 `--scope` 时重建全部 Topics；指定 scope 时只替换该范围，并保留数据库中其他来源的 Topics。构建失败不会替换已有数据库。

Topics 数据库默认位置为 `data/pec/index/pec_topics.db`，可用 `PEC_TOPICS_DB` 覆盖。数据库由 `topics` 表组成，包含来源、标题、背景、决策、行动项、分类和原始证据字段。抽取结果必须引用真实的 `evidence_chunk_ids`，没有明确原文依据的决策或行动项应为空。

单独验证结构化检索：

```powershell
& 'C:\BITrusted\work\python.exe' -m src.pec.search_api.search1 `
	"TENACITY 的中国试验和 CDE 沟通有哪些决策"
```

Agent 查询时会并行执行 chunk 向量检索和 Topics 结构化检索。Topics 数据库不存在或不可用时，会自动降级为 chunk-only，不影响基础检索。