# Agent Context Budget

The outer ReAct agent builds a bounded model-input view. It does not delete
checkpoint messages or replace complete business results with previews.
Document/watchlist intent parsing applies the same estimated input budget.

## Configuration

| Environment variable | Default | Meaning |
| --- | --- | --- |
| `AGENT_CONTEXT_WINDOW_TOKENS` | `16000` | Application context allowance, not detected model capacity |
| `AGENT_CONTEXT_OUTPUT_TOKENS` | `2048` | Capacity reserved for output |
| `AGENT_CONTEXT_SAFETY_TOKENS` | `2048` | Capacity reserved for estimation/protocol error |

The default estimated input allowance is 11,904 tokens. Positive input capacity
is required. Configure these values for the actual Apollo deployment. The output
reservation is a budgeting allowance; it does not set the provider's generation
limit. Align it with the provider's configured maximum output.

The estimator counts ASCII alphanumeric runs at roughly three characters per
token, ASCII punctuation separately, and non-ASCII characters by UTF-8 byte
length. It includes system prompts, message metadata, tool-call arguments and
tool/structured-output schemas. This is deliberately cautious for Chinese but
is not an exact tokenizer or a mathematical upper bound for every model.

## Invariants

- The current user message stays verbatim, including whitespace. It is never
  silently truncated or replaced by a summary.
- Current-turn tool calls keep their IDs, arguments, order and corresponding
  tool results. Every tool result and required business context is sent in full
  after recursive sensitive-key filtering, or the model call is rejected.
- Older complete conversational turns are included newest-first until the
  remaining budget is exhausted. Historical tool calls/results are excluded;
  retained user/assistant text is explicitly labelled as historical.
- The builder does not shorten strings, cap lists or dictionary keys, limit
  nesting depth, reorder evidence, or create preview/truncation markers. Exact
  sensitive keys such as `raw_payload`, credentials and internal source paths
  are removed without mutating checkpoint state. Arbitrary string text is not
  redacted by this key filter.
- The business snapshot keeps `result_source` and `active_result_id`. Its active
  result body is omitted only when a trusted current-turn tool message supplies
  the same complete safe result. Counts, result IDs, partial artifact reads or
  untrusted messages cannot establish this equivalence. On a new turn, the full
  safe active result is restored from checkpoint because historical tools are
  not sent. Source labels alone are not an authorization/security boundary.
- Complete `dmf_results` remain in state for export and cross-turn reuse. Export
  can continue only when required model input fits; an oversized result stops
  further model-driven export rather than substituting a preview.
- Domain intent parsing preserves the current query and required business
  parameters intact, including pending notification addresses. Only its recent
  conversation is budget-trimmed. Oversize required parameters trigger clarification.
- If required current input still exceeds the estimated allowance, no model call
  is made. The agent returns a request-to-shorten message. A tool that has already
  completed before this check is not rolled back or automatically replayed.

## Artifact Publication And Recovery

`ToolDefinition.result_strategy` controls presentation, not authorization.
`artifact` results remain complete. Explicit trusted references are sent in the
existing Business Context, not inserted into tool-result bodies. Results never
become a directory as a budget fallback.
`inline_compact` results remain exactly as returned after safe filtering and do
not publish reader references. References and available schemas count toward the
same input budget; overflow prevents publication and any subsequent model call.
Stored artifacts contain the complete tool return value, not data discarded by
the tool itself. In particular, PEC evidence discarded before the service returns
cannot be recovered from its artifact.

PEC results use a 20,000-byte UTF-8 JSON budget, measured with the same
`json.dumps(result, ensure_ascii=False, default=str)` serialization as the tool
message. Only `context_summary` is returned; the duplicate `context` field and
query echo are removed. The summary is a hit-count overview, not generated prose.
Evidence retains source, location, type, extraction provenance and text; verbose
extraction metadata is omitted from the inline view.

There is no fixed 600-character text limit or eight-item limit. Whole evidence
items are selected in retrieval order while they fit. An oversized first item
can have its text shortened with `complete=false` and `truncated=true`; oversized
source metadata is never rewritten to create a misleading citation.
`evidence_count` counts all built evidence items, `returned_evidence_count` counts
the included items, and `truncated_evidence_count` counts omitted items (not the
number of shortened texts). Error responses are bounded and omit backend details.
The builder does not apply a second evidence limit. The global token budget may
reject a complete PEC return value; 20KB is not a provider token allowance and
does not change the model configuration. These service-owned limits are unchanged.

PEC registration removes its search tool after a successful hit, including a
truncated hit. Empty results permit at most one supplemental search. This is a
provider-owned availability policy, not a tool-name rule in the budget layer.
Other failure adapters retain their existing behavior.

The budget builder only filters data, validates tool pairs, checks the complete
input budget, and selects historical turns. It has no Artifact publication or
tool-body rewriting logic. The outer node uses existing State and stored records
to put verified minimal references in the Business Context `artifact_refs` list.
Backend metadata (`ToolMessage.additional_kwargs`) is not publication to the
model. References must match current-request stored tool-call records and a
current message containing the authentic complete safe source result. Tool
bodies remain unchanged except for sensitive-key filtering. Publication is
persisted only after successful model invocation. Available tools are selected
once with the verified reference set, and their schemas are included in the same
budget check before binding and invocation. No clipping or automatic read fallback
occurs. There are no additional persistent context fields or caches.

`published_artifact_refs`, `artifact_reads`, and `artifact_read_disabled` are reset
on each new request, including checkpoint reuse. Reader authorization verifies
the publication and storage record before IO; storage still verifies request
ownership. A result strategy alone never grants access.

Expected reader errors return a paired error result and disable further reads for
the request. The model can answer from existing evidence or report missing facts.
Unknown execution defects and other tools retain their normal failure policy.
No empty tool binding is used to force a final answer, and tool-call/result IDs
remain paired. Existing total-round and duplicate-call guards still apply.

## Observability And Limits

`context.prepared` audit events record estimated input tokens, configured input
budget, original message count and sent message count. `context.rejected` records
an input-budget rejection. These events do not log prompts or result payloads.

This change bounds model input, not checkpoint storage growth. Long-lived thread
retention/compaction remains separate work. No exact Apollo tokenizer is used,
and provider context-limit errors are not automatically retried. Live model
capacity and estimation accuracy need validation against the deployed model.

## Tests

```powershell
python -m pytest -q tests/test_agent_context_budget.py tests/test_agent_context_eval.py tests/test_agent_eval_cases.py
```

Coverage includes 30-turn history, Chinese/English oversize queries, tool schema
and argument/reference budgets, full error results or rejection, complete deep
structures and many documents/conditions, same-turn deduplication, cross-turn
recovery, in-budget full export and 1,000-record same/cross-turn rejection,
authorized artifact full/directory/page reading, long-history document resume,
and report privacy. Existing small-result DMF and workflow cases remain regression
checks. No token allowances are increased to make these cases pass.

Deterministic YAML expectations can use `llm_inputs`:

```yaml
expected:
  llm_inputs:
  - call_index: 1
    call_type: text
    max_estimated_tokens: 11904
    current_query_verbatim: true
    tool_pairs: true
    message_types: [system, human, ai, tool, system]
    contains: [result_source]
    not_contains: [raw_payload]
```

Call indices are zero-based within the current turn, including structured calls.
Optional `max_messages` and `max_chars` limits are also supported. Token checks
include the recorded tool schemas. For a resume turn without a new user query,
assert the original request via `contains` or a Python exact-message assertion.
Input assertions run only in deterministic mode; live wrappers do not retain
raw prompts. Reports continue to omit input messages and tool schemas.