from src.agent.graph import research_graph
import time


def main():
    print("=" * 60)
    print("DMF Research Agent")
    print("你可以直接输入问题")
    print("输入 exit / quit / q 退出")
    print("=" * 60)

    while True:
        try:
            question = input("\n你：").strip()

            if not question:
                continue

            if question.lower() in {"exit", "quit", "q"}:
                print("\nAgent：再见。")
                break

            print("\nAgent：正在处理...")

            total_start = time.perf_counter()

            result = research_graph.invoke(
                {
                    "user_query": question,
                    "warnings": [],
                }
            )

            total_elapsed = (
                    time.perf_counter() - total_start
            )

            print(
                f"\n[总耗时] {total_elapsed:.2f}s"
            )

            answer = result.get("final_answer")

            if not answer:
                answer = "本次任务没有生成最终回答。"

            print(f"\nAgent：{answer}")

        except KeyboardInterrupt:
            print("\n\nAgent：再见。")
            break

        except Exception as exc:
            print(f"\nAgent：本次处理失败：{exc}")


if __name__ == "__main__":
    main()