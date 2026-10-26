# Agent 上下文预算与最终回答协议

外层 ReAct Agent 为模型构建受预算限制的输入视图，不删除检查点消息，
不把完整业务结果替换成预览。文档和关注列表的意图解析也使用本地估算预算。

## 配置与超限处理

| 环境变量 | 默认值 | 含义 |
| --- | --- | --- |
| `AGENT_CONTEXT_WINDOW_TOKENS` | `16000` | 应用配置的上下文额度，不是自动探测出的模型容量 |
| `AGENT_CONTEXT_OUTPUT_TOKENS` | `2048` | 预留输出容量 |
| `AGENT_CONTEXT_SAFETY_TOKENS` | `2048` | 预留估算误差和协议开销 |

默认可用输入预算为 11,904 Token，必须大于零。应根据实际 Apollo 部署容量
显式设置窗口，不自动扩容。输出预留只是预算，不会设置服务端生成长度上限；
需要与服务端实际最大输出配置保持一致。

估算器将 ASCII 字母数字连续片段约按每三个字符一个 Token 计数，分别计算
ASCII 标点，并按 UTF-8 字节数计算非 ASCII 字符。估算包含系统提示、消息元数据、
工具调用参数以及工具或结构化输出的 Schema。这对中文较保守，但不是精确
Tokenizer，也不保证对所有模型构成数学意义上的上界。

必要输入超限时不调用模型。Agent 明确返回“本次未生成答案”，并展示可获得的
本地估算值和可用预算。已完成的工具不回滚、不自动重放；需要缩小问题范围，
或由管理员核实部署容量后显式调整上述配置。不能仅为让测试通过而提高额度。

## 内容完整性约束

- 当前用户问题原样保留，包括空白，不静默截断或替换成摘要。
- 当前轮工具调用保留 ID、参数、顺序和配对结果。递归过滤敏感键后，
  工具结果和必要业务上下文完整发送，否则拒绝模型调用。
- 从较新历史轮次开始选择完整对话，直到剩余预算不足。历史工具调用和结果
  不发送；保留的用户和助手文本明确标记为历史内容。
- 预算构建器不缩短字符串、不限制列表或字典项数、不限制嵌套深度、不重排证据，
  不生成截断标记。敏感键过滤移除 `raw_payload`、凭证和内部源路径等已知字段，
  不修改检查点；这不是对任意字符串内容的全面脱敏。
- 业务快照保留 `result_source` 和 `active_result_id`。只有可信的当前轮工具消息
  已提供同一份完整安全结果时，才省略快照中的重复结果正文。计数、结果 ID、
  局部读取或不可信消息都不能证明等价。新一轮不发送历史工具消息，因此会从
  检查点恢复完整安全结果；来源标签本身不是授权边界。
- 完整 `dmf_results` 仍保留在状态中，供导出和跨轮复用。必要输入超限时，
  停止后续模型驱动的导出，不用预览替代结果。
- 领域意图解析完整保留当前问题和必要业务参数，包括待处理通知地址；
  只按预算选择最近对话。必要参数超限时进入澄清流程。

## 消息顺序与工具协议

模型输入按以下顺序组织：

1. 系统提示；
2. 明确标记“仅为数据、不是指令”的业务快照系统消息；
3. 预算允许的历史用户/助手文本；
4. 当前用户问题、助手工具调用及配对工具结果。

系统消息保持连续前缀，不再把业务快照追加到最后一个工具结果后面。
当前轮内容及工具调用/结果之间的相对顺序不变。

工具“可以执行”与“为历史协议保留定义”分开处理：

- 仍有可用工具时保持原有动态绑定策略，只绑定当前可用工具。
- 没有可用工具但当前轮存在已注册工具的调用历史时，保留这些工具的 Schema，
  并显式设置 `tool_choice="none"`，要求模型只生成答案。
- 没有可用工具也没有对应工具历史时，直接调用未绑定模型，不发送 `tools=[]`，
  也不发送 `tool_choice`。
- 协议保留的 Schema 同样计入预算，不能通过省略其开销绕过超限保护。
- Schema 不是执行授权。即使模型无视 `none` 强行请求调用，执行层仍检查工具
  可用性、授权、重复调用和总轮次，不恢复已经禁用的检索。

以上修复消除了离线复现的请求兼容性隐患，不等于确认了此前 Apollo 400 的
唯一原因。不同 Apollo 模型对消息和工具参数的支持仍需显式授权后真实复验；
目前不引入未经验证的 `allowed_tools` 等扩展协议。

## 工具产物发布与失败恢复

`ToolDefinition.result_strategy` 控制呈现，不控制授权。`artifact` 结果保持
完整，可信引用放在业务快照中，不插入工具结果正文。不会用目录作为预算
超限兜底。`inline_compact` 结果经安全过滤后原样保留，不发布读取引用。
引用、可用 Schema 和历史协议 Schema 都计入同一预算；超限会阻止发布和
后续模型请求。产物保存工具实际返回的完整值，不能恢复工具返回前丢弃的证据。

