# 统一链路追踪与模型用量更新

## 2026-10-26：修复后真实 Apollo 完整回答验收通过

- 用户重新授权一个受控只读请求，上限为 4 次业务调用和一次认证，关闭全部
  重试；仅验收进程显式使用 32K 窗口，不改生产默认或增加证据裁剪。
- 实际 LLM 2 次、Embedding 1 次、Rerank 1 次，认证 1 次；全部业务调用
  HTTP 200，零重试，约 18.5 秒。检索返回 2 条完整、未截断证据，模型生成
  **1,136 字符最终答案**。最终请求保留检索 Schema、`tool_choice="none"`，
  业务系统消息位于连续前缀，未再次执行检索。
- 9 个 Span 同属一个 Trace，父子关系可解析，4 个实际模型请求各有一个
  模型 Span。LLM 实际输入 7,907、输出 996 Token；Embedding 输入/总量 9，
  输出不适用；Rerank 用量缺失。没有可信价格，费用仍未知，不按零计价。
- 首版验收脚本错误比较 Embedding 的通用 `completion_tokens=0` 与不适用
  输出量，导致检查误报。已保留初始 JSON/Markdown，仅离线核对既有响应的
  输入和总量，未追加调用、未改写 API 观察或 Span；最终 19 项检查通过。
- 新报告和业务答案：
  [中文报告](../outputs/trace-validation/20261026T011559556156Z/report.md)、
  [JSON 报告](../outputs/trace-validation/20261026T011559556156Z/report.json)、
  [实际最终答案](../outputs/trace-validation/20261026T011559556156Z/answer.md)。
  答案是单独本地业务输出，不写入 Trace；此前两次 `incomplete` 报告未覆盖。
- 当前累计业务模型请求为 14 次，最新授权已用完，不自动继续调用。完整答案
  链路已在上述限定配置下通过，不等于默认 16K 部署已验收或正式 RAG 质量达标。
  答案末尾对“其他项目”的概括只能限定到本次命中范围；未做人审或正式评分。
- 索引未修改。发布目录和 Manifest 内部版本标识不一致仍如实记录；没有
  为此次联调改索引、恢复暂停的 Benchmark 或开展答案优化。

## 2026-10-26：最终回答请求协议修复（离线）

- 优先查阅 LangChain Agent 工厂和 Anthropic 官方工具示例，确认应区分
  “无工具可用”“保留工具调用历史”“禁止再次选择工具”，并保持系统消息
  连续前缀。参考链接和完整约束见[上下文预算说明](AGENT_CONTEXT_BUDGET.md)。
- 使用真实 `ChatOpenAI` 序列化和本地 HTTP 替身复现：最终请求含 `tools=[]`，
  业务快照被放在工具消息后；无工具普通对话也发送空工具列表。两个新增
  协议测试修复前均失败。替身只用于离线程序回归，不生成真实模型成绩。
- 修复消息组装：业务快照移到系统前缀，当前问题、工具调用 ID、结果顺序和
  完整安全证据不变。没有可用工具但保留当前轮已注册工具历史时，绑定历史
  Schema 并设置 `tool_choice="none"`；无工具历史时直接调用未绑定模型。
  仍有可用工具的动态绑定策略不变，不引入未经 Apollo 验证的扩展参数。
- 历史 Schema 纳入同一预算；执行层可用性、授权、重复和轮次保护不变。
  测试仍强行模拟模型忽略 `none`，确认成功 PEC 检索只执行一次。
- 默认 16K 窗口、输出预留和安全预留不变，不自动扩容，也不新增预算层
  证据裁剪。超限提示明确没有生成答案，显示可用的本地估算值和输入预算；
  拒绝 Span 标为 `rejected`，估算不是实际用量。
- Apollo 统一入口补充 HTTP 错误状态和固定白名单错误码；未知错误码及
  响应正文不记录。400 不重试，用量和价格缺失不按零处理。
- 修改实现：`src/agent/context_budget.py`、`src/agent/react_nodes.py`、
  `src/llm/apollo.py`、`src/observability/tracing.py`。新增
  `tests/test_agent_request_protocol.py`，适配直接相关测试替身、上下文顺序断言
  和固定 Agent Eval YAML；保留原有完整性、Token 上限和执行拒绝断言。
- 最终合并运行 16 个直接相关测试模块，**282 项通过**，包含固定 Agent Eval、
  多轮会话、Skill、记忆、PEC 工具、Apollo 和 Trace 回归。变更生产 Python
  文件语法编译通过，定向 `git diff --check` 通过。本轮新增的请求协议和
  错误诊断测试全部离线执行，没有消费额外 Apollo 调用额度。
- 已确认的是请求兼容性隐患，**尚未确认此前 Apollo 400 的唯一根因，也没有
  宣称真实最终答案已生成**。累计 10 次授权已用完，本轮不追加真实请求，
  不修改既有两次 `incomplete` 实测结果。真实复验仍需新授权，并显式确认
  部署窗口，不能让一次性的 32K 验证自动变成生产默认配置。

