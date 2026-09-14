# 2026-09-03 Agent Watchlist 集成

- 将团队共享 Watchlist 接入现有 LangGraph，新增单一确定性 `manage_watchlist` 节点，支持添加、删除、列表、立即检查、事件列表和事件确认。
- LLM 只提取结构化意图；目标解析、安全校验、服务调用、幂等键和回答格式由确定性代码负责。
- 单项操作直接执行；v1 阻断批量、清空、批量确认和外发请求，未来开放时必须接入 `interrupt/resume` 确认。
- Web 与 CLI 为每条消息注入请求 ID，立即检查使用稳定幂等键；API 与 Agent 共用 Watchlist Service façade。
- Agent Eval 增加 Watchlist fake 边界和 5 个 YAML Cases，核心节点保持真实；新增真实 SQLite façade、恢复、事件归属和重复确认集成测试。

# 2026-09-03 可扩展 Agent Eval

- 新增严格 YAML + Pydantic 场景合同、稳定 loader、精确 Case 筛选和 pytest markers。
- 新增 deterministic/live 共用 Runner 与 Assertions；真实执行 LangGraph、意图、路由、DMF、文档、导出和回答节点。
- 仅替换 LLM、DMF 服务、文档 extractor/searcher 和导出文件系统；deterministic 模式额外阻断 HTTP。
- 首批 6 个场景覆盖 DMF 查询、跨轮结果复用、文档确认、导出成功/失败和普通交流。
- 新增调用次数、节点轨迹、interrupt、state、关键事实、禁止项、Markdown 和 artifact 断言。
- Live LLM 通过同一 Case、Runner 和 Assertions 执行，默认 skip；报告不保存调用 payload 并脱敏认证信息、Cookie 和 captcha。
- 框架测试通过 15 项，live 默认跳过 6 个场景；既有 Agent 回归保持通过。

# 2026-09-03 DMF Watchlist

- 新增 `valid_date_expired` 有效日期过期提醒，按台湾当地日期判断，到期当天仍有效。
- 支持公历和民国年常见日期格式；不可解析日期不误报并保存 run warning。
- 首次发现已过期立即提醒，同一过期周期按规范化有效日期去重，恢复有效后允许新周期提醒。
- 新增过期提醒游标、周期版本和 run warnings，迁移版本为 `c42f81a6d903`。
- 新增按单个稳定 DMF 编号管理的团队共享关注清单。
- 新增首次观察、连续两次未返回、重新出现和字段变化状态机。
- 新增 Watchlist CRUD、手动执行、运行历史、事件列表和幂等确认 API。
- 新增独立定时 worker、数据库执行锁和运行幂等键。
- 新增三张 Watchlist 表及 Alembic 迁移 `7b13d8a94f21`。
- 历史结果新增本次查询的 `snapshot_id`，与全局 baseline ID 分离。

## 2026-09-03：DMF 历史快照与变更检测 P0

### 已完成

- 新增 SQLAlchemy + Alembic 历史数据库层，支持 SQLite 并兼容 PostgreSQL。
- 每个 Agent/Web 具体查询独立保存 monitor run；非空完整结果保存快照并检测新增、消失和字段变化。
- `SUCCESS_EMPTY` 只保存空观察并告警，不生成变化、不创建或推进 baseline。
- 严格区分 query fingerprint、DMF business key 和内容 hash；缺失或重复 DMF 编号不自动匹配。
- monitor run 与 snapshot/diff 使用分离事务，历史处理失败不会改变 FDA 查询状态。
- 快照保存脱敏的完整分页 raw JSON、结构化 source、UTC queried_at 及内容 hash。
- Agent 确定性回答展示变化计数和比较警告，不展示 raw payload。
- 批量查询中途验证码失效后，剩余查询项补记为 `NOT_EXECUTED` 并独立统计。
- monitor run 保存每个具体查询的真实 `started_at`、`ended_at`；未执行项时间保持为空。
- 历史存储异常对外统一返回脱敏错误码和提示，完整异常仅保留在内部日志与审计字段。

### 验证记录

