from src.agent.react_graph import build_react_graph


def main():
    result = build_react_graph().invoke(
        {
            "user_query": "帮我查询 Ibuprofen 的 DMF 信息",
            "warnings": [],
        }
    )

    print("\n========== Agent Answer ==========\n")
    print(result["final_answer"])


if __name__ == "__main__":
    main()