# 修改记录

## 2026-08-21：文档上传到 DMF 查询闭环

### 已完成

- 新增文档产物、Flow 提取条件和用户决策 Schema；原始文件与完整 Markdown 不进入 LangGraph State。
- 新增可供 CLI 和后续 FastAPI 复用的 `DocumentDMFService`，统一编排本地文本落盘、MinerU 解析、Apollo Studio Flow 提取和确认后的 DMF 查询。
- Markdown 仅允许从受控结果目录读取，Flow 工具参数由 State 注入，不向模型暴露文档正文或文件路径参数。
- 新增确定性文档 Graph 分支：文档分析只展示提取条件；文档查询在执行 DMF 前通过 interrupt 强制确认，支持修改或拒绝。
- 活动文档会进入意图上下文，避免模型在文档已上传后继续要求用户提供文件。
- CLI 新增 `/file "文件路径"`、`/document` 和 `/clear-file`，支持 PDF、Word、图片、Markdown 和文本文件。
- CLI 可展示 Flow 候选，并以确认、逐项修改或拒绝的方式恢复同一 LangGraph 会话。
- 修正集中配置对仓库根 `.env` 的读取，同时保持既有静态资源和存储默认目录不变。
- 先前的动态 Flow 工具仍作为通用扩展能力保留；关键文档查询顺序现由确定性 Graph 分支控制。

### 验证记录

- 文档服务、可信路径和确认后查询边界：4 项通过。
- `/file` 路径与确认/编辑/拒绝交互：4 项通过。
- 文档 Graph 提取、确认、编辑、拒绝和单次查询：4 项通过。
- 活动文档意图、文档 Graph 与既有工具图组合验证：17 项通过。
- 文档闭环及现有动态工具、审批、DMF、Excel 核心回归：49 项通过。
- VS Code/Pylance 对本次修改文件未报告错误。

## 2026-08-21：Apollo Studio Flow 接入 Agent

### 已完成

- 复用 `src/Apollo/workflow_client.py` 的 blocking Workflow API 调用，不在 Agent 工具层重复实现 HTTP 请求。
- 新增 `extract_document_dmf_params` 只读工具，将 Markdown 文档正文交给 Apollo Studio Flow，并返回 `data.outputs.structured_output`。
- 将 Flow 工具注册到通用工具上下文，`document_review` 和 `dmf_document_compare` 意图可通过现有动态工具循环选择它。
- 更新通用工具提示词，限定只有文档分析或提取 DMF 条件时调用 Flow，并禁止把文件路径、任务 ID 或文件名冒充 Markdown 正文。
- 增加 mock HTTP 客户端测试、工具委托测试、注册可见性测试和 LangGraph 端到端调用测试；测试不访问真实 Apollo Studio。

### 验证记录

- Flow 客户端、工具与注册表聚焦测试：5 项通过。
- Flow 接入后的既有 Agent 图与工具循环回归：9 项通过。
- Flow 从动态选择到结构化结果进入工具产物的端到端测试：9 项通过。

## 2026-08-20：Agent 动态工具调用

### 目标

- 保留现有 DMF 固定查询与 `result / summary / analysis` 输出契约。
- 为其他需求及 DMF 查询后的操作增加可扩展动态工具调用。
- 支持工具风险分级、多工具编排，以及写入工具执行前的暂停确认。

### 已完成

- 新增统一工具注册中心，工具可声明适用场景、风险等级和并行安全性。
- 将 `search_dmf` 注册为通用只读工具。
- 将 Excel 导出包装为 DMF 查询后处理工具；真实查询结果由执行器注入，不作为模型参数。
- 修正 Excel 明细页表头与实际七列数据不一致的问题。
- Excel 导出支持指定输出目录和文件名，并拒绝文件名中的目录穿越。
- 补充源码实际使用的 `langchain-openai` 和 `openpyxl` 依赖。
- 扩展 Agent State，加入工具场景、循环次数、重复调用保护和工具产物字段。
- 新增共享工具执行器：支持动态 `bind_tools`、可信 State 参数注入、统一错误消息和并行安全工具的同轮执行。
- 增加最多 8 轮限制，并在连续重复同名同参数调用时停止。
- 写入类工具通过 LangGraph `interrupt` 返回工具、参数和风险，等待批准的 call id。
- 将现有图调整为混合架构：DMF 保留固定查询，查询成功后进入后处理工具循环；其他意图进入通用工具循环。
- DMF 最终回答保持原有内容，并在成功导出时附加文件路径。
- 被拒绝或失败的后处理调用也会作为结构化事件保留，DMF 最终答案会如实附加导出状态。
- CLI 使用内存 checkpointer 和会话 `thread_id`，可展示风险并在用户批准或拒绝后恢复执行。
- 新增 `docs/ADDING_AGENT_TOOLS.md`，说明后续工具的定义、注册、风险分级、State 注入、并行规则和测试要求。
- 达到工具循环上限时，通用回答和 DMF 确定性回答都会给出明确停止原因。
- 增加跨轮 DMF 后处理：识别“导出 Excel/exel/xlsx”等请求，复用 checkpoint 中的活动 `dmf_results`，不重复查询。
- 后处理请求使用独立路由和最终节点，只返回导出状态，不重复打印完整 DMF 表格。
- 新增两轮 checkpoint 测试，验证“查询后导出”只查询一次，并兼容 `exel` 常见拼写。
- 增加澄清问题完整性校验：模型返回残句时按任务类型替换为完整、确定性的澄清问题，并在最终输出节点再次兜底。

### 验证记录

- 使用 Conda 环境 `C:\BITrusted\work`（Python 3.11.15）执行测试。
- `tests/test_tool_registry.py` 与 `tests/test_excel_exporter.py`：5 项通过。
- `tests/test_agent_tool_loop.py`：5 项通过，覆盖只读执行、State 注入、同轮并行、未知工具和重复调用保护。
- 主图接线后的聚焦回归：15 项通过。
- `tests/test_tool_approval.py`：3 项通过，确认批准前不执行、拒绝不落盘、混合批次整体等待。
- 动态工具框架与现有 DMF 核心契约最终聚焦测试：27 项通过。
- 完整图测试覆盖通用 Agent 动态调用 `search_dmf`，以及固定 DMF 查询后导出 Excel 的暂停、批准、拒绝和最终答案拼装。
- 排除仓库两个既有错误导入文件后，其余测试：27 项通过。
- VS Code/Pylance 对本次修改的 Python 文件未报告诊断错误；`git diff --check` 未发现空白错误。
- 跨轮后处理修复后的核心回归：30 项通过；同一 `thread_id` 下“查询 Ibuprofen → 导出 exel”只执行一次查询并进入导出审批。
- 排除两个既有错误导入文件后的仓库回归：30 项通过。
- 澄清文案完整性修复后的核心回归：34 项通过；覆盖意图模型返回“您提到要查询”等残句时的任务级兜底。

### 已知的既有测试阻断

完整 `pytest` 在收集阶段被以下旧测试阻断，本次未修改这些与动态工具无关的文件：

- `tests/captcha_ocr_test.py` 从顶层导入不存在的 `captcha_client`，应改为当前包路径。
- `tests/test_mineru_client.py` 从第三方 `config` 包导入 `Settings`，与当前 `src.settings` 和 MinerU 实现不一致。

### 兼容性说明

- `export_multi_query_result(result)` 原调用方式保持有效。
- 现有固定 DMF 查询节点暂不改为自由工具推理，避免改变真实数据查询和输出行为。