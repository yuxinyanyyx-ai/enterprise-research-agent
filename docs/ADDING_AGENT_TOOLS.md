# 添加 Agent 工具

Agent 使用显式 Provider 和工具注册表。普通函数工具只需实现 LangChain Tool，并在 Provider 中注册；新增 Provider 时只需加入 `src/tools/providers.py` 的 `BUILTIN_PROVIDERS`，无需修改 `react_nodes.py`、`react_graph.py` 或执行器。没有包扫描，也不通过 import 副作用注册。

本期只支持普通 Tool 的注册驱动扩展。Workflow 仍保留现有静态接入方式；需要 `interrupt()`、checkpoint 或跨轮恢复的能力不能由普通工具执行器调用。

## 1. 定义工具

在 `src/tools/` 下创建业务工具模块。工具应当是业务服务的薄适配层，不要把 HTTP、数据库或文件处理细节直接堆在 Agent 节点里。

```python
from langchain_core.tools import tool

from src.tools.registry import (
    ToolContext,
    ToolKind,
    ToolRegistry,
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


def register_example_tools(registry: ToolRegistry) -> None:
    register_tool(
        lookup_example,
        registry=registry,
        risk=ToolRisk.READ_ONLY,
        contexts={ToolContext.GENERAL},
        kind=ToolKind.FUNCTION,
        parallel_safe=True,
    )
```

在 `src/tools/providers.py` 显式导入 `register_example_tools`，并加入 `BUILTIN_PROVIDERS`。同一领域已有 Provider 时，在该函数内增加注册即可。`ToolKind.FUNCTION` 是默认值，可省略。

Provider 是 `Callable[[ToolRegistry], None]`，必须只向传入的 registry 注册，不修改全局状态，也不连接外部服务。同一 Provider 可用于构建多个独立 registry；同一 registry 内名称重复会立即报错。

`load_builtin_tools()` 在锁内构建并缓存生产 registry；`build_builtin_registry(providers=(... ,))` 每次返回独立实例，空元组可构建空 registry。Graph 构建时接收 `registry=...`，模型绑定、路由和执行共享该实例。生产 Provider 变更后重启进程，不支持运行期热插拔。

未来 MCP 的远端发现、会话生命周期和失败策略由单独 MCP Provider/适配层处理，本次不实现 MCP，也不进行 Python 包自动发现。

## 2. 选择执行场景

- `ToolContext.GENERAL`：通用 Agent 可以选择的工具。
- `ToolContext.DMF_EXPORT`：已有可信 DMF 查询结果后才能选择的导出工具。

一个工具可以注册到多个场景。工具只会暴露在允许的场景中；模型尝试调用其他场景的工具时，执行器会拒绝。

## 3. 设置风险等级

- `ToolRisk.READ_ONLY`：只读操作，自动执行。
- `ToolRisk.LOCAL_WRITE`：写本地文件。
- `ToolRisk.EXTERNAL_WRITE`：修改外部系统。

风险不是授权凭据。普通 Tool 的 `LOCAL_WRITE` 和 `EXTERNAL_WRITE` 未提供 `authorize` 时默认拒绝，普通只读 Tool 默认允许。外层与底层执行器都检查策略，不能靠直接调用底层执行器绕过。Watchlist 和通知配置仍由专用子图校验和执行。风险必须按真实副作用声明，不能把写操作标为只读。

可选注册策略的职责如下：

- `availability(state) -> bool`：决定是否绑定给模型，执行前也重新检查。例如导出要求有效的活动结果。
- `authorize(state, call) -> str | None`：判断动作权限，拒绝时返回原因，允许时返回 `None`。例如导出要求用户明确提出导出动作，外部写操作需要可信权限或确认凭据。
- 参数有效性由 Tool Schema 和业务 validator 负责，不由 `authorize` 比较参数与用户原文。`search_dmf` 在 Service 调用前清理空白并要求至少一个有效条件，允许“布洛芬”标准化为 `Ibuprofen`。

策略应为无副作用函数，可能在执行前被多次调用。信任身份、租户和确认信息应取自服务端 State，不能信任模型提供的同名参数。Schema 只能验证类型与业务约束，不能证明药名翻译或实体标准化正确；有歧义时需要澄清和业务评测。文档条件仍必须走确认 Workflow，外层提示保留这一约束，不用字符串包含关系充当来源证明。

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


def register_processing_tools(registry: ToolRegistry) -> None:
    register_tool(
        process_results,
        registry=registry,
        risk=ToolRisk.READ_ONLY,
        contexts={ToolContext.GENERAL},
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

普通 Tool 不需要自定义返回适配：原始结果会进入 `ToolMessage` 和 `tool_artifacts`。默认 `result_adapter(state, result)` 在 `success=False` 时结束本轮，其余结果允许模型继续。成功的空结果不是失败。

需要写回外层已有 State 字段或自定义部分成功语义时，在 Provider 中指定 `result_adapter`，返回 State 更新字典。适配器必须无外部副作用，只更新该能力拥有的字段，不覆盖消息、轮数或账本。DMF adapter 负责活动结果、结果 ID、部分成功和失败后旧结果失效；简单 Tool 不需要实现 adapter。成功产物若返回非空字符串 `file_path`，最终回答会补全路径；只应返回真实生成的文件。

工具循环默认最多 8 轮，包含函数、Workflow 和策略拒绝；恢复暂停不重复计数。预算必须为正整数。外层默认拒绝同请求内重复的同工具同参数调用，可用 `repeat_message` 自定义原因。声明 `reuse_result=True` 后允许复用已有结果，不重新执行工具或 result adapter。去重键包含工具名、参数、所有注入的 State 值以及 `deduplication_state` 声明的附加字段；每次复用前仍重新检查可用性和授权。导出额外包含 `result_id`，避免复用不同活动结果的文件。

同请求清单写操作仍按规范化任务去重。这些保护不是跨进程崩溃后的 exactly-once 保证；外部写操作还需业务服务提供幂等机制。手动检查继续使用 Service 的数据库幂等键。

## 8. 测试清单

新增工具至少覆盖：

1. 注册场景和风险等级正确。
2. 工具参数 Schema 不暴露注入字段。
3. 正常结果和业务空结果。
4. 参数错误和运行异常。
5. 本地和外部写入缺少授权策略时都被拒绝；直接调用底层执行器也不能绕过。
6. `parallel_safe=True` 时不存在共享可变状态或目标文件冲突。
7. Workflow handoff 不会被通用执行器调用。
8. 子图 `interrupt()` 能从最外层 Graph 暂停，并使用同一 `thread_id` 恢复。
9. 子图完成后只有白名单字段进入外层 State。
10. 一个全新名称的普通 Tool 仅通过 Provider 注册就能在真实外层 Graph 中绑定、路由和执行。
11. Provider 构建独立 registry，不依赖导入顺序；重复注册报错，生产 registry 被缓存。
12. 不可用工具即使被模型强行调用也会被拒绝；重复调用策略和可信注入字段不因缓存而被绕过。
13. 标准化药名不会被授权层误杀；空白或无条件查询在 Service 调用前被拒绝。

测试应使用 Fake Tool/Fake LLM，避免访问真实 Apollo、DMF 或 MinerU 服务。`tests/test_tool_extensibility.py` 提供普通 Tool 的完整契约示例；Workflow 相关条目用于保留当前行为，不代表新增 Workflow 仅注册即可使用。