## 2026-10-26：真实 Apollo 验收

- 已按用户授权执行真实接口探针与只读 PEC Agent 请求。原授权最多 8 次，
  首次使用 6 次；发现上下文拒绝后，用户批准累计最多 10 次，第二次使用
  4 次。累计 LLM 4 次、Embedding 3 次、Rerank 3 次，零重试；认证另计 2 次。
- LLM、Embedding、Rerank 接口均连通。真实 Span 的父子关系、请求关联、
  实际用量解析和缺失用量标记通过核验。独立 Topic 分支没有执行。
- 首次 Agent 检索成功，返回 5 条证据，但后续触发
  `context_budget_exceeded`；返回的是拒绝提示，不是答案。
- 用户批准仅在第二次验收进程使用 32K 上下文窗口，不改变生产默认或裁剪
  证据。第二次检索仍成功，但最终模型请求返回 `OpenAIInvalidRequestError`。
  失败 Span 正确记录了错误、耗时和缺失用量。具体服务端原因尚未确认。
- **当前结论：接口和追踪采集通过真实验证，完整 Agent 答案链路未通过。**
  两次运行均标记 `incomplete`，保留检索成功结果与失败记录，不报告正式
  RAG 成绩；已达到授权调用上限，没有继续发送请求。
- 已知 LLM 用量为输入 6,506、输出 153 Token，只覆盖 3 次成功响应，另有
  1 次失败请求用量未知；Embedding 输入 38 Token；3 次 Rerank 用量缺失。
  不把未知计为零，不跨模型计价。没有可信价格，费用保持缺失。
- 脱敏汇总与每次实测报告：
  [真实验收汇总](../outputs/trace-validation/SUMMARY_20261026.md)、
  [首次报告](../outputs/trace-validation/20261026T004302728848Z/report.md)、
  [第二次报告](../outputs/trace-validation/20261026T004645571116Z/report.md)。
  各运行目录同时保存 `report.json` 与原始脱敏 Span JSONL。
- 本轮离线回归 **131 项通过**；实测报告调用数、Span 数和父子关系的离线
  核验通过。没有为验证报告而追加真实请求。

## 2026-10-26：移除独立 Topic 检索分支

- 按用户要求，Agent PEC 查询不再调用独立 SQLite Topic 检索，也不再创建
  Chunk/Topic 并行线程池。直接调用已发布索引的检索入口，保留统一索引中的
  Topic、原始 Chunk、融合、重排及证据关联实现；不删除或重建索引。
- 这次是明确的检索行为调整：不再有独立 Topic 标签推断、相关性判断调用，
  也不再因为缺少独立数据库而产生部分可用提示。旧版离线构建和搜索工具
  保留，但不参与 Agent 请求。
- 为兼容已有调用方，保留 `topics` 响应字段，明确标记 `enabled: false`；
  统一索引查询成功时 `available_sources` 为 `["chunks"]`，`partial` 为
  `false`。查询失败时返回明确失败结果，并只记录异常类型，不泄露异常正文。
- 直接执行检索后不再跨越该线程池边界，因此原先此处丢失 Trace Context
  的问题随独立分支一并消除。并发请求测试验证请求、工具、检索、Embedding
  和 Rerank 的 Trace ID 与父子关系，以及失败状态、用量和敏感信息隔离。
- 修改文件：`src/pec/knowledge_service.py`、`tests/test_pec_knowledge_service.py`、
  `tests/test_pec_tool.py`、`tests/test_tracing.py`、`skills/knowledge/SKILL.md`、
  `docs/PEC_KNOWLEDGE.md` 及本更新文档和后续计划。
- 离线回归：Trace、PEC Service/Tool、Agent 审计、Apollo、固定 Agent Eval
  共 **128 项通过**。变更 Python 文件语法编译通过，`git diff --check` 通过。
- 本次没有调用真实 Apollo，也没有运行或改动 RAG Benchmark；不能将离线
  测试作为真实模型质量或费用测量。下一步仍是明确预算后的真实链路验收。

## 范围

本次新增与请求关联的链路追踪和用量统计，不改变 Agent、DMF 或 PEC 的
业务流程。追踪数据以 JSONL Span 写入
`logs/traces/trace-<pid>.jsonl`；单个文件达到 10 MiB 后轮转，并保留 5 个
备份文件。追踪日志写入失败时会记录警告，但不会替代或中断原有业务结果。

## Trace 数据模型与上下文传播

每个 Span 记录以下信息：

- `trace_id`、`request_id`、`span_id` 和 `parent_span_id`
- 操作名称、组件、可选模型名称、开始时间、耗时和执行状态
- 错误类型，以及 Apollo 统一重试入口管理的重试次数
- 可选的输入、输出和总 Token 数，以及来源（`actual`、`estimated` 或
  `missing`）
