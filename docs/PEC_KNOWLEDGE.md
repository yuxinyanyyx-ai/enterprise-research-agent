# PEC 知识检索

PEC 工具通过默认统一注册入口接入 Agent：`search_pec_knowledge` 用于查询，`get_meeting_index_status` 用于查看索引状态，`parse_pptx_structure` 用于提取当前会话已上传 PPTX 的逐页文字或图片文件的视觉识别结果，`update_meeting_index` 用于更新 Chunk 和向量索引。索引更新标记为 `LOCAL_WRITE`，其余工具标记为 `READ_ONLY`。Topic 构建仍通过离线 CLI 执行。

## Knowledge Skill

`skills/catalog.json` 列出可用 Skill。Agent 启动请求时按 Skill frontmatter 中的触发词预选指引；其他适用指引可由 Agent 通过 `load_skill` 在本轮按需载入。Skill 正文作为系统上下文注入；声明的工具必须同时存在于 Agent 注册表。修改 Skill 或 catalog 后需重启 Agent 进程；部署时应将 `skills` 与 `src` 一起提供。

Agent 根据用户问题自主判断是否需要 PEC 检索，加载 Knowledge Skill 后调用 `search_pec_knowledge(query)` 获取可用的 Topic/Chunk 证据，并基于来源文件及提供的位置生成回答。已有信息足够或问题与 PEC 无关时，不必调用检索。Skill 加载只提供操作指引，不授予额外工具权限；原检索工具接口和执行限制保持不变。

业务验收使用现有模型配置和索引，核对真实工具调用、实际返回证据与最终回答中的来源。Topic 和 Chunk 不必同时命中；后端不可用或证据不足时应明确说明，不能据此宣称完整验收通过。真实验证会产生模型及检索服务调用费用，业务记录仅存受控本地目录，不上传公开仓库。自动化测试用于检查提示接入和兼容性，不代替真实模型验收。

## 配置

必需的 Apollo 配置：

- `APOLLO_CLIENT_ID`、`APOLLO_CLIENT_SECRET`、`APOLLO_TOKEN_URL`
- `APOLLO_BASE_URL`：OpenAI-compatible LLM、Embedding 和 Rerank base URL
- `APOLLO_MODEL`
- `APOLLO_EMBEDDING_MODEL`
- `APOLLO_RERANK_MODEL`

可选配置：

- `APOLLO_VISION_MODEL`：仅用于 Agent 会话中单独上传的 PNG、JPG、JPEG 图片；未配置时使用 `APOLLO_MODEL`
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

页面预览依赖 PyMuPDF、Pillow，以及可选的 PowerPoint/Word 或 LibreOffice。预览图仅供人工查看，不会送入视觉模型；缺少这些系统组件时，文本索引和查询仍可运行。

PEC 知识库 `/knowledge` 仅接受 PPTX、DOCX、PDF、XLSX。文档正文和原生表格按上述解析器入库；不对整个页面、内嵌图片、扫描页、图表或流程图调用 Vision/OCR。独立图片文件不进入知识库索引。

Agent 会话上传 PNG、JPG、JPEG 时，图片本身会单独调用 Apollo Vision，提取可读文字及有业务含义的视觉关系，并将结果保存为该会话的 Markdown 解析产物。该调用只发生在图片文件上传解析时，不会因 PPTX/PDF 页面含图、预览渲染或更新知识库索引而触发。图片解析失败会明确返回错误，不会伪装为成功解析。

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