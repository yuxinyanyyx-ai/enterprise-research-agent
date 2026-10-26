# PEC RAG Benchmark

This is an opt-in, standalone evaluator for the currently published PEC index. It reads the
published chunk and embedding snapshot and calls the existing Apollo Embedding, Rerank and LLM
interfaces. It does not alter the Agent, the production retrieval defaults, or index contents.
No request to Apollo is made by pytest or by importing the benchmark package.

## Environment and preflight

Use the project Python environment and configure the same Apollo variables as the application.
Preflight prints index generation metadata and only sanitized service status; it does not print
credentials or response bodies. The command deliberately makes one small request to each Apollo
model API:

```powershell
C:\BITrusted\work\python.exe -m src.pec.benchmark preflight --live --max-calls 3
```

All API calls in this module require the explicit `--live` option. If embedding, rerank, or LLM
preflight fails, do not interpret a partial run as a successful ablation. A published index must
contain embeddings made by the configured embedding model. The report records both the `CURRENT`
generation directory (the immutable index identity) and the manifest's generation field; a
mismatch is surfaced in the report rather than silently reconciled.

## Generate and review candidate cases

The curated working dataset is `data\pec\benchmark\candidate_cases.jsonl`, with its
case-by-case checklist in `data\pec\benchmark\candidate_review_checklist.md`. The 30-case
Apollo-generated snapshot is preserved separately as `candidate_cases.generated.jsonl`.
After source and reasoning checks, 24 candidates remain for human review; six weak or duplicate
items are retained in `candidate_cases.withheld.jsonl` and must not be evaluated. The 24 retained
cases were individually confirmed by the user as manually reviewed and are recorded as `approved`;
the six withheld cases remain excluded. The original generated snapshot is not ground truth.

The active index currently covers only 11 PEC December 2025 files. The retained candidates cite
10 of those files, but cannot yet expand benchmark meeting/year coverage. Do not use the older
root-level index as though it were active. Re-index and publish the other local PEC materials
before adding cases from them. The source-review report reclassified the local/global regulatory
recommendation disagreement as a cross-document `recommendation_disagreement` subtype, not a
factual-conflict positive. The three duplicated Zongertinib conflict proposals are withheld.
Unanswerable-case claims are restricted to the uploaded December 2025 source files and their
extractable text; they do not establish absence in images, scans, or sources outside that fixed set.

Candidate generation normally makes six real Apollo LLM calls (one per question category); the
required seven-call cap reserves one evidence-validation
repair attempt. It requires an
explicit call budget. The output is always marked `pending_review`; model-proposed answers,
citations, conflict labels, and no-answer labels are not ground truth:

```powershell
C:\BITrusted\work\python.exe -m src.pec.benchmark generate `
  --live --max-calls 7 `
  --output data\pec\benchmark\candidate_cases.generated.jsonl
```

If a category fails strict evidence validation, successful categories remain in a pending-only
partial JSONL. Resume against the same index generation with a new explicit budget:

```powershell
C:\BITrusted\work\python.exe -m src.pec.benchmark generate `
  --live --resume --max-calls 7 `
  --output data\pec\benchmark\candidate_cases.generated.jsonl
```

The partial file cannot be evaluated and is discarded only after all 30 categories and source IDs
pass validation.

The generator uses only visible evidence chunks from the pinned published index, checks that cited
IDs and source paths exist in that evidence bundle, and checks the requested distribution. When
available, it reuses existing cross-document links and structured conflict pairs from the published
Topic index; a conflict note is only an unreviewed prompt hint, not a verified conclusion:

| Type | Count |
|---|---:|
| Fact | 8 |
| Cross-document | 6 |
| Insight | 6 |
| Historical change | 4 |
| Evidence conflict | 3 |
| Unanswerable | 3 |

In addition to the four requested retrieval strategies, each Vector run reports a paired raw-only
versus raw+Topic corpus ablation. Both use the same query embedding, top-N and index generation;
the report includes per-case evidence IDs and the measured Recall@5 delta. This diagnostic does
not add another Apollo request.

The LLM cannot establish that a question is genuinely unanswerable by inspecting a sample of the
index, nor can it certify that evidence is truly conflicting. Those labels and all reference
answers/citations must be checked against the full source material by a person. Review individual
cases with:

```powershell
C:\BITrusted\work\python.exe -m src.pec.benchmark review `
  --dataset data\pec\benchmark\candidate_cases.jsonl `
  --case-id PEC-FACT-001 --status approved `
  --reviewer "reviewer-name" --note "Checked answer and quoted page against the source."
```

For conflict and no-answer cases, approval additionally requires an explicit manual label
confirmation:

```powershell
C:\BITrusted\work\python.exe -m src.pec.benchmark review `
  --dataset data\pec\benchmark\candidate_cases.jsonl `
  --case-id PEC-EVIDENCE_CONFLICT-025 --status approved `
  --reviewer "reviewer-name" --note "Compared both cited source pages." `
  --confirm-answerability "Confirmed both source positions conflict."
```

Use the exact case IDs returned in the JSONL. Only `approved` records with reviewer metadata and
valid source/evidence mappings can enter an official run. Editing an approved record changes the
dataset SHA-256 and therefore its reported dataset version.

## Run the retrieval ablation

At least ten Apollo requests per selected case are budgeted for a retrieval-only comparison:
Vector (1 embedding), Hybrid (1 embedding), Hybrid + Rerank (1 embedding + 1 rerank), and iterative
Hybrid + Rerank (first pass, one LLM gap check, up to two targeted embeddings, and a second rerank).
The evaluator rejects a budget too small to run all four strategies on every selected case.

```powershell
C:\BITrusted\work\python.exe -m src.pec.benchmark run `
  --live --dataset data\pec\benchmark\candidate_cases.jsonl `
  --max-cases 1 --max-calls 10 --retrieval-only
```

Use repeated `--question-type` options to filter. The default run evaluates the answer path too;
budget up to 18 requests per case (the four retrieval strategies plus Apollo answer generation and
model-assisted judging per strategy):

```powershell
C:\BITrusted\work\python.exe -m src.pec.benchmark run `
  --live --dataset data\pec\benchmark\candidate_cases.jsonl `
  --max-cases 1 --max-calls 18
```

`--retry-failures <report.json>` selects only case IDs that failed in a previous report. Reports
are written under `outputs\pec-rag-benchmark\<run-id>\report.json` and `report.md`. Each report
contains the commit, dataset digest, immutable index generation, model names, retrieval budget,
per-case source/evidence hits, automatic answer evaluations, call counts, token usage where
reported by Apollo, latency, and sanitized failure types. Embedding/Rerank token use and price are
`null` when the gateway does not expose them; the evaluator does not estimate them. Human evaluation
is a separate field and starts as `not_started`.

Record human answer audits separately from the automatic judge:

```powershell
C:\BITrusted\work\python.exe -m src.pec.benchmark audit `
  --report outputs\pec-rag-benchmark\<run-id>\report.json `
  --case-id PEC-FACT-001 --strategy hybrid_rerank `
  --auditor "reviewer-name" --note "Verified cited page and answer." `
  --answer-correctness 1 --faithfulness 1 --citation-precision 1 `
  --correct-refusal false --conflict-identified false
```

The command updates both report files and keeps human scores in a distinct section.

Compare two JSON reports:

```powershell
C:\BITrusted\work\python.exe -m src.pec.benchmark compare `
  outputs\pec-rag-benchmark\<first>\report.json `
  outputs\pec-rag-benchmark\<second>\report.json
```

The comparison reports dataset/index identity matches and metric deltas. It does not hide failed
cases or present an unreviewed candidate dataset as benchmark results.

## Metrics and interpretation

Retrieval Recall@5/10 counts expected evidence IDs retrieved divided by all expected IDs;
unanswerable cases have retrieval metrics `null` and are evaluated by refusal behavior. MRR is the
reciprocal rank of the first expected evidence ID, and Hit Rate indicates whether any expected
evidence was returned. P50/P95 use nearest-rank percentiles across successful case searches.

Answer Correctness, Faithfulness, Citation Precision, refusal, and conflict recognition are
reported as **automatic LLM-judge** results. They are not human scores. Inspect citations and
source pages, especially for conflicts and abstentions, and record a separate manual audit before
using the results as a quality claim. A judge cannot repair incorrect ground truth.

## Offline regression

The benchmark unit tests use fixed tiny in-memory fixtures and injected local call functions only
to verify the evaluator. This is not a benchmark dataset or a source of quality scores:

```powershell
C:\BITrusted\work\python.exe -m pytest tests\test_pec_rag_benchmark.py -q
```