- 历史 repository、查询接入、采集状态和输出边界聚焦测试通过。
- Alembic 初始迁移在临时 SQLite 连续执行两次成功，5 张历史业务表完整创建。
- 新增事务中途失败测试：在 snapshot 与 records flush 后注入异常，验证新快照、记录、事件和 baseline 推进整体回滚，而 monitor run 独立留存。

## 2026-08-25：Agent 路由与回答稳定性收敛

### 已完成

- 将“分析/总结当前 DMF 结果”改为代码级跨轮路由，直接复用 `dmf_results`，不重新查询或进入文档流程。
- 将“查询这些/上述条件”等文档后续请求改为复用 `extracted_dmf_query`，兼容 `DFM` 常见笔误，并直接进入条件确认。
- 将 Excel 导出改为确定性节点：普通查询直接回答，明确要求导出时才执行；跨轮导出不重复输出完整结果表。
- 普通聊天直接进入 `general_chat`，不再经过可调用 DMF 查询或文档提取的动态工具 Agent。
- 取消 `search_dmf` 和 `extract_document_dmf_params` 的动态工具注册，固定 DMF 查询与文档 Flow 各保留单一执行入口。
- 删除无调用方的 LLM 工具 Agent 和调度提示词；保留通用工具执行器作为未来非重复扩展能力的基础设施。
- 将含混的 `dmf_post_process` 统一重命名为 `dmf_export`，同步 Schema、Graph 节点和工具上下文。
- 普通回答提示明确系统真实支持文件上传、解析、条件提取和确认后查询，禁止根据局部工具列表否认系统能力。

### 验证记录

- Agent、跨轮状态、文档、Web 上传、导出和工具基础设施相关回归：58 项通过。
- 新增“查询后分析结果不重复查询”和“文档提取后查询这些不重复解析”两轮 Graph 测试。
- VS Code 静态诊断无错误，源码中旧 `dmf_post_process` 和 LLM 工具 Agent 引用已清零。
- 全量 `pytest` 仍在收集阶段被两个既有错误导入阻断：`tests/captcha_ocr_test.py` 的 `captcha_client` 和 `tests/test_mineru_client.py` 的顶级 `config`。

## 2026-08-25：意图提示词精简

### 已完成

- 将 `DMF_INTENT_SYSTEM_PROMPT` 从约 130 行压缩到约 30 行。
- 删除已由确定性代码路由处理的重复导出、跨轮结果和冗长示例说明。
- 保留结构化意图仍需要的任务分类、字段提取、输出选择、文档判断和澄清规则。
- 摘要与分析提示词保持独立，避免改变真实结果的内容生成边界。

### 验证记录

- 意图输出、跨轮结果与文档 Graph 核心回归：26 项通过。
- `src/agent/prompts.py` 静态诊断无错误。

# 修改记录

## 2026-08-25：Web 会话记忆边界验证

### 验证结果

- 同一 Web session 连续三轮请求始终复用相同 LangGraph `thread_id`。
- 两个 Web session 使用不同 `thread_id`，会话状态互相隔离。
- 同一 LangGraph thread 连续 12 轮后，checkpoint 完整保留 12 条用户消息，没有固定轮数截断。
- 浏览器连续两次刷新会创建不同 session ID，刷新后消息列表为空；原因是前端初始化始终新建 session，且未使用浏览器存储恢复 session。
- 真实 API 连续创建 10 个 session，得到 10 个唯一 ID。
- 当前 8000 端口根路径返回 DMF 工作台，`/voice` 返回 404；浏览器中的 Peter 页面是未刷新的旧标签内容，不是应用路由或会话串线。
- 会话和跨轮相关自动化测试：8 项通过。

## 2026-08-25：移除工具执行批准

### 已完成

- 删除动态工具执行器的 `tool_approval` 中断，包含 Excel 导出在内的已注册工具现在会在同一轮直接执行。
- 删除 Web API 的 `approved_call_ids` 协议和前端“批准执行/拒绝”面板。
- CLI 不再询问工具批准，仅保留文档 DMF 查询条件的确认、修改或拒绝流程。
- 保留工具风险等级用于审计和分类，不再用于阻断执行。
- 更新同轮导出、跨轮导出、工具注册表和开发文档契约。

### 验证记录

