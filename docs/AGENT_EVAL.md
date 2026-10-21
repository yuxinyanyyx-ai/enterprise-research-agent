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

- `tests/agent_eval/cases/*.yaml`: 基础场景与可读的端到端示例
- `tests/agent_eval/reliability_cases.py`: 固定生成的自然语言、DMF 查询、导出、失败和恢复场景
- `tests/agent_eval/schema.py`: 严格 Pydantic 合同
- `tests/agent_eval/loader.py`: 安全加载、去重和筛选
- `tests/agent_eval/fakes.py`: scripted LLM 与外部边界 fake
- `tests/agent_eval/runner.py`: deterministic/live 共用执行器
- `tests/agent_eval/assertions.py`: 结构、事实、禁止项和调用断言
- `tests/agent_eval/metrics.py`, `tests/agent_eval/report.py`: 汇总指标及脱敏报告

当前固定集合含 51 个标准 Agent 场景，涵盖非工具请求、DMF 工具选择、跨轮上下文、查询后导出、多步骤任务、上游失败和文档确认/恢复。另有一个单独 opt-in 的 Watchlist 真实服务集成流程，不计入标准模型评测场景。每个标准场景的 `tool_decisions` 记录每次 ReAct 模型决策应选的工具；空数组代表此轮应直接回答而不调用工具。生成式场景 ID 固定，输入均为合成业务资料。

## Metrics

- **Task Success Rate**：完整 Case 通过率，Case 内所有轮次的路由、状态、答案事实、调用次数和工具选择断言均须通过。
- **Tool Selection Accuracy**：每个 ReAct 模型决策的预期工具列表与实际列表完全相等的比例；“不调用工具”也作为一个决策。此指标不代表工具参数准确率。
- **Tool call counts**：按 LLM、DMF、文档提取、导出和 Watchlist 汇总实际边界调用次数。
- **P50/P95 latency**：分别统计整轮图执行和单次 LLM 调用延迟，单位为毫秒。
- **Token consumption**：优先使用模型返回的 usage metadata；模型未返回时用上下文估算器估算。JSON/Markdown 报告同时标注 token 数据来源，不应将估算值误报成供应商账单用量。
- **Failure reasons**：按稳定错误类别汇总，不保存提示词、工具参数或异常消息。

Deterministic 模式可离线回归编排、边界和业务断言，但 Scripted LLM 会预先给出工具选择，**不能证明真实模型达到 Task Success Rate 或 Tool Selection Accuracy 目标**。

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

这会运行 53 个固定标准/上下文 Case 和 Eval 框架测试；网络访问被阻断，DMF、文档、导出和 Watchlist 外部边界使用严格 fake。

执行单个 Case：

```powershell
$env:AGENT_EVAL_CASE = "DMF-001"
C:\BITrusted\work\python.exe -m pytest tests/test_agent_eval_cases.py -m agent_eval -q
```

也可以使用 pytest `-k DMF-001`。`AGENT_EVAL_CASE` 支持逗号分隔的精确 ID。

真实 LLM 是显式 opt-in，并仍使用 fake DMF、文档和导出边界：

```powershell
$env:AGENT_EVAL_LIVE_LLM = "1"
Remove-Item Env:AGENT_EVAL_MAX_CASES -ErrorAction SilentlyContinue
C:\BITrusted\work\python.exe -m pytest tests/test_agent_eval_live.py -m live_llm -q
```

完整模型评测会调用真实配置的 LLM，可能产生费用；执行前确认服务权限和测试环境配置。设置 `AGENT_EVAL_MAX_CASES` 可只运行前 N 个标准 Case 做 smoke test。若要检验目标阈值，必须运行完整固定集合，不能用 deterministic 结果代替。

每个 Case 的脱敏记录写入 `outputs/agent_eval/<timestamp>/`；整次执行结束后，汇总写入 `outputs/agent_eval/<timestamp>-summary/metrics.json` 和 `metrics.md`。查看 `metrics.md` 中的通过率、工具选择准确率、P50/P95、调用数、Token 来源和失败分类，再与目标 `Task Success Rate ≥90%`、`Tool Selection Accuracy ≥95%` 比较。失败的 Case 也会进入汇总，不应只报告通过的样本。

`AGENT_EVAL_LIVE_DMF=1` 保留给少量显式 live DMF smoke case，不会由 `AGENT_EVAL_LIVE_LLM` 自动开启。

## Reports And Sensitive Data

Live 报告写入 `outputs/agent_eval/<timestamp>/report.json` 和 `report.md`。报告不保存 prompt、fixture、调用参数、文档原文或 FDA raw payload，并遮盖 Authorization、Bearer、API key、token、Cookie 和 captcha。报告中的 `manual_review` 初始为空，首版不使用第二个 LLM judge。

## Verification Record — 2026-10-21

- Fixed scenario inventory: 54 cases total, including 51 standard cases available to deterministic and live-LLM modes, two additional deterministic-only context cases, and one separate real Watchlist integration case. The full live run recorded 100 ReAct tool/no-tool decisions.
- Agent/ReAct/tool/workflow regression command: `pytest` over `test_agent_*.py`, ReAct, tool, workflow, and document-DMF suites — **321 passed, 52 skipped**, with one Starlette/httpx deprecation warning.
- Idempotency regression verifies that replaying the same export with the same result state reuses the completed artifact, and replaying a Watchlist write with the same request ledger does not call the service again. Document confirmation/resume is also covered by the existing graph tests.
- Full live LLM evaluation: 51 cases against `claude_4_6_sonnet`, with DMF, document, export, and Watchlist boundaries replaced by fakes. **Task Success Rate: 47/51 (92.2%)** and **Tool Selection Accuracy: 98/100 (98.0%)**; both target thresholds were met. The pytest command still exited nonzero because four case assertions failed; the failures are included in the reported success rate and were not excluded.
- Live performance and usage: request P50/P95 **8264.8/14908.281 ms**; LLM P50/P95 **3167.725/7545.082 ms**. Input/output/total tokens were **333,766/16,780/350,546**; 100 usage records were actual provider metadata and 14 were estimated. Boundary calls: LLM 114, DMF 34, document extraction 6, export 8, Watchlist 8. Estimated tokens are not provider-billed usage.
- Live assertion failures: `DMF-001` returned the expected result data but used the header `申请商名称` instead of the exact expected header `申请商`; `WATCH-001` completed deletion and identified `watch-1` but omitted its associated DMF number in the final response; `WATCH-003` directly asked for confirmation about the two DMF results without calling the Watchlist workflow or making a write; `WATCH-004` directly refused bulk deletion and made no Watchlist call. The last two are safe no-write responses, but they did not match the fixed workflow/tool-selection expectations. These cases remain visible as failures pending a deliberate decision about output requirements and acceptable safe no-tool alternatives.
- Detailed live metrics: [metrics.md](../outputs/agent_eval/20261021T075536912995Z-summary/metrics.md).
- Failed-case report pages: [DMF-001](../outputs/agent_eval/20261021T074702724764Z/report.md), [WATCH-001](../outputs/agent_eval/20261021T074910748658Z/report.md), [WATCH-003](../outputs/agent_eval/20261021T074930284775Z/report.md), and [WATCH-004](../outputs/agent_eval/20261021T074934542591Z/report.md).
