from types import SimpleNamespace

from langgraph.types import Command


class DomainHarness:
    def __init__(self, graph):
        self.graph = graph
        self.payloads = {}

    @staticmethod
    def flatten(value):
        output = value.get("workflow_output") or {}
        result = output.get("result") or {}
        return {**output.get("updates", {}), **result.get("data", {}),
                "final_answer": result.get("message", ""), "workflow_status": result.get("status", ""),
                "needs_clarification": result.get("status") == "needs_clarification",
                **({"__interrupt__": value["__interrupt__"]} if value.get("__interrupt__") else {})}

    def invoke(self, value, config=None):
        key = str((config or {}).get("configurable", {}).get("thread_id", "default"))
        payload = self.payloads.get(key, {})
        if not isinstance(value, Command):
            payload = {**payload, **value, "operation_ledger": {}}
            value = {"workflow_input": payload}
        result = self.flatten(self.graph.invoke(value, config))
        self.payloads[key] = {**payload, **{name: value for name, value in result.items() if name not in {"final_answer", "__interrupt__", "needs_clarification", "workflow_status"}}}
        return result