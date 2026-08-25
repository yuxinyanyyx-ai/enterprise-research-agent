
import time
from pathlib import Path
from typing import Callable
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from src.agent.graph import build_research_graph
from src.schemas.document_dmf import DocumentArtifact
from src.services.document_dmf_service import DocumentDMFService


def _file_command_path(question: str) -> Path:
    value = question[len("/file") :].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    if not value:
        raise ValueError("用法：/file \"C:\\path\\document.pdf\"")
    return Path(value).expanduser()


def _document_confirmation_response(
    interrupt_value: dict,
    input_fn: Callable[[str], str] = input,
) -> dict:
    query_payload = dict(interrupt_value.get("query") or {})
    queries = [dict(query) for query in query_payload.get("queries") or [query_payload]]
    print(f"\n文档：{interrupt_value.get('file_name', '')}")
    print(f"提取到 {len(queries)} 组查询条件：")
    for index, query in enumerate(queries, start=1):
        print(f"\n[{index}]")
        print(f"- DMF 编号：{query.get('dmf_no') or '未提取到'}")
        print(f"- 申请商：{query.get('applicant_name') or '未提取到'}")
        print(f"- 成分：{'、'.join(query.get('ingredients') or []) or '未提取到'}")
    choice = input_fn("\n确认、修改或拒绝？[c/e/r]：").strip().lower()
    if choice in {"c", "confirm", "y", "yes"}:
        return {"action": "confirm", "query": {"queries": queries}}
    if choice not in {"e", "edit"}:
        return {"action": "reject"}

    edited_queries = []
    for index, query in enumerate(queries, start=1):
        print(f"\n修改第 {index} 组，直接回车保留原值：")
        dmf_no = input_fn(f"DMF 编号 [{query.get('dmf_no', '')}]：").strip()
        applicant = input_fn(f"申请商 [{query.get('applicant_name', '')}]：").strip()
        current_ingredients = ", ".join(query.get("ingredients") or [])
        ingredients_text = input_fn(
            f"成分（逗号分隔）[{current_ingredients}]："
        ).strip()
        edited_queries.append(
            {
                "dmf_no": dmf_no or query.get("dmf_no", ""),
                "applicant_name": applicant or query.get("applicant_name", ""),
                "ingredients": (
                    [item.strip() for item in ingredients_text.split(",") if item.strip()]
                    if ingredients_text
                    else query.get("ingredients", [])
                ),
            }
        )
    return {"action": "edit", "query": {"queries": edited_queries}}


def _resume_after_interrupt(graph, config, result, input_fn=input):
    """Handle document query confirmation interrupts for the current request."""

    while result.get("__interrupt__"):
        interrupt_value = result["__interrupt__"][0].value
        if interrupt_value.get("type") != "document_query_confirmation":
            raise RuntimeError(f"不支持的中断类型：{interrupt_value.get('type', '')}")
        print(f"\nAgent：{interrupt_value.get('message', '请确认文档查询条件。')}")
        resume_value = _document_confirmation_response(interrupt_value, input_fn)
        result = graph.invoke(Command(resume=resume_value), config=config)

    return result


def main():
    graph = build_research_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": str(uuid4())}}
    active_document: dict = {}

    print("=" * 60)
    print("DMF Research Agent")
    print("你可以直接输入问题")
    print('输入 /file "文件路径" 上传并解析文档')
    print("输入 /document 查看当前文档，/clear-file 清除当前文档")
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

            normalized_question = question.lower()
            if normalized_question == "/file" or normalized_question.startswith("/file "):
                path = _file_command_path(question)
                print(f"\n系统：正在解析 {path.name}...")
                artifact = DocumentDMFService().parse_document(path)
                active_document = artifact.model_dump()
                print(f"系统：文档解析完成：{artifact.file_name}")
                continue

            if question.lower() == "/document":
                if active_document:
                    print(f"\n系统：当前文档：{active_document['file_name']}")
                else:
                    print("\n系统：当前没有活动文档。")
                continue

            if question.lower() == "/clear-file":
                active_document = {}
                print("\n系统：已清除当前文档引用。")
                continue

            print("\nAgent：正在处理...")

            total_start = time.perf_counter()

            result = graph.invoke(
                {
                    "user_query": question,
                    "document_artifact": active_document,
                    "document_status": (
                        active_document.get("status", "") if active_document else ""
                    ),
                    "warnings": [],
                },
                config=config,
            )
            result = _resume_after_interrupt(graph, config, result)

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