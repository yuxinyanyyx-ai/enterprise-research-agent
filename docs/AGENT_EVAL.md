# Agent Eval

Agent Eval 使用 YAML 描述多轮场景，并真实执行 LangGraph 与核心 Agent 节点。测试只替换外部依赖：LLM、DMF 服务、文档提取器和导出文件系统。

## Architecture

```text
YAML Case
    |
shared loader -> AgentEvalRunner -> shared assertions
                         |
              Scripted LLM or Real LLM
```

禁止替换 `understand_request`、route、`query_dmf`、document、export 或 answer 节点。Runner 在注入边界后调用 `build_research_graph(InMemorySaver())`，不使用模块级 `research_graph`。

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
      intent:
        kind: llm
        value:
          data_source: dmf
          requested_outputs: [result]
          ingredients: [Ibuprofen]
      result:
        kind: dmf_result
        value:
          success: true
          query_count: 1
          success_count: 1
          failed_count: 0
          total_records: 0
          results: []
    turns:
      - user_query: 查询 Ibuprofen
        scripted_llm: [intent]
        dmf_result: result
        expected:
          route: query_dmf
          call_deltas: {llm: 1, dmf: 1}
```

未知字段、重复 ID、空 turns、非法 fixture 引用会在 pytest 收集时失败。Scripted fixture 只由 deterministic provider 消费；live 模式使用同一个 Case 和 Assertions，但忽略 scripted LLM 输出。

回答断言优先使用稳定事实：`contains`、`not_contains`、`regex` 和 `markdown_headers`。不要对 LLM 生成的摘要或分析使用完整文本 golden。

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
