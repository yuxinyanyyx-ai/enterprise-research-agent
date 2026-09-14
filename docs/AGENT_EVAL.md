# Agent Eval

Agent Eval 使用 YAML 描述多轮场景，并真实执行 LangGraph 与核心 Agent 节点。测试只替换外部依赖：LLM、DMF 服务、Watchlist 服务、文档提取器和导出文件系统。

## Architecture

```text
YAML Case
    |
shared loader -> AgentEvalRunner -> shared assertions
                         |
              Scripted LLM or Real LLM
```

禁止替换实际路由、领域解析、业务节点或外层执行器。Runner 在注入外部边界后调用 `build_react_graph(checkpointer=InMemorySaver(), llm_factory=...)`。领域 handoff、暂停恢复和后续导出均经过生产图。

Scripted LLM 的 `tool_calls` fixture 提供标准工具协议；领域任务另提供专用参数模型输出。`answer_from: tool` 是测试替身根据实际工具返回生成固定展示，不是生产摘要/分析链。这些测试验证协议和业务边界，不证明真实模型的自然语言选路能力。日志隔离到评估临时目录。

## Files

- `tests/agent_eval/cases/*.yaml`: 场景定义
- `tests/agent_eval/schema.py`: 严格 Pydantic 合同
- `tests/agent_eval/loader.py`: 安全加载、去重和筛选
- `tests/agent_eval/fakes.py`: scripted LLM 与外部边界 fake
- `tests/agent_eval/runner.py`: deterministic/live 共用执行器
- `tests/agent_eval/assertions.py`: 结构、事实、禁止项和调用断言
- `tests/agent_eval/report.py`: live 脱敏报告

## Add A Case

复制现有 YAML case，并分配全局唯一 ID。一个最小查询场景包含：

```yaml
cases:
  - id: DMF-NEW-001
    description: Query one ingredient
    tags: [dmf]
    fixtures:
      selection:
        kind: llm
        value:
          tool_calls:
            - {name: search_dmf, args: {ingredients: [Ibuprofen]}, id: query-1, type: tool_call}
      answer:
        kind: llm
        value: {answer_from: tool}
      result:
        kind: dmf_result
        value:
          success: true
          message: 查询完成
          query: {ingredients: [Ibuprofen]}
          query_count: 1
          success_count: 1
          failed_count: 0
          total_records: 0
          results: []
    turns:
      - user_query: 查询 Ibuprofen
        scripted_llm: [selection, answer]
        dmf_result: result
        expected:
          route: execute_function_tools
          call_deltas: {llm: 2, dmf: 1}
```

未知字段、重复 ID、空 turns、非法 fixture 引用会在 pytest 收集时失败。Scripted fixture 只由 deterministic provider 消费；live 模式使用同一个 Case 和 Assertions，但忽略 scripted LLM 输出。

回答断言优先使用稳定事实：`contains`、`not_contains`、`regex` 和 `markdown_headers`。不要对 LLM 生成的摘要或分析使用完整文本 golden。

Watchlist 场景使用 `watchlist_result` fixture，并通过 `call_deltas.watchlist` 或 `expected_calls.watchlist` 精确约束业务调用次数。覆盖单项六动作、唯一历史结果复用、多结果澄清和高影响请求阻断；真实 Service/Repository/SQLite 行为保留在普通集成测试中，不在 YAML fake 中重复实现。

## Commands

默认离线执行：

```powershell
C:\BITrusted\work\python.exe -m pytest tests/test_agent_eval_cases.py -m agent_eval -q
```

执行单个 Case：

```powershell
$env:AGENT_EVAL_CASE = "DMF-001"
C:\BITrusted\work\python.exe -m pytest tests/test_agent_eval_cases.py -m agent_eval -q
```

也可以使用 pytest `-k DMF-001`。`AGENT_EVAL_CASE` 支持逗号分隔的精确 ID。

真实 LLM 是显式 opt-in，并仍使用 fake DMF、文档和导出边界：

```powershell
$env:AGENT_EVAL_LIVE_LLM = "1"
$env:AGENT_EVAL_MAX_CASES = "1"
C:\BITrusted\work\python.exe -m pytest tests/test_agent_eval_live.py -m live_llm -q
```

`AGENT_EVAL_LIVE_DMF=1` 保留给少量显式 live DMF smoke case，不会由 `AGENT_EVAL_LIVE_LLM` 自动开启。

## Reports And Sensitive Data

Live 报告写入 `outputs/agent_eval/<timestamp>/report.json` 和 `report.md`。报告不保存 prompt、fixture、调用参数、文档原文或 FDA raw payload，并遮盖 Authorization、Bearer、API key、token、Cookie 和 captcha。报告中的 `manual_review` 初始为空，首版不使用第二个 LLM judge。