- 工具执行器、Graph 自动导出、跨轮上下文与 Web 文档确认回归：9 项通过。
- 工具注册、CLI 文档命令和全部相关入口回归：16 项通过。
- Playwright 实测“查询 Ibuprofen → 导出 Excel”：导出请求直接返回 `completed`，无 interrupt 和批准按钮，并生成可用下载链接。

## 2026-08-25：Web 文档拖放上传

### 已完成

- 为整个工作台增加文件 `dragenter`、`dragover`、`dragleave` 和 `drop` 处理，阻止浏览器默认打开拖入文件。
- 拖入文件时显示全页面释放遮罩，释放后复用现有 multipart 文档上传流程。
- 每次仅接受一个拖入文件；多文件、会话未建立、任务处理中和等待确认状态均提供明确提示。
- 兼容 `dragleave` 不再暴露文件类型的浏览器行为，避免拖放遮罩残留。

### 验证记录

- Playwright 验证拖入遮罩、multipart 文件名、上传完成后的文档状态和遮罩关闭。
- Playwright 验证无类型 `dragleave` 与多文件拒绝行为。

## 2026-08-25：Apollo 自签名证书连接修复

### 已完成

- 定位 Web 消息持续返回 500 的根因：Apollo OAuth Token 请求校验企业自签名证书链失败，异常发生在 Agent 意图识别前。
- 新增 `APOLLO_VERIFY_SSL` 配置，并统一应用到 OAuth `requests`、ChatOpenAI 同步 `httpx` 和异步 `httpx` 客户端。
- 配置支持 `true`（默认严格校验）、`false`（受控本地开发环境）或企业 CA 文件路径；不存在的 CA 文件会返回明确配置错误。
- 当前本地 `.env` 显式配置为 `false`，生产环境应优先配置受信任的企业 CA 文件。

### 验证记录

- Apollo SSL 配置聚焦测试：4 项通过。
- 真实 OAuth Token 请求成功，未输出 Token 内容。
- Playwright 从工作台发送 Ibuprofen 查询，消息接口返回 200 并成功展示 21 条 DMF 记录。
- VS Code 静态诊断未报告错误。

## 2026-08-25：独立 Web 模块迁移

### 已完成

- 新建 `src/web` 包，集中管理 Agent Web API、页面模板和静态资源。
- 将浏览器会话 API 从 `src/agent/web_routes.py` 迁移到 `src/web/routes/agent.py`，Agent 包只保留 LangGraph 业务逻辑。
- 将工作台模板和 CSS/JavaScript 从 `src/mineru` 迁移到 `src/web`，MinerU 包只保留文档解析路由与服务。
- 更新 FastAPI 装配入口、配置默认路径和测试引用；`TEMPLATE_DIR`、`STATIC_DIR` 环境变量覆盖方式保持不变。
- 删除旧 Web 路由和旧静态资源目录，不保留双重所有权的兼容模块。
- `/`、`/static/*`、`/api/agent/*` 和 `uvicorn src.main:app` 外部契约保持不变。

### 验证记录

- 独立 Web 路由聚焦测试：2 项通过。
- Web API、文档服务、文档 Graph、Workflow 和输出格式相关回归：34 项通过。
- 首页、JavaScript、CSS 和既有 Agent OpenAPI 路径启动探针通过，默认模板与静态资源目录均指向 `src/web`。
- Playwright 验证桌面与 390x844 移动视口：工作台和会话正常加载，移动菜单可见，页面无横向溢出。
- VS Code 静态诊断未报告错误，代码中无旧 Web 模块或 MinerU 静态目录引用。

## 2026-08-24：DMF Agent Web 工作台

### 已完成

- 将首页升级为响应式 DMF Agent 工作台，提供聊天、活动文档、批量条件确认、工具审批、结果表格、未命中条目和 Excel 下载。
- 新增进程内 Web 会话 API，使用 LangGraph `thread_id` 与 `InMemorySaver` 保持多轮上下文和 interrupt 恢复。
- 文档通过 `multipart/form-data` 上传，复用既有文件校验与 `DocumentDMFService`；支持 PDF、Office、图片、Excel、Markdown 和文本。
- 导出文件使用会话内随机 `file_id` 下载，不向浏览器开放任意本地路径。
- 前端使用原生 HTML/CSS/JS，安全地通过 DOM API 渲染回答和 Markdown 表格，不注入服务端 HTML。
- 桌面采用固定工作侧栏与稳定输入区，移动端侧栏可开合，查询表格使用局部横向滚动且页面无横向溢出。

