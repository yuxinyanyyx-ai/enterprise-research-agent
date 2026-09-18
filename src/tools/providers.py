"""Explicit composition root for built-in capabilities."""

from src.tools.dmf_tools import register_dmf_tools
from src.tools.export_tools import register_export_tools
from src.tools.registry import ToolProvider
from src.tools.workflow_tools import register_workflow_tools
from src.pec.tools import register_pec_tools

BUILTIN_PROVIDERS: tuple[ToolProvider, ...] = (
    register_dmf_tools,
    register_export_tools,
    register_workflow_tools,
    register_pec_tools,
)