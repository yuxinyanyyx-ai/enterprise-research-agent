"""Explicit composition root for built-in capabilities."""

from src.tools.dmf_tools import register_dmf_tools
from src.tools.artifact_tools import register_artifact_tools
from src.tools.export_tools import register_export_tools
from src.tools.registry import ToolProvider
from src.tools.workflow_tools import register_workflow_tools
from src.pec.tools import register_pec_tools
from src.pec.tools.index_tools import register_index_tools
from src.pec.tools.ppt_tools import register_ppt_tools
from src.tools.project_slide import register_project_slide_tools
from src.agent.skill_tools import register_skill_tools

BUILTIN_PROVIDERS: tuple[ToolProvider, ...] = (
    register_dmf_tools,
    register_artifact_tools,
    register_export_tools,
    register_workflow_tools,
    register_pec_tools,
    register_index_tools,
    register_project_slide_tools,
    register_ppt_tools,
    register_skill_tools,
)