# 添加 Agent 工具

Agent 使用显式工具注册表。普通函数工具只需要定义 LangChain Tool、注册执行策略，并让注册中心加载工具模块。需要 `interrupt()`、checkpoint 或多步骤状态的能力必须作为 LangGraph 子图接入，不能由普通工具执行器调用。

## 1. 定义工具

在 `src/tools/` 下创建业务工具模块。工具应当是业务服务的薄适配层，不要把 HTTP、数据库或文件处理细节直接堆在 Agent 节点里。

```python
from langchain_core.tools import tool

from src.tools.registry import (
    ToolContext,
    ToolKind,
    ToolRisk,
    register_tool,
)


@tool("lookup_example", description="根据关键词查询示例数据。")
def lookup_example(keyword: str) -> dict:
    """Return structured example data."""
    return {
        "success": True,
        "items": [],
    }


register_tool(
    lookup_example,
    risk=ToolRisk.READ_ONLY,
    contexts={ToolContext.GENERAL},
    kind=ToolKind.FUNCTION,
    parallel_safe=True,
)
```

然后在 `src/tools/registry.py` 的 `load_builtin_tools()` 中导入新模块。`ToolKind.FUNCTION` 是默认值，可省略。外层还需要更新 `react_nodes.py` 的能力白名单和 `function_problem()` 执行门禁；仅注册不会自动获得执行权限。

## 2. 选择执行场景

- `ToolContext.GENERAL`：通用 Agent 可以选择的工具。
- `ToolContext.DMF_EXPORT`：已有可信 DMF 查询结果后才能选择的导出工具。

一个工具可以注册到多个场景。工具只会暴露在允许的场景中；模型尝试调用其他场景的工具时，执行器会拒绝。

## 3. 设置风险等级

- `ToolRisk.READ_ONLY`：只读操作，自动执行。
- `ToolRisk.LOCAL_WRITE`：写本地文件。
- `ToolRisk.EXTERNAL_WRITE`：修改外部系统。

风险等级用于审计、分类和执行策略。外层允许 `search_dmf` 只读查询，并对 `export_dmf_excel` 提供明确的本地写例外；其他本地写工具不会自动开放。Watchlist 和通知配置由专用子图校验和执行。风险必须按真实副作用声明，不能把写操作标为只读。

## 4. 区分函数工具和 Workflow

- `ToolKind.FUNCTION`：一次调用即可完成、无需跨轮恢复的原子能力。
- `ToolKind.WORKFLOW_HANDOFF`：把请求移交给静态 LangGraph 子图的模型可见协议。

能力选择遵循互斥规则：

- 明确条件的 DMF 查询使用 `search_dmf`，一次可包含多个成分。
- 文档提取、条件确认及确认后查询使用 `run_document_dmf_workflow`。
- 清单和通知管理使用 `run_watchlist_workflow`，实际发送仍由后台处理。
- 所有 Excel 导出使用外层 `export_dmf_excel`，包括文档查询结果。
- 每次模型决策只接受一个调用；多个调用整批拒绝且无副作用。不确定请求直接澄清，没有通用 fallback Workflow。

`WORKFLOW_HANDOFF` 是模型可见协议，函数体不可由通用执行器调用。外层按明确工具名进入静态子图。`completed` 返回 ReAct 继续用户要求的后续操作；`needs_clarification/cancelled/failed/rejected` 结束本轮。摘要和分析由外层基于真实结果回答，没有专用链。

包含 `interrupt()` 的 Workflow 必须遵守以下规则：

- 子图编译时不安装独立 checkpointer，由最外层 Graph 持有 checkpointer。
- `Command(resume=...)` 始终发送给最外层 Graph，并复用相同 `thread_id`。
- 不在普通 `BaseTool` 或线程池中调用子图，避免中断被异常处理吞掉。
- `interrupt()` 前的代码必须可重入且无非幂等副作用。

## 5. 注入可信 State

不应让模型生成的大型数据或安全上下文，可以用 `InjectedToolArg` 从工具参数 Schema 中隐藏，再由执行器从 Graph State 注入。

```python
from typing import Annotated

from langchain_core.tools import InjectedToolArg, tool


@tool("process_results")
def process_results(
    results: Annotated[dict, InjectedToolArg],
    output_name: str = "",
) -> dict:
    """Process trusted results."""
    return {"success": True}


register_tool(
    process_results,
    risk=ToolRisk.LOCAL_WRITE,
    contexts={ToolContext.DMF_EXPORT},
    state_arguments={"results": "dmf_results"},
)
```

`state_arguments` 的键是工具参数名，值是调用上下文中的可信 State 字段名。执行器始终用 State 值覆盖模型参数。导出前还会校验 `DMFSearchResult`，模型不能传入任意结果数据。

外层通过 `workflow_input/workflow_output` 和显式白名单桥接静态子图。外层仅保留 `react_messages`，内层消息和确认控制字段不回流。结果带服务端 `result_id/result_source`；新查询失败不使用旧结果，文档变更清理派生缓存与结果。多次独立查询不自动合并，导出针对活动结果。

## 6. 并行规则

主 ReAct 不允许同轮多调用。通用执行器在其他上下文保留以下并行规则：

- 本轮存在两个或更多待执行工具。
- 每个工具都声明 `parallel_safe=True`。
- 工具之间没有数据依赖。

只要本轮包含一个非并行安全工具，整批调用按顺序执行。依赖上一步结果的操作应由模型在下一轮发起。

## 7. 返回与错误

工具优先返回可 JSON 序列化的字典，并至少包含 `success` 和 `message`。不要吞掉异常；执行器会捕获异常，生成错误 `ToolMessage` 和工具事件，供模型或确定性输出节点解释。

工具循环默认最多 8 轮，包含函数、Workflow 和策略拒绝；恢复暂停不重复计数。预算必须为正整数。相同查询不重复执行，同请求重复导出复用产物，同请求清单写操作按规范化任务去重；这些保护不是跨进程崩溃后的 exactly-once 保证。手动检查继续使用 Service 的数据库幂等键。

## 8. 测试清单

新增工具至少覆盖：

1. 注册场景和风险等级正确。
2. 工具参数 Schema 不暴露注入字段。
3. 正常结果和业务空结果。
4. 参数错误和运行异常。
5. 写入能力经过明确门禁；Excel 本地写例外不放开其他写工具。
6. `parallel_safe=True` 时不存在共享可变状态或目标文件冲突。
7. Workflow handoff 不会被通用执行器调用。
8. 子图 `interrupt()` 能从最外层 Graph 暂停，并使用同一 `thread_id` 恢复。
9. 子图完成后只有白名单字段进入外层 State。

测试应使用 Fake Tool/Fake LLM，避免访问真实 Apollo、DMF 或 MinerU 服务。