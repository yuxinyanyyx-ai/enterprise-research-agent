from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from openai import APIConnectionError, APITimeoutError, BadRequestError

from src.llm import apollo
from src.observability import tracing
from src.observability.tracing import PriceRule, calculate_cost, trace_span
from src.tools.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk


@pytest.fixture
def trace_directory(tmp_path, monkeypatch):
    with tracing._lock:
        if tracing._handler is not None:
            tracing._handler.close()
        tracing._handler = None
        tracing._handler_path = None
        tracing._warned = False
    monkeypatch.setattr(tracing, "TRACE_DIR", tmp_path / "traces")
    monkeypatch.setattr(tracing, "_price_rules", ())
    yield tmp_path / "traces"
    with tracing._lock:
        if tracing._handler is not None:
            tracing._handler.close()
        tracing._handler = None
        tracing._handler_path = None


def _spans(directory):
    return [
        json.loads(line)
        for path in directory.glob("*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


def test_nested_spans_share_trace_and_keep_parent_links(trace_directory):
    with trace_span("request", "agent", request_id="req-1", trace_id="trace-1") as root:
        with trace_span("tool", "agent.tool") as tool:
            with trace_span("apollo.invoke", "apollo.llm", model_name="model-a") as child:
                child.set_usage(input_tokens=11, output_tokens=4, source="actual")

    spans = _spans(trace_directory)
    by_name = {span["operation_name"]: span for span in spans}
    assert by_name["request"]["parent_span_id"] is None
    assert by_name["tool"]["parent_span_id"] == root.span_id
    assert by_name["apollo.invoke"]["parent_span_id"] == tool.span_id
    assert {span["trace_id"] for span in spans} == {"trace-1"}
    assert {span["request_id"] for span in spans} == {"req-1"}
    assert by_name["apollo.invoke"]["total_tokens"] == 15


def test_apollo_retry_usage_and_failure_are_recorded_once(trace_directory, monkeypatch):
    monkeypatch.setattr(apollo.time, "sleep", lambda _: None)

    class RetryingClient:
        model_name = "model-a"

        def __init__(self):
            self.calls = 0

        def invoke(self, _messages):
            self.calls += 1
            if self.calls == 1:
                raise APIConnectionError(
                    message="private request text",
                    request=httpx.Request("POST", "https://apollo.example/chat"),
                )
            return AIMessage(
                content="ok",
                usage_metadata={"input_tokens": 19, "output_tokens": 3, "total_tokens": 22},
            )

    client = RetryingClient()
    with trace_span("request", "agent", request_id="req-2", trace_id="trace-2"):
        response = apollo.invoke_with_gateway_retries(
            client,
            [HumanMessage(content="private prompt data")],
            retries=1,
        )

    assert response.content == "ok"
    assert client.calls == 2
    spans = _spans(trace_directory)
    model_spans = [span for span in spans if span["component"] == "apollo.llm"]
    assert len(model_spans) == 1
    assert model_spans[0]["retry_count"] == 1
    assert model_spans[0]["metadata"]["attempt_count"] == 2
    assert model_spans[0]["token_usage_source"] == "actual"
    assert model_spans[0]["input_tokens"] == 19
    assert model_spans[0]["output_tokens"] == 3
    assert sum(span["input_tokens"] is not None for span in spans) == 1
    assert "private prompt data" not in json.dumps(spans)
    assert "private request text" not in json.dumps(spans)


@pytest.mark.parametrize("code", [
    "context_length_exceeded", "private-code-with-document-content", {"private": "value"},
])
def test_invalid_request_records_only_safe_diagnostics(trace_directory, code):
    class InvalidClient:
        calls = 0

        def invoke(self, _messages):
            self.calls += 1
            raise BadRequestError(
                "private-response-body",
                response=httpx.Response(
                    400, request=httpx.Request("POST", "https://apollo.example/chat"),
                ),
                body={"code": code, "message": "private-document-text"},
            )

    client = InvalidClient()
    with pytest.raises(BadRequestError):
        apollo.invoke_with_gateway_retries(client, [], retries=2)
    assert client.calls == 1
    span = _spans(trace_directory)[0]
    assert span["status"] == "error"
    assert span["metadata"]["http_status"] == 400
    assert span["retry_count"] == 0
    assert span["token_usage_source"] == "missing"
    assert span["input_tokens"] is None
    if code == "context_length_exceeded":
        assert span["metadata"]["provider_error_code"] == code
    else:
        assert "provider_error_code" not in span["metadata"]
    assert "private" not in json.dumps(span)


def test_budget_rejection_traces_estimate_without_api_usage(trace_directory, monkeypatch):
    from src.agent.react_nodes import build_react_agent_node

    monkeypatch.setenv("AGENT_CONTEXT_WINDOW_TOKENS", "3000")
    monkeypatch.setenv("AGENT_CONTEXT_OUTPUT_TOKENS", "500")
    monkeypatch.setenv("AGENT_CONTEXT_SAFETY_TOKENS", "500")
    question = "private-business-question " * 3000

    def no_model():
        raise AssertionError("Rejected input must not create a model")

    with trace_span("request", "agent", request_id="budget-request") as root:
        result = build_react_agent_node(
            llm_factory=no_model, registry=ToolRegistry(),
        )({"react_messages": [HumanMessage(content=question)]})
    assert result["failure_code"] == "context_budget_exceeded"
    spans = _spans(trace_directory)
    rejected = next(span for span in spans if span["operation_name"] == "context.rejected")
    assert rejected["trace_id"] == root.trace_id
    assert rejected["parent_span_id"] == root.span_id
    assert rejected["status"] == "rejected"
    assert rejected["metadata"]["input_budget_tokens"] == 2000
    assert rejected["input_tokens"] > 2000
    assert rejected["token_usage_source"] == "estimated"
    assert rejected["output_tokens"] is None
    assert rejected["cost_amount"] is None
    assert not any(span["component"] == "apollo.llm" for span in spans)
    assert "private-business-question" not in json.dumps(spans)


def test_failed_and_cancelled_spans_preserve_status(trace_directory, monkeypatch):
    monkeypatch.setattr(apollo.time, "sleep", lambda _: None)

    class FailingClient:
        def invoke(self, _messages):
            raise APIConnectionError(
                message="timeout",
                request=httpx.Request("POST", "https://apollo.example/chat"),
            )

    with pytest.raises(APIConnectionError):
        apollo.invoke_with_gateway_retries(FailingClient(), [], retries=1)
    with pytest.raises(asyncio.CancelledError):
        with trace_span("cancelled", "agent"):
            raise asyncio.CancelledError()

    spans = _spans(trace_directory)
    failed = next(span for span in spans if span["operation_name"] == "llm.invoke")
    cancelled = next(span for span in spans if span["operation_name"] == "cancelled")
    assert failed["status"] == "error"
    assert failed["error_type"] == "APIConnectionError"
    assert failed["retry_count"] == 1
    assert failed["token_usage_source"] == "missing"
    assert failed["input_tokens"] is None
    assert cancelled["status"] == "cancelled"
    assert cancelled["error_type"] == "CancelledError"


def test_timeout_is_recorded_as_failure(trace_directory):
    class TimedOutClient:
        def invoke(self, _messages):
            raise APITimeoutError(
                request=httpx.Request("POST", "https://apollo.example/chat")
            )

    with pytest.raises(APITimeoutError):
        apollo.invoke_with_gateway_retries(TimedOutClient(), [], retries=0)

    span = next(span for span in _spans(trace_directory) if span["operation_name"] == "llm.invoke")
    assert span["status"] == "error"
    assert span["error_type"] == "APITimeoutError"
    assert span["retry_count"] == 0


def test_embedding_and_rerank_record_actual_usage_without_payloads(trace_directory, monkeypatch):
    monkeypatch.setenv("APOLLO_BASE_URL", "https://apollo.example/v1")
    monkeypatch.setenv("APOLLO_EMBEDDING_MODEL", "embedding-a")
    monkeypatch.setenv("APOLLO_RERANK_MODEL", "rerank-a")

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_post(url, **_kwargs):
        if url.endswith("/embeddings"):
            return Response({
                "data": [{"index": 0, "embedding": [0.1, 0.2]}],
                "usage": {"prompt_tokens": 5, "total_tokens": 5},
            })
        return Response({
            "results": [{"index": 0, "relevance_score": 0.9}],
            "usage": {"input_tokens": 10, "total_tokens": 10},
        })

    monkeypatch.setattr(apollo.requests, "post", fake_post)
    apollo.embed_texts(["private document text"], access_token="secret-token")
    apollo.rerank_documents(
        "private query", ["private candidate"], top_n=1, access_token="secret-token"
    )

    spans = _spans(trace_directory)
    embedding = next(span for span in spans if span["component"] == "apollo.embedding")
    rerank = next(span for span in spans if span["component"] == "apollo.rerank")
    assert embedding["input_tokens"] == 5
    assert embedding["output_tokens"] is None
    assert embedding["token_usage_source"] == "actual"
    assert embedding["metadata"]["vector_dimension"] == 2
    assert rerank["input_tokens"] == 10
    assert rerank["output_tokens"] is None
    assert rerank["token_usage_source"] == "actual"
    assert rerank["metadata"]["result_count"] == 1
    serialized = json.dumps(spans)
    assert "private document text" not in serialized
    assert "private query" not in serialized
    assert "secret-token" not in serialized


def test_cost_requires_model_specific_prices_and_known_usage():
    llm_rule = PriceRule(
        "model-a", Decimal("2"), Decimal("4"), "USD", "actual"
    )
    actual = calculate_cost(
        model_name="model-a",
        input_tokens=1_000_000,
        output_tokens=500_000,
        token_usage_source="actual",
        price_rules=(llm_rule,),
    )
    estimated = calculate_cost(
        model_name="model-a",
        input_tokens=1_000_000,
        output_tokens=500_000,
        token_usage_source="estimated",
        price_rules=(llm_rule,),
    )
    missing = calculate_cost(
        model_name="model-a",
        input_tokens=10,
        output_tokens=2,
        token_usage_source="missing",
        price_rules=(llm_rule,),
    )
    embedding_rule = PriceRule(
        "embedding-a", Decimal("1"), None, "USD", "actual", output_billed=False
    )
    embedding = calculate_cost(
        model_name="embedding-a",
        input_tokens=1_000,
        output_tokens=None,
        token_usage_source="actual",
        price_rules=(embedding_rule,),
    )
    assert actual.amount == Decimal("4")
    assert actual.source == "actual"
    assert estimated.amount == Decimal("4")
    assert estimated.source == "estimated"
    assert missing.amount is None and missing.source == "missing"
    assert embedding.amount == Decimal("0.001")
    assert embedding.source == "actual"


def test_sensitive_metadata_and_sink_failure_do_not_affect_work(trace_directory, tmp_path, monkeypatch):
    with trace_span(
        "safe",
        "test",
        metadata={
            "prompt": "private prompt",
            "authorization": "private credential",
            "tool_name": "safe_tool",
        },
    ):
        pass
    assert "private prompt" not in json.dumps(_spans(trace_directory))
    assert "private credential" not in json.dumps(_spans(trace_directory))

    blocked = tmp_path / "not-a-directory"
    blocked.write_text("blocked", encoding="utf-8")
    monkeypatch.setattr(tracing, "TRACE_DIR", blocked)
    with trace_span("continues", "test") as span:
        span.set_usage(input_tokens=1, source="estimated")
    assert span.status == "success"


def test_concurrent_contexts_never_cross_link(trace_directory):
    barrier = Barrier(4)

    def trace_one(index):
        request_id = f"request-{index}"
        trace_id = f"trace-{index}"
        with trace_span("request", "agent", request_id=request_id, trace_id=trace_id) as root:
            barrier.wait(timeout=5)
            with trace_span("child", "agent.tool") as child:
                return root.span_id, child.span_id, trace_id, request_id

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(trace_one, range(4)))

    spans = _spans(trace_directory)
    for root_id, child_id, trace_id, request_id in results:
        root = next(span for span in spans if span["span_id"] == root_id)
        child = next(span for span in spans if span["span_id"] == child_id)
        assert root["trace_id"] == child["trace_id"] == trace_id
        assert root["request_id"] == child["request_id"] == request_id
        assert child["parent_span_id"] == root_id


def test_parallel_tools_inherit_request_trace(trace_directory, monkeypatch):
    from langchain_core.messages import AIMessage
    from src.agent import audit_log, tooling
    from src.agent.tooling import execute_tools

    monkeypatch.setattr(audit_log, "write_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tooling, "store_artifact", lambda *_args, **_kwargs: None)

    @tool("trace_tool_a")
    def trace_tool_a() -> dict:
        """Return one safe local result."""
        return {"success": True}

    @tool("trace_tool_b")
    def trace_tool_b() -> dict:
        """Return another safe local result."""
        return {"success": True}

    registry = ToolRegistry()
    for local_tool in (trace_tool_a, trace_tool_b):
        registry.register(ToolDefinition(
            tool=local_tool,
            risk=ToolRisk.READ_ONLY,
            contexts=frozenset({ToolContext.GENERAL}),
            parallel_safe=True,
        ))

    calls = [
        {"name": name, "id": f"call-{index}", "args": {}, "type": "tool_call"}
        for index, name in enumerate(("trace_tool_a", "trace_tool_b"))
    ]
    with trace_span("request", "agent", request_id="parallel-request", trace_id="parallel-trace") as root:
        execute_tools({
            "request_id": "parallel-request",
            "trace_id": "parallel-trace",
            "root_span_id": root.span_id,
            "messages": [AIMessage(content="", tool_calls=calls)],
        }, registry=registry)

    tool_spans = [
        span for span in _spans(trace_directory)
        if span["component"] == "agent.tool"
    ]
    assert len(tool_spans) == 2
    assert {span["trace_id"] for span in tool_spans} == {"parallel-trace"}
    assert {span["request_id"] for span in tool_spans} == {"parallel-request"}
    assert {span["parent_span_id"] for span in tool_spans} == {root.span_id}


@pytest.mark.parametrize("failed_backend", [None, "chunks"])
def test_pec_unified_retrieval_keeps_request_trace(
    trace_directory, monkeypatch, failed_backend,
):
    from src.agent import audit_log, tooling
    from src.agent.tooling import execute_tools
    from src.pec import knowledge_service
    from src.pec.knowledge import chunk_index
    from src.pec.tools.tools import register_pec_tools

    monkeypatch.setattr(audit_log, "write_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tooling, "store_artifact", lambda *_args, **_kwargs: None)
    monkeypatch.setenv("APOLLO_BASE_URL", "https://apollo.example/v1")
    monkeypatch.setenv("APOLLO_EMBEDDING_MODEL", "embedding-a")
    monkeypatch.setenv("APOLLO_RERANK_MODEL", "rerank-a")
    barrier = Barrier(2)

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def post(url, **_kwargs):
        if url.endswith("/embeddings"):
            if failed_backend == "chunks":
                raise RuntimeError("private embedding failure")
            return Response({
                "data": [{"index": 0, "embedding": [0.1, 0.2]}],
                "usage": {"prompt_tokens": 5, "total_tokens": 5},
            })
        return Response({"results": [{"index": 0, "relevance_score": 0.9}]})

    def chunks(query, **_kwargs):
        barrier.wait(timeout=10)
        apollo.embed_texts([query], access_token="private-token")
        apollo.rerank_documents(
            query, ["private evidence"], top_n=1, access_token="private-token",
        )
        return []

    monkeypatch.setattr(apollo.requests, "post", post)
    monkeypatch.setattr(chunk_index, "_search_chunks_impl", chunks)
    assert not hasattr(knowledge_service, "search_topics")
    registry = ToolRegistry()
    register_pec_tools(registry)

    def request(index):
        request_id = f"pec-request-{index}"
        trace_id = f"pec-trace-{index}"
        with trace_span("request", "agent", request_id=request_id, trace_id=trace_id) as root:
            result = execute_tools({
                "request_id": request_id,
                "trace_id": trace_id,
                "root_span_id": root.span_id,
                "messages": [AIMessage(content="", tool_calls=[{
                    "name": "search_pec_knowledge",
                    "id": f"pec-call-{index}",
                    "args": {"query": f"private query {index}"},
                    "type": "tool_call",
                }])],
            }, registry=registry)
            return root, result

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(request, range(2)))

    spans = _spans(trace_directory)
    assert {span["trace_id"] for span in spans} == {"pec-trace-0", "pec-trace-1"}
    for root, result in results:
        business_result = result["tool_artifacts"][0]["result"]
        assert business_result["success"] is (failed_backend is None)
        assert business_result["partial"] is False
        assert business_result["topics"]["enabled"] is False
        branch = [span for span in spans if span["trace_id"] == root.trace_id]
        assert {span["request_id"] for span in branch} == {root.request_id}
        tool_span = next(span for span in branch if span["component"] == "agent.tool")
        retrieval = next(span for span in branch if span["operation_name"] == "retrieval.search_chunks")
        embedding = next(span for span in branch if span["component"] == "apollo.embedding")
        assert not any(span["component"] == "apollo.llm" for span in branch)
        assert tool_span["parent_span_id"] == root.span_id
        assert retrieval["parent_span_id"] == tool_span["span_id"]
        assert embedding["parent_span_id"] == retrieval["span_id"]
        assert retrieval["status"] == ("error" if failed_backend == "chunks" else "success")
        assert embedding["status"] == retrieval["status"]
        assert tool_span["input_tokens"] is None
        assert retrieval["input_tokens"] is None
        if failed_backend != "chunks":
            rerank = next(span for span in branch if span["component"] == "apollo.rerank")
            assert rerank["parent_span_id"] == retrieval["span_id"]
            assert embedding["input_tokens"] == 5
            assert rerank["token_usage_source"] == "missing"
            assert rerank["input_tokens"] is None
    serialized = json.dumps(spans)
    for secret in ("private query", "private-token", "private evidence", "private embedding failure", "private topic failure"):
        assert secret not in serialized
    assert tracing.current_trace_context() is None
