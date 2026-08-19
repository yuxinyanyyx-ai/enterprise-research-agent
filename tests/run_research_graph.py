from src.agent.graph import research_graph


def main():
    result = research_graph.invoke(
        {
            "user_query": "帮我查询 Ibuprofen 的 DMF 信息",
            "warnings": [],
        }
    )

    print("\n========== Agent Answer ==========\n")
    print(result["final_answer"])


if __name__ == "__main__":
    main()