### 验证记录

- Agent Web API 会话、interrupt 恢复和 multipart 上传测试：2 项通过。
- Agent Web API、文档 Graph 与回答格式回归：20 项通过。
- 上传白名单与 Agent API 回归：16 项通过。
- Playwright 验证 1440x900 和 390x844 布局、移动侧栏开合、结果表格、未命中清单、批量条件编辑与 resume 请求。

## 2026-08-24：混合查询展示未命中条目

### 已完成

- 批量查询部分命中时，在 DMF 记录表格后追加“未命中查询”清单。
- 未命中清单保留每项实际使用的 DMF 编号、申请商和成分组合，并明确标注返回 0 条记录。
- 仅将执行成功但无记录的查询列为未命中；网络、验证码和接口错误继续按查询失败处理。

### 验证记录

- DMF 回答格式聚焦测试：13 项通过。

## 2026-08-24：批量查询前置失败状态修复

### 已完成

- 修复验证码获取或网络连接在真实查询前失败时，返回 `failed_count=0` 被批量聚合器误判为成功的问题。
- 批量聚合现在按原查询项补记前置失败数量，并保留 DMF 编号、申请商、成分和上游错误消息。
- 零记录回答改为逐项展示实际查询条件与状态，区分“查询成功但无记录”和“验证码/网络导致查询未执行”。
- 部分查询失败时继续展示已成功取得的记录，并附加成功数和失败数提示。

### 验证记录

- 批量聚合与回答诊断聚焦测试：26 项通过。
- 当前真实接口诊断：FDA 验证码端点连接超时；四次约 15 秒超时与本次 74 秒总耗时吻合。

## 2026-08-24：Excel 多行 DMF 批量查询

### 已完成

- 文档 Flow 输出契约扩展为 `queries` 数组，每个元素保留一行独立的 DMF 编号、申请商和成分组合。
- 兼容旧的单对象 Flow 输出并自动包装为单项批次；批量候选会按原顺序去重。
- 用户确认前展示全部候选且不执行 DMF；确认后逐组查询并合并查询数量、成功数、失败数和明细。
- CLI 支持整体确认、拒绝或逐组编辑，不再只展示第一组条件。
- 新增 Apollo Studio Structured Output Schema 与提取提示配置文档。

### 验证记录

- 文档服务批量契约聚焦测试：13 项通过。
- 文档 Graph 与 CLI 批量确认聚焦测试：9 项通过。
- 文档服务、Graph、CLI 与 Workflow 工具相关测试：24 项通过。
- 排除仓库两个既有错误导入测试后，其余完整回归：59 项通过。

## 2026-08-24：Excel 文档上传支持

### 已完成

- 文档上传默认白名单新增 `.xlsx` 和 `.xls`，CLI 与 FastAPI 上传入口共享该配置。
- `.xlsx` 使用 `openpyxl` 本地结构化读取，旧 `.xls` 使用 `xlrd`；Excel 文件不上传 MinerU。
- 读取所有非空工作表并转换为受控 Markdown，每个 Sheet 保留独立标题和表格。
- 空表头生成稳定列名，重复表头追加序号；单元格竖线和换行进行 Markdown 转义。
- 公式只读取工作簿缓存结果，不执行公式；不解析宏、图表、图片、批注或外部链接。
- Excel 生成的 Markdown 继续复用 Apollo Studio Flow、可编辑条件确认和 DMF 查询链路。
- 空工作簿、损坏文件和转换后 Markdown 超限会返回明确错误，且不会残留不完整 artifact。
- 新增 `xlrd>=2.0.1,<3` 依赖，并在当前 Conda 环境安装 `xlrd 2.0.2`。

### 验证记录

- Excel 文档服务聚焦测试：11 项通过。
- 文档服务、Graph、CLI、Flow 和工具注册回归：24 项通过。

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