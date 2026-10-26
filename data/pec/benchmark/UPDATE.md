# PEC RAG Benchmark 更新记录

## 2026-10-25：依据原始文件审核报告修订候选题

### 修改内容

- 对照用户提供的 `PEC_RAG_source_review_report.md` 修订候选数据与逐题审核清单。
- `PEC-CROSS_DOCUMENT-013`：将 CKD GDMT Slide 5 的主要目标修正为量化既存 CKD 患者单药及联合治疗使用率；议程引用移除原文不存在的 `Topic:` 前缀。CKD 预读与两项 VicaEMPA 提案的映射仍待业务人员确认。
- `PEC-EVIDENCE_CONFLICT-031`：保留原 ID 以便审计，改为 cross-document 的 `recommendation_disagreement` 子类；标明不是 factual-conflict 正例。
- `PEC-UNANSWERABLE-028–030`：将结论范围限定为 2025 年 12 月 PEC ZIP 的 11 份原始文件及可提取文本，并明确不覆盖图像、扫描、不可提取内容或固定范围外资料。
- `PEC-UNANSWERABLE-029`：补充 Slide 2 已确认预算落实、但没有披露具体金额的上下文；问题仍只询问具体金额。
- 统一 028–030 的 `answer_support`，确保正向事实、未提供信息和不可外推范围相互区分。
- `PEC-FACT-004` 与 `PEC-INSIGHT-017`：将跨 PDF 表格列的长引用拆分为可单独核验的短引用。
- 为候选添加来源审核状态；同步更新 `candidate_review_checklist.md` 和 `src/pec/knowledge/RAG_BENCHMARK.md`。

### 当前状态

- 候选共 24 条；人工审核状态见下方新记录。
- 类型数：fact 8、cross_document 5、insight 6、historical_change 2、evidence_conflict 0、unanswerable 3。
- 031 不作为事实冲突正例；没有经核验的 factual-conflict 正例。
- 活动索引目录与 manifest generation 的不一致仍待解决，详见 `NEXT_PLAN.md`。

## 2026-10-25：逐题人工批准并启动正式评测

- 用户明确确认 24 条保留候选已逐题人工核验问题、答案、证据与标签，并批准用于正式 Benchmark。
- 24 条记录状态登记为 `approved`；审核者记录为 `User (IDE confirmation)`。3 条无答案题同时记录了明确的 answerability 确认。6 条暂缓题仍未纳入。
- 正式运行配置：24 条、端到端评测、最多 432 次 Apollo 模型请求；另外最多 3 次请求用于服务预检。
- 正式报告结果待运行完成后追加；本条本身不代表已有成绩。
- 索引目录 generation 与 manifest generation 不一致，运行报告必须保留此警告，不得静默修正。

## 2026-10-25：正式 Apollo 评测结果

- Run ID：`94fcc9865af0478d9ff62407ca492bf8`
- 官方运行状态：`official_live`；24 个 approved 样本；端到端答案生成已开启。
- Commit：`add02c6757789df2485cf1ff65f52127dce938fb`
- Dataset SHA-256：`60b2cb61614d698d77d17fd1279e907885f06a7c84bd33bf5e7caf087091d693`
- 评测索引目录 generation：`97760ae1341343db96d449161ea7fc37`；manifest generation：`9f29e50100f44ce288e53116bd185f28`。两者不一致，报告明确标记 `index_generation_metadata_mismatch=true`。

### 检索消融

以下指标是报告中的实测值，但**仅基于答案生成/评分未失败的策略-案例子集**，不是完整 24 题检索指标：

| Strategy | 成功样本 / 24 | 失败数 | Recall@5 | Recall@10 | MRR | Hit Rate | P50 ms | P95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Vector | 12 | 12 | 0.5227 | 0.5682 | 0.3758 | 0.7273 | 1890.000 | 6281.263 |
| Hybrid | 16 | 8 | 0.4231 | 0.4615 | 0.2051 | 0.5385 | 2197.388 | 6242.954 |
| Hybrid + Rerank | 9 | 15 | 0.7222 | 0.8333 | 0.5944 | 0.8889 | 4291.262 | 5481.761 |
| Hybrid + Rerank + Iterative | 11 | 13 | 0.7273 | 0.8409 | 0.6000 | 0.9091 | 13144.410 | 15132.933 |

Raw + Topic 的 Recall@5 为 0.5227；Raw-only 为 0.6136，差值为 **-0.0909**。该 Topic 消融也只覆盖成功子集，不能作为完整数据集结论。

### 自动答案评分（未人工复核）

| Strategy | 自动 Correctness | 自动 Faithfulness | 自动 Citation Precision | 正确拒答率 |
|---|---:|---:|---:|---:|
| Vector | 0.7317 | 0.7158 | 0.5808 | 1.0000 |
| Hybrid | 0.6806 | 0.6575 | 0.5269 | 0.6667 |
| Hybrid + Rerank | 0.9100 | 0.8211 | 0.6322 | n/a |
| Hybrid + Rerank + Iterative | 0.8518 | 0.7809 | 0.6355 | n/a |

- 每策略自动评测成功样本数分别为 12、16、9、11；人工抽样数为 0。自动分数不等于人工 Ground Truth 审核。
- 共 96 个策略-案例组合，其中 48 个在 `answer_generation` 阶段失败：47 个 `ValueError`，1 个 HTTP 500。当前报告只保留异常类型，无法从报告中确定这些 `ValueError` 的具体子原因。
- 评测器在答案生成失败时将整条 case 记为失败并丢弃已完成的检索命中；因此上表 Recall/MRR/Hit Rate 的分母是成功子集，存在选择偏差，不能据此声称达到完整 Benchmark 指标。
- 实际 Apollo 请求：LLM 168、Embedding 127、Rerank 64；LLM 输入 Token 654,458、输出 Token 68,735。Embedding/Rerank Token 与价格不可得，未估算费用。预检另有最多 3 次连通性请求。
- 最好 Recall@5 为 0.7273，低于目标 0.85；最好自动 Citation Precision 为 0.6355，低于目标 0.95。两者均来自不完整子集。

报告文件：

- [`report.md`](../../../outputs/pec-rag-benchmark/20261025T033324402387Z/report.md)
- [`report.json`](../../../outputs/pec-rag-benchmark/20261025T033324402387Z/report.json)

**结论：本次是真实 Apollo 官方运行，但因答案阶段失败和索引 metadata 不一致，成绩只能作为部分诊断结果，不能作为完整、可比较的正式基线。**

### 文件

- `candidate_cases.jsonl`
- `candidate_review_checklist.md`
- `src/pec/knowledge/RAG_BENCHMARK.md`
- `UPDATE.md`
- `NEXT_PLAN.md`
