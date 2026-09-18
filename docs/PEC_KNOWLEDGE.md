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
- `APOLLO_PEC_VISION_MODEL`：PEC 页面 Vision 专用模型，优先级高于 `APOLLO_VISION_MODEL`
- `APOLLO_RERANK_URL`：可选覆盖；默认使用 `${APOLLO_BASE_URL}/rerank`
- `APOLLO_VERIFY_SSL`：`true`、`false` 或 CA bundle 路径
- `PEC_KNOWLEDGE_ROOT`：默认 `data/pec`
- `PEC_TOPICS_DB`：默认 `<PEC_KNOWLEDGE_ROOT>/index/pec_topics.db`
- `PREVIEW_ON_INDEX`：默认开启；预览失败不阻断文本索引
- `RERANK_MIN_SCORE`：默认 `0.4`
- `PEC_PPT_VISION_MODE`：`smart`（默认）、`all` 或 `none`
- `PEC_PPT_VISION_MAX_SLIDES`：单个 PPT 最多调用 Vision 的页数，默认 `0` 表示不限制
- `PEC_PPT_VISION_PROMPT_VERSION`：Vision 缓存版本，提示词变化时递增，默认 `1`

## 建立索引

将 PPTX、DOCX、PDF 或 XLSX 文件放入 `data/pec/sources`，然后执行：

```powershell
python -m src.pec.knowledge.chunk_index update
python -m src.pec.knowledge.chunk_index update --scope PEC1
python -m src.pec.knowledge.chunk_index status
```

索引产物为 `manifest.json`、`chunks.jsonl` 和 `embeddings.npy`。未变化 chunk 会复用已有向量；embedding 模型变化会触发全量重建。更新失败时保留上一份完整快照。

页面预览依赖 PyMuPDF、Pillow，以及可选的 PowerPoint/Word 或 LibreOffice。没有这些系统组件时，文本索引和查询仍可运行。

PPT 视觉抽取按整页 PNG 调用 Vision，而不是对页面内每张图片分别调用。`smart` 模式只处理无文本、文本不足或包含图片/组合图形/图表/原生表格的高风险页面；`all` 模式用于高价值资料或离线召回率对照；`none` 模式只保留原生文本和表格抽取。页面结果按源文件摘要、页码、模型和提示词版本缓存，重复索引不会重复调用未变化页面。单页 Vision 失败时保留原生抽取结果。

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