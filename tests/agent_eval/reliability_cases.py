"""Fixed, synthetic ReAct scenarios for routing and workflow reliability."""

from __future__ import annotations

from typing import Any

from tests.agent_eval.schema import AgentCase


def _case(
    case_id: str,
    description: str,
    turns: list[dict[str, Any]],
    fixtures: dict[str, dict[str, Any]],
    calls: dict[str, int],
    *,
    tags: list[str],
    documents: list[dict[str, Any]] | None = None,
) -> AgentCase:
    return AgentCase.model_validate({
        "id": case_id,
        "description": description,
        "tags": tags,
        "fixtures": fixtures,
        "turns": turns,
        "expected_calls": calls,
        **({"documents": documents} if documents else {}),
    })


def _llm(value: Any) -> dict[str, Any]:
    return {"kind": "llm", "value": value}


def _call(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {
        "tool_calls": [
            {"name": name, "args": args, "id": call_id, "type": "tool_call"}
        ]
    }


def _counts(
    *,
    llm: int,
    dmf: int = 0,
    document_extract: int = 0,
    export: int = 0,
    watchlist: int = 0,
) -> dict[str, int]:
    return {
        "llm": llm,
        "dmf": dmf,
        "document_extract": document_extract,
        "export": export,
        "watchlist": watchlist,
    }


def _dmf_result(ingredient: str, dmf_no: str) -> dict[str, Any]:
    return {
        "success": True,
        "message": "查询完成",
        "query_count": 1,
        "success_count": 1,
        "failed_count": 0,
        "total_records": 1,
        "results": [{
            "success": True,
            "message": "查询成功",
            "query": {"ingredient": ingredient},
            "total": 1,
            "total_pages": 1,
            "records": [{
                "dmf_no": dmf_no,
                "applicant_name": "Example Pharma",
                "ingredient": ingredient,
                "valid_date": "2027-01-01",
            }],
        }],
        "query": {"ingredients": [ingredient]},
    }


def _query_case(index: int, ingredient: str, query: str) -> AgentCase:
    case_id = f"ROUTE-DMF-{index:02d}"
    dmf_no = f"DMF-{index:05d}"
    return _case(
        case_id,
        f"Routes a differently phrased {ingredient} lookup to DMF search",
        [{
            "user_query": query,
            "scripted_llm": ["search", "answer"],
            "dmf_result": "result",
            "expected": {
                "route": "execute_function_tools",
                "state": {"dmf_results.success": True},
                "answer": {"contains": [ingredient, dmf_no]},
                "call_deltas": _counts(llm=2, dmf=1),
                "tool_decisions": [["search_dmf"], []],
            },
        }],
        {
            "search": _llm(_call(
                "search_dmf", {"ingredients": [ingredient]}, f"{case_id}-search"
            )),
            "answer": _llm({"answer_from": "tool"}),
            "result": {"kind": "dmf_result", "value": _dmf_result(ingredient, dmf_no)},
        },
        _counts(llm=2, dmf=1),
        tags=["dmf", "tool-selection", "reliability"],
    )


def _export_case(index: int, ingredient: str, query: str) -> AgentCase:
    case_id = f"FLOW-EXPORT-{index:02d}"
    dmf_no = f"EXP-{index:05d}"
    return _case(
        case_id,
        f"Completes {ingredient} lookup before the requested export",
        [{
            "user_query": query,
            "scripted_llm": ["search", "export", "answer"],
            "dmf_result": "result",
            "export_result": "file",
            "expected": {
                "route": "execute_function_tools",
                "answer": {"contains": [ingredient, dmf_no, f"agent-export-{index:02d}.xlsx"]},
                "artifact_types": ["export_dmf_excel"],
                "call_deltas": _counts(llm=3, dmf=1, export=1),
                "tool_decisions": [["search_dmf"], ["export_dmf_excel"], []],
            },
        }],
        {
            "search": _llm(_call(
                "search_dmf", {"ingredients": [ingredient]}, f"{case_id}-search"
            )),
            "export": _llm(_call("export_dmf_excel", {}, f"{case_id}-export")),
            "answer": _llm({"answer_from": "tool"}),
            "result": {"kind": "dmf_result", "value": _dmf_result(ingredient, dmf_no)},
            "file": {"kind": "export_result", "value": f"agent-export-{index:02d}.xlsx"},
        },
        _counts(llm=3, dmf=1, export=1),
        tags=["dmf", "export", "multi-step", "reliability"],
    )


def _failure_case(index: int, ingredient: str, query: str) -> AgentCase:
    case_id = f"RECOVERY-DMF-{index:02d}"
    message = f"模拟上游故障 {index}"
    result = {
        "success": False,
        "message": message,
        "query_count": 1,
        "success_count": 0,
        "failed_count": 1,
        "total_records": 0,
        "results": [{"success": False, "message": message, "records": []}],
        "query": {"ingredients": [ingredient]},
    }
    return _case(
        case_id,
        f"Surfaces the classified upstream failure for {ingredient} without inventing results",
        [{
            "user_query": query,
            "scripted_llm": ["search"],
            "dmf_result": "failure",
            "expected": {
                "route": "execute_function_tools",
                "answer": {
                    "contains": [message],
                    "not_contains": ["查询成功", "agent-export"],
                },
                "call_deltas": _counts(llm=1, dmf=1),
                "tool_decisions": [["search_dmf"]],
            },
        }],
        {
            "search": _llm(_call(
                "search_dmf", {"ingredients": [ingredient]}, f"{case_id}-search"
            )),
            "failure": {"kind": "dmf_result", "value": result},
        },
        _counts(llm=1, dmf=1),
        tags=["dmf", "failure", "recovery", "reliability"],
    )


def _retry_case() -> AgentCase:
    case_id = "RECOVERY-DMF-RETRY-01"
    ingredient = "Ibuprofen"
    failure = {
        "success": False,
        "message": "模拟上游故障，请稍后重试",
        "query_count": 1,
        "success_count": 0,
        "failed_count": 1,
        "total_records": 0,
        "results": [{
            "success": False,
            "message": "模拟上游故障，请稍后重试",
            "records": [],
        }],
        "query": {"ingredients": [ingredient]},
    }
    success = _dmf_result(ingredient, "DMF-RETRY-0001")
    return _case(
        case_id,
        "Recovers on an explicit retry after a transient DMF service failure",
        [
            {
                "user_query": "查询 Ibuprofen 的 DMF",
                "scripted_llm": ["first_search"],
                "dmf_result": "failure",
                "expected": {
                    "route": "execute_function_tools",
                    "answer": {"contains": ["模拟上游故障"]},
                    "call_deltas": _counts(llm=1, dmf=1),
                    "tool_decisions": [["search_dmf"]],
                },
            },
            {
                "user_query": "请重试刚才失败的 Ibuprofen DMF 查询",
                "scripted_llm": ["retry_search", "answer"],
                "dmf_result": "success",
                "expected": {
                    "route": "execute_function_tools",
                    "answer": {"contains": [ingredient, "DMF-RETRY-0001"]},
                    "state": {"dmf_results.success": True},
                    "call_deltas": _counts(llm=2, dmf=1),
                    "tool_decisions": [["search_dmf"], []],
                },
            },
        ],
        {
            "first_search": _llm(_call(
                "search_dmf", {"ingredients": [ingredient]}, f"{case_id}-first"
            )),
            "retry_search": _llm(_call(
                "search_dmf", {"ingredients": [ingredient]}, f"{case_id}-retry"
            )),
            "answer": _llm({"answer_from": "tool"}),
            "failure": {"kind": "dmf_result", "value": failure},
            "success": {"kind": "dmf_result", "value": success},
        },
        _counts(llm=3, dmf=2),
        tags=["dmf", "failure", "recovery", "cross-turn", "reliability"],
    )


def _document_case(index: int, action: str, ingredient: str) -> AgentCase:
    case_id = f"RESUME-DOC-{index:02d}"
    result = _dmf_result(ingredient, f"DOC-{index:05d}")
    fixtures: dict[str, dict[str, Any]] = {
        "handoff": _llm(_call(
            "run_document_dmf_workflow", {}, f"{case_id}-handoff"
        )),
        "intent": _llm({"query_document_conditions": True}),
        "answer": _llm({"answer_from": "tool"}),
        "extraction": {
            "kind": "document_extraction",
            "value": {"queries": [{"ingredients": [ingredient]}]},
        },
        "result": {"kind": "dmf_result", "value": result},
    }
    first_turn = {
        "user_query": "从已上传资料提取条件并查询 DMF",
        "scripted_llm": ["handoff", "intent"],
        "document_extraction": "extraction",
        "expected": {
            "route": "document_workflow",
            "interrupt": True,
            "answer": {"empty": True},
            "call_deltas": _counts(llm=2, document_extract=1),
            "tool_decisions": [["run_document_dmf_workflow"]],
        },
    }
    resume = {"action": action}
    resume_turn: dict[str, Any] = {
        "resume": resume,
        "expected": {
            "interrupt": False,
            "tool_decisions": [[]] if action != "reject" else [],
        },
    }
    if action == "reject":
        resume_turn["expected"].update({
            "answer": {"contains": ["取消"]},
            "call_deltas": _counts(llm=0),
        })
        expected_calls = _counts(llm=2, document_extract=1)
    else:
        resume_turn.update({"scripted_llm": ["answer"], "dmf_result": "result"})
        resume_turn["expected"].update({
            "route": "react_agent",
            "answer": {"contains": [ingredient, f"DOC-{index:05d}"]},
            "call_deltas": _counts(llm=1, dmf=1),
        })
        expected_calls = _counts(llm=3, dmf=1, document_extract=1)
    return _case(
        case_id,
        f"Resumes document confirmation exactly once after {action}",
        [first_turn, resume_turn],
        fixtures,
        expected_calls,
        tags=["document", "interrupt", "resume", "reliability"],
        documents=[{
            "document_id": f"document-{index}",
            "markdown": f"原料名称：{ingredient}",
        }],
    )


def reliability_cases() -> list[AgentCase]:
    """Return 40 fixed synthetic scenarios for the standard live evaluation set."""

    cases: list[AgentCase] = []
    chat_queries = [
        "你好",
        "谢谢你",
        "请简单介绍一下你能协助的工作",
        "请把这句话改得更简洁：项目正在按计划推进",
        "帮我拟一封礼貌的会议邀请邮件",
        "用中文解释一下什么是结构化数据",
        "给我三个整理工作计划的建议",
        "今天适合先完成哪一项待办？",
        "把这句话翻译成英文：会议将在周五举行",
        "怎样把一场会议安排得更高效？",
        "润色这句话：请在方便时回复我的消息",
    ]
    for index, query in enumerate(chat_queries, 1):
        case_id = f"ROUTE-CHAT-{index:02d}"
        cases.append(_case(
            case_id,
            "Answers a non-research request without invoking a business tool",
            [{
                "user_query": query,
                "scripted_llm": ["answer"],
                "expected": {
                    "route": "react_agent",
                    "answer": {"empty": False},
                    "call_deltas": _counts(llm=1),
                    "tool_decisions": [[]],
                },
            }],
            {"answer": _llm("可以，我会直接协助处理这个请求。")},
            _counts(llm=1),
            tags=["general-chat", "tool-selection", "reliability"],
        ))

    queries = [
        ("Ibuprofen", "查询 Ibuprofen 的 DMF"),
        ("Acetaminophen", "查一下 Acetaminophen 对应的 DMF"),
        ("Amoxicillin", "请检索 Amoxicillin 的 DMF 记录"),
        ("Metformin", "帮我找 Metformin 的 DMF 信息"),
        ("Omeprazole", "需要查询 Omeprazole 的 DMF"),
        ("Ceftriaxone", "查询一下 Ceftriaxone 的 DMF 资料"),
        ("Lidocaine", "请帮忙搜索 Lidocaine 对应的 DMF"),
        ("Azithromycin", "能否查询 Azithromycin 的 DMF 信息？"),
        ("Fluconazole", "检索 Fluconazole 的 DMF 记录"),
        ("Doxycycline", "我想查 Doxycycline 的 DMF"),
        ("Atorvastatin", "请找出 Atorvastatin 的 DMF 信息"),
        ("Pantoprazole", "帮我查找 Pantoprazole 相关的 DMF"),
    ]
    cases.extend(
        _query_case(index, ingredient, query)
        for index, (ingredient, query) in enumerate(queries, 1)
    )

    exports = [
        ("Ibuprofen", "查询 Ibuprofen 后导出 Excel"),
        ("Amoxicillin", "请检索 Amoxicillin 并下载 Excel"),
        ("Metformin", "查找 Metformin 的 DMF，完成后导出"),
        ("Omeprazole", "查询 Omeprazole 结果并保存为 Excel"),
        ("Lidocaine", "搜索 Lidocaine，然后给我 Excel 文件"),
        ("Azithromycin", "帮我查 Azithromycin 并导出查询结果"),
    ]
    cases.extend(
        _export_case(index, ingredient, query)
        for index, (ingredient, query) in enumerate(exports, 1)
    )

    failures = [
        ("Ibuprofen", "查询 Ibuprofen"),
        ("Amoxicillin", "帮我查询 Amoxicillin"),
        ("Metformin", "检索 Metformin 的 DMF"),
        ("Omeprazole", "请查找 Omeprazole 的 DMF 记录"),
        ("Lidocaine", "查询 Lidocaine 相关 DMF 信息"),
    ]
    cases.extend(
        _failure_case(index, ingredient, query)
        for index, (ingredient, query) in enumerate(failures, 1)
    )
    cases.append(_retry_case())

    documents = [
        ("confirm", "Ibuprofen"),
        ("confirm", "Metformin"),
        ("edit", "Amoxicillin"),
        ("reject", "Omeprazole"),
        ("confirm", "Lidocaine"),
    ]
    cases.extend(
        _document_case(index, action, ingredient)
        for index, (action, ingredient) in enumerate(documents, 1)
    )
    return cases
