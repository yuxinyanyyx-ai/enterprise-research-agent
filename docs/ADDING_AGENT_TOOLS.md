# 添加 Agent 工具

Agent 使用显式工具注册表。新增工具不需要修改 LangGraph，只需要定义 LangChain Tool、注册执行策略，并让注册中心加载工具模块。

## 1. 定义工具

在 `src/tools/` 下创建业务工具模块。工具应当是业务服务的薄适配层，不要把 HTTP、数据库或文件处理细节直接堆在 Agent 节点里。

```python
from langchain_core.tools import tool

from src.tools.registry import (
    ToolContext,
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
    parallel_safe=True,
)
```

然后在 `src/tools/registry.py` 的 `load_builtin_tools()` 中导入新模块。这里只负责显式暴露工具，主图不需要增加节点或分支。

## 2. 选择执行场景

- `ToolContext.GENERAL`：通用 Agent 可以选择的工具。
- `ToolContext.DMF_POST_PROCESS`：固定 DMF 查询完成后才能选择的工具。

一个工具可以注册到多个场景。工具只会暴露在允许的场景中；模型尝试调用其他场景的工具时，执行器会拒绝。

## 3. 设置风险等级

- `ToolRisk.READ_ONLY`：只读操作，自动执行。
- `ToolRisk.LOCAL_WRITE`：写本地文件，执行前暂停并请求用户批准。
- `ToolRisk.EXTERNAL_WRITE`：修改外部系统，执行前暂停并请求用户批准。

风险必须按真实副作用声明，不能为了减少确认步骤把写操作标为只读。

## 4. 注入可信 State

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
    contexts={ToolContext.DMF_POST_PROCESS},
    state_arguments={"results": "dmf_results"},
)
```

`state_arguments` 的键是工具参数名，值是 `ResearchState` 字段名。执行器始终用 State 值覆盖模型参数。

## 5. 并行规则

只有同时满足以下条件的同轮调用才并行：

- 本轮存在两个或更多待执行工具。
- 每个工具都声明 `parallel_safe=True`。
- 工具之间没有数据依赖。

只要本轮包含一个非并行安全工具，整批调用按顺序执行。依赖上一步结果的操作应由模型在下一轮发起。

## 6. 返回与错误

工具优先返回可 JSON 序列化的字典，并至少包含 `success` 和 `message`。不要吞掉异常；执行器会捕获异常，生成错误 `ToolMessage` 和工具事件，供模型或确定性输出节点解释。

工具循环默认最多 8 轮。连续两轮出现完全相同的工具和参数时会停止，防止无限调用。

## 7. 测试清单

新增工具至少覆盖：

1. 注册场景和风险等级正确。
2. 工具参数 Schema 不暴露注入字段。
3. 正常结果和业务空结果。
4. 参数错误和运行异常。
5. 写入工具在批准前不产生副作用。
6. 用户拒绝时不执行。
7. `parallel_safe=True` 时不存在共享可变状态或目标文件冲突。

测试应使用 Fake Tool/Fake LLM，避免访问真实 Apollo、DMF 或 MinerU 服务。