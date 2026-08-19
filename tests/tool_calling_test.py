from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from src.llm.apollo import create_apollo_llm

from src.tools.dmf_tools import search_dmf


@tool(
    "search_dmf_demo",
    description="查询指定药物成分的 DMF 数据。只有用户需要查询 DMF 数据时才使用。",
)
def search_dmf_demo(ingredient: str) -> dict:
    """Demo DMF search tool."""
    return {
        "ingredient": ingredient,
        "total_records": 2,
        "records": [
            {
                "dmf_no": "10001",
                "applicant": "Demo Pharma A",
                "status": "Active",
            },
            {
                "dmf_no": "10002",
                "applicant": "Demo Pharma B",
                "status": "Inactive",
            },
        ],
    }


SYSTEM_PROMPT = """
你是一个 DMF Research Agent。

你拥有 search_dmf_demo 工具，可以查询药物成分的 DMF 数据。

规则：
1. 普通聊天、概念解释，不要调用工具。
2. 用户要求查询某个成分的 DMF 数据时，调用 search_dmf_demo。
3. 不要为了展示工具能力而调用工具。
"""
tool_map = {
    "search_dmf": search_dmf,
}

from langchain_core.messages import (
    HumanMessage,
    SystemMessage,
    ToolMessage,
)


def run_case(llm_with_tools, query: str):
    print("=" * 70)
    print(f"用户：{query}")

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=query),
    ]

    # 第一次调用 LLM
    response = llm_with_tools.invoke(messages)

    tool_calls = getattr(response, "tool_calls", []) or []

    print(f"是否调用 Tool：{bool(tool_calls)}")

    # 不需要 Tool
    if not tool_calls:
        print(f"模型回答：{response.content}")
        return

    # 把 AIMessage 放回消息历史
    messages.append(response)

    # 执行每一个 Tool Call
    for call in tool_calls:
        tool_name = call["name"]
        tool_args = call["args"]

        print(f"Tool 名称：{tool_name}")
        print(f"Tool 参数：{tool_args}")

        tool = tool_map[tool_name]

        print("开始执行 Tool...")

        tool_result = tool.invoke(tool_args)

        print("Tool 执行完成")
        print(f"Tool Result：{tool_result}")

        # 非常关键：
        # Tool 结果必须通过 ToolMessage 返回给模型
        messages.append(
            ToolMessage(
                content=str(tool_result),
                tool_call_id=call["id"],
            )
        )

    # 第二次调用 LLM
    # 现在模型已经看到了真实 Tool Result
    final_response = llm_with_tools.invoke(messages)

    print("-" * 70)
    print("最终回答：")
    print(final_response.content)
def main():
    llm = create_apollo_llm()

    tools = [search_dmf]
    # 最关键的一步：把 Tool 暴露给 LLM
    llm_with_tools = llm.bind_tools(tools)

    test_cases = [
        "你好",
        "什么是 DMF？",
        "查询 Ibuprofen 的 DMF",
        "帮我查一下 Aspirin 的 DMF 数据",
        "Ibuprofen 是什么药？",
    ]

    for query in test_cases:
        run_case(llm_with_tools, query)


if __name__ == "__main__":
    main()