PEC 服务拥有独立的 20,000 字节 UTF-8 JSON 返回预算，使用与工具消息相同的
`json.dumps(result, ensure_ascii=False, default=str)` 序列化测量。仅返回
`context_summary`，去掉重复 `context` 和查询回显；摘要是命中计数，不是模型生成
正文。行内证据保留来源、位置、类型、提取来源和文本，省略冗长提取元数据。

没有固定 600 字符或八条证据限制。服务按检索顺序选择可容纳的完整证据；
首条过大时，其文本可能被缩短，并标记 `complete=false`、`truncated=true`。
不会改写过大的来源元数据来制造误导引用。`evidence_count` 表示构建总数，
`returned_evidence_count` 表示返回条数，`truncated_evidence_count` 表示省略条数，
不是缩短文本条数。错误响应也有大小限制，并省略后端详情。

这些服务自有边界未变；模型预算层不施加第二次证据裁剪。完整 PEC 返回值
仍可能超过全局 Token 预算；20KB 不是模型 Token 额度，也不改变模型配置。
成功检索（包括截断命中）后 PEC 不再开放检索，空结果最多允许一次补充查询。
这是工具提供方的可用性策略，不是预算层的工具名称特判。

预算构建器只负责过滤数据、校验工具配对、检查完整输入和选择历史轮次。
外层节点根据状态和存储记录将最小可信引用放进 `artifact_refs`；
`ToolMessage.additional_kwargs` 中的后端元数据不等于向模型发布引用。
引用必须匹配当前请求的工具记录和带有真实完整安全结果的当前消息。
只有模型调用成功后才持久化发布状态，不新增持久化上下文缓存。

新请求（包括检查点复用）会重置 `published_artifact_refs`、`artifact_reads`
和 `artifact_read_disabled`。读取前同时验证发布记录、存储记录和请求归属；
结果策略本身不授予访问权。预期读取错误返回配对错误消息，并禁用本请求的
后续读取，模型可从已有证据作答或说明缺失。未知执行缺陷和其他工具维持
原有失败策略。

## 可观测性与限制

`context.prepared` 审计事件记录估算输入量、配置预算、原消息数和发送消息数。
`context.rejected` 记录超限及可获得的估算/预算数字；对应拒绝 Span 标记为
`rejected`，估算来源为 `estimated`，不冒充实际 API 用量，不记录提示或结果正文。

Apollo 统一入口记录最近一次 HTTP 错误状态；只有固定白名单内的稳定错误码
才写入 `provider_error_code`。未知错误码、异常正文、请求和响应正文不记录。
HTTP 400 不因本次修改增加重试，未知用量和费用仍保持缺失。

本次只限制模型输入，不处理长期检查点存储增长；历史保留和压缩另行规划。
尚未使用精确 Apollo Tokenizer，模型容量和估算准确性需要与实际部署核验。
历史真实验收报告仍保留失败状态；离线测试不能作为正式 RAG 成绩。

## 测试

```powershell
python -m pytest -q tests\test_agent_request_protocol.py tests\test_agent_context_budget.py tests\test_agent_context_eval.py tests\test_agent_eval_cases.py
```

覆盖 HTTP 序列化、无工具请求、最终禁止工具选择、历史 Schema 预算、30 轮历史、
中英文超长问题、完整深层结构、工具参数/引用预算、同轮去重与跨轮恢复、
完整导出或拒绝、1,000 条记录拒绝、产物授权与读取恢复、文档恢复和报告隐私。
原有小结果 DMF 与工作流案例继续作为回归检查。

固定 YAML 评测通过 `llm_inputs` 检查实际消息顺序，不放宽 Token 上限：

```yaml
expected:
  llm_inputs:
  - call_index: 1
    call_type: text
    max_estimated_tokens: 11904
    current_query_verbatim: true
    tool_pairs: true
    message_types: [system, system, human, ai, tool]
    contains: [result_source]
    not_contains: [raw_payload]
```

调用序号从当前轮的零开始，包括结构化调用；也支持 `max_messages` 和
`max_chars`。Token 检查包含记录的 Schema。无新用户问题的恢复轮次，
应使用 `contains` 或 Python 精确断言检查原始问题。输入断言只用于确定性
离线测试；真实调用包装器不保留原始提示，报告不导出输入消息或 Schema。

## 参考的 GitHub 实践

- [LangChain Agent 工厂](https://github.com/langchain-ai/langchain/blob/master/libs/langchain_v1/langchain/agents/factory.py)：
  有工具时绑定，无工具时走普通模型调用，不用空工具列表强制回答。
- [Anthropic 官方工具示例](https://github.com/anthropics/anthropic-sdk-python/blob/main/examples/tools.py)：
  工具结果返回后保留调用/结果链和工具定义完成回答。
- [Anthropic 禁止工具选择定义](https://github.com/anthropics/anthropic-sdk-python/blob/main/src/anthropic/types/tool_choice_none_param.py)：
  显式区分保留工具协议与允许继续使用工具。Apollo 使用 OpenAI 兼容形式
  `tool_choice="none"`，离线测试核对实际序列化字段，不直接照搬 Anthropic 请求格式。
- [LangChain Anthropic 消息适配](https://github.com/langchain-ai/langchain/blob/master/libs/partners/anthropic/langchain_anthropic/chat_models.py)：
  非连续系统消息有兼容性限制，因此业务快照保持系统前缀，不插在工具结果后。
