from langchain_core.messages import AIMessage, SystemMessage

from src.agent.react_graph import build_react_graph
from src.agent.skills import build_system_prompt, load_skill_catalog
from src.tools.registry import ToolContext, load_builtin_tools


def _call(name: str, call_id: str, **args):
    return AIMessage(content="", tool_calls=[{
        "name": name,
        "id": call_id,
        "args": args,
        "type": "tool_call",
    }])


def test_skill_catalog_loads_all_current_skills_and_registered_tools():
    catalog = load_skill_catalog()
    assert {skill.id for skill in catalog.skills} == {
        "knowledge",
        "ppt-structure",
        "ppt-project-slide",
    }
    tools = {
        item.tool.name
        for item in load_builtin_tools().for_context(ToolContext.GENERAL)
    }
    catalog.validate_tools(tools)


def test_skill_preselection_handles_explicit_names_and_distinct_ppt_tasks():
    catalog = load_skill_catalog()
    assert catalog.select_for_message("PEC 会议中有哪些决策？") == ("knowledge",)
    assert catalog.select_for_message("请把这份PPT结构化，提取Status和Mitigation") == (
        "ppt-structure",
    )
    assert catalog.select_for_message("请生成 Project Slide 第1页") == (
        "ppt-project-slide",
    )
    assert catalog.select_for_message("你好，请按 $ppt-project-slide 处理") == (
        "ppt-project-slide",
    )
    assert catalog.select_for_message("$knowledge 同时生成 Project Slide 第1页") == (
        "knowledge",
        "ppt-project-slide",
    )


def test_only_selected_skill_instructions_are_injected():
    catalog = load_skill_catalog()
    prompt = build_system_prompt(["ppt-structure"])
    assert "## Skill: ppt-structure" in prompt
    assert "## Skill: knowledge" not in prompt
    assert "## Skill: ppt-project-slide" not in prompt
    assert "load_skill" in prompt
    assert "## Active skills" not in catalog.catalog_block()


def test_agent_can_load_a_missed_skill_into_trusted_system_context(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_ARTIFACT_DIR", str(tmp_path))
    invocations = []

    class Model:
        def bind_tools(self, tools):
            self.tool_names = {tool.name for tool in tools}
            return self

        def invoke(self, messages):
            invocations.append(messages)
            system = "\n".join(
                message.content for message in messages
                if isinstance(message, SystemMessage)
            )
            if len(invocations) == 1:
                assert "## Skill: ppt-structure" not in system
                assert "ppt-structure" in system
                assert "load_skill" in self.tool_names
                return _call("load_skill", "load-1", skill_id="ppt-structure")
            assert "## Skill: ppt-structure" in system
            return AIMessage(content="已按结构化指引处理。")

    result = build_react_graph(llm_factory=Model).invoke({
        "user_query": "请整理上传资料，输出清晰的状态和风险表格。",
    })

    assert result["active_skill_ids"] == ["ppt-structure"]
    assert [item["tool_name"] for item in result["tool_artifacts"]] == ["load_skill"]
    assert result["final_answer"] == "已按结构化指引处理。"


def test_skill_tool_exposes_only_known_ids():
    tool = load_builtin_tools().get("load_skill").tool
    assert set(tool.args) == {"skill_id"}
    response = tool.invoke({"skill_id": "ppt-project-slide"})
    assert response == {"success": True, "skill_id": "ppt-project-slide"}