- 可选的成本、币种、成本来源和经过白名单筛选的元数据

活动 Trace 上下文使用 `ContextVar` 传播。Agent HTTP 请求创建根 Span；
Agent 模型调用、工具调用、PEC 检索、Embedding 和 Rerank 创建子 Span。
并行工具执行会显式复制当前上下文到工作线程，避免并发请求之间串联
父 Span。Agent PEC 查询现直接调用统一索引，不再创建独立 Topic 检索线程。
旧版离线 PEC 搜索批次仍保留上下文复制。Agent 会话恢复通过恢复 Span
追踪，不修改 LangGraph 检查点状态。

追踪元数据仅允许写入少量计数器和标识符。Prompt、文档内容、凭证以及任意
工具参数不会被序列化。

## 用量与成本规则

- 只有 Apollo 响应中存在可识别的用量字段时，才在模型/API 调用 Span 中
  记录一次。
- 上下文预算中的 Token 数标记为 `estimated`，不与实际模型用量相加。
- 缺少用量时，数值保持 `null`、来源标记为 `missing`，不会伪装成零。
- Embedding 和 Rerank 只记录 API 响应实际提供的 Token 用量，不会把非
  Token 计费单位误报为 Token。
- 价格规则需要显式配置并精确匹配模型。模型、必要 Token 数或价格规则
  未知时，成本保持缺失。实际价格和估算价格会分别标记。目前没有配置
  生产环境价格规则。

Apollo 客户端默认关闭 OpenAI SDK 内部重试。经过包装的 LLM 调用由
`invoke_with_gateway_retries` 统一负责重试，因此 `retry_count` 只反映应用
层发起的重试。明确受调用预算约束的入口保持关闭重试；其他包装调用使用
显式重试预算，以避免不可见的 SDK 嵌套重试。

## 记录格式示例

以下仅用于展示数据结构，不是生产环境的实测记录：

```json
{
  "trace_id": "<request-trace-id>",
  "request_id": "<request-id>",
  "span_id": "<model-span-id>",
  "parent_span_id": "<agent-span-id>",
  "operation_name": "llm.invoke",
  "component": "apollo.llm",
  "model_name": "<configured-model>",
  "start_time": "<UTC timestamp>",
  "duration_ms": null,
  "status": "success",
  "error_type": null,
  "retry_count": 0,
  "input_tokens": null,
  "output_tokens": null,
  "total_tokens": null,
  "token_usage_source": "missing",
  "cost_amount": null,
  "cost_currency": null,
  "cost_source": "missing",
  "metadata": {}
}
```

## 接入位置

- `src/observability/`：共享 Span 数据模型、上下文传播、安全元数据筛选、
  JSONL 写入和按模型计价。
- `src/llm/apollo.py`：统一 LLM 重试与用量采集，并追踪 Embedding、
  Rerank 和图像描述 API 调用。
- `src/agent/` 与 `src/web/routes/agent.py`：请求及恢复根 Span、模型和工具
  Span、上下文预算估算，以及与现有无业务负载审计记录的兼容。
- `src/pec/`：Chunk 检索、搜索 API、Topic/文档处理、Embedding 和 Rerank
  调用链路。
- `src/services/slide_generator_services.py` 与
  `src/tools/project_slide_tool/lpt.py`：由 Apollo 驱动的幻灯片处理流程。
- `tests/agent_eval/`：分别汇总实际、估算和缺失用量；模型调用失败时不再
  被误报为输出 Token 数为零。

## 验证结果

重试归一后的定向回归结果：

- Apollo、Trace、审计、Agent Eval 报告和 PEC Benchmark 测试：
  **56 项通过**。
- Agent 图与组合、工具与上下文、PEC Knowledge/Topic/工具、Project Slide
  测试：**131 项通过，1 项跳过**。
- `git diff --check` 通过；Git 只提示 Windows 工作区文件的换行符规范化。

本次实现早期，`compileall` 通过；选定的离线回归集合报告
**161 项通过，1 项跳过**。另有一组既有 PEC chunk-index 测试与当前接口
不匹配（测试引用了不存在的 `CHUNKS_FILE`、`_chunks_digest`，以及不匹配的
monkeypatch 函数签名）；本次没有修改这些测试。

本次没有发起真实 Apollo 请求，也没有配置模型价格，因此不报告生产环境
成本或真实模型调用结果。

## 兼容性与限制

- 追踪能力以附加方式接入，不改变现有 Agent 或 PEC 响应结构。
- 现有审计事件仍不包含业务负载，并通过 Trace、Request 和 Span 标识符
  关联。
- 尚未实现 Dashboard、保留期管理或访问控制界面。
- 当前未发现生产环境中的异步或流式 LLM 调用；今后新增此类调用时必须
  显式接入追踪。
- Trace 数据写入本地 JSONL。部署环境中的保留策略、访问控制和集中导出
  仍需由运维侧确定。
