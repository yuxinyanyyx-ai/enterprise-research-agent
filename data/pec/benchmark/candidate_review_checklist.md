# PEC RAG Benchmark 候选题人工审核清单

## 修订状态与边界

- 保留集：`candidate_cases.jsonl`（24 条）；暂缓集：`candidate_cases.withheld.jsonl`（6 条建议撤回/替换的原题）；原始 Apollo 候选快照：`candidate_cases.generated.jsonl`。24 条保留候选已根据用户于 2026-10-25 的逐题人工核验确认登记为 `approved`；3 条无答案题的标签确认信息也已记录。6 条暂缓题仍不得进入评测。
- 所有参考答案均为草稿，不是 Ground Truth。保留集附原文引用、页/slide/table 位置、evidence ID、支持关系及修订原因。
- 活动索引 generation `97760ae1341343db96d449161ea7fc37` 有 986 chunks、11 个来源文件；保留题引用其中 10 个文件，但全部属于 2025 年 12 月 PEC 会议。当前不能诚实扩展跨会议/年份覆盖。
- 本地 PEC 2024-04、MAP 2025-05、PEC 2026-04 等资料不在活动 manifest；root-level 2024 chunks 不是当前 `CURRENT` generation。需要重新入库并发布新 generation 后再纳入。
- 保留集类型数：fact=8, cross_document=5, insight=6, historical_change=2, evidence_conflict=0, unanswerable=3。
- 已对照 2026-10-25 原始文件审核报告修订来源引用和题目标签；24 条仍全部 `pending_review`。更新记录见 `UPDATE.md`，后续工作见 `NEXT_PLAN.md`。
- `PEC-EVIDENCE_CONFLICT-031` 保留原 ID 以便审计，但现归为 cross-document 的 `recommendation_disagreement` 子类，不是 factual-conflict 正例；仍待人工决定保留或暂缓。025–027 的重复/非冲突题暂缓。

## 保留候选逐题审核

逐题核对原始文件、精确位置、active generation evidence IDs 和引用支持关系；历史题需确认前后状态，跨文档题需确认多来源共同回答同一业务问题，Insight 需区分直接事实与合理推论。

### PEC-FACT-001 — fact — approved

- [ ] 核对问题：2025年12月PEC议程将谁列为会议主席？
- 草稿答案（非 Ground Truth）：直接事实：议程将 Wei Zhang 列为 Head of Medicine，并标注为 Chair。
- 修订原因：收窄为议程可直接核验的职位与姓名。
- 证据：`PEC-Dec 2025/PEC-Dec 2025/00 2025 Dec PEC agenda.docx` — **Table 3 row 2** — `chunk-e6cccc744213`
  - 原文引用：> Chair:
Head of Medicine: Wei Zhang
    - 支持关系：支持主席及姓名
- [ ] 对照原始 DOCX/PDF/PPTX 完成人工核验。

### PEC-FACT-002 — fact — approved

- [ ] 核对问题：2025年12月PEC终稿将 Zongertinib-1L NSCLC NDA 审批时间线的复核安排在何时，并以什么事件为前提？
- 草稿答案（非 Ground Truth）：直接事实：终稿记录在 CFDI inspection 后、约 2026 年 2 月中旬重新审视该 NDA 审批时间线。
- 修订原因：区分复核时间与审批时间；不声称审批将在该日期完成。
- 证据：`PEC-Dec 2025/PEC-Dec 2025/00 2025 Dec PEC meeting minutes - Final.pdf` — **Page 2** — `chunk-12e96f2426ee`
  - 原文引用：> Zongertinib-1L NSCLC: to revisit the NDA approval timeline after CFDI inspection around mid of Feb 2026.
    - 支持关系：支持复核事件及时间
- [ ] 对照原始 DOCX/PDF/PPTX 完成人工核验。

### PEC-FACT-003 — fact — approved

- [ ] 核对问题：PEC 预读将 BI 3802876 描述为哪类分子、结合哪些靶点？
- 草稿答案（非 Ground Truth）：直接事实：材料将 BI 3802876 描述为结合 FAP 与 TRAILR2 的双特异性分子。
- 修订原因：保留直接事实，并用原文引用替换模型改写引用。
- 证据：`PEC-Dec 2025/PEC-Dec 2025/07. CRM_FAP TRAILR2 Asian Phase 1 For PEC endorsement.pptx` — **Slide 3** — `chunk-5192a84b3d7c`
  - 原文引用：> Drug candidate
BI 3802876 is a bispecific molecule binding FAP and TRAILR2.
    - 支持关系：支持分子类型与靶点
- [ ] 对照原始 DOCX/PDF/PPTX 完成人工核验。

### PEC-FACT-004 — fact — approved

- [ ] 核对问题：2025年12月PEC终稿对 FAP/TRAILR2 MASH 肝硬化亚洲 I 期试验作出什么决定，并要求团队进一步核实哪些事项？
- 草稿答案（非 Ground Truth）：直接事实：PEC 标记为 Not Endorsed；要求评估情景 2 下有限中国患者入组的价值及资源/运营复杂性，澄清咨询问题与能否通过该亚洲 I 期免除 intensive PK，并计划临时 PEC 进一步背书。
- 修订原因：改用会议终稿；只复述该次决定，不外推通用规则。
- 证据：`PEC-Dec 2025/PEC-Dec 2025/00 2025 Dec PEC meeting minutes - Final.pdf` — **Page 2** — `chunk-12e96f2426ee`
  - 原文引用：> Not Endorsed
    - 支持关系：支持决定状态
  - 原文引用：> PEC suggested team to evaluate the value by enrollment of limited Chinese patients
    - 支持关系：支持患者价值评估
  - 原文引用：> invested resource
    - 支持关系：支持资源投入因素；按原文审查意见拆为可核验短引文
  - 原文引用：> operational complexity increased under scenario 2
    - 支持关系：支持情景 2 下运营复杂性增加；按原文审查意见拆为可核验短引文
  - 原文引用：> clearly clarify the two questions for consultation
    - 支持关系：支持澄清咨询问题
  - 原文引用：> if an intensive PK could be waived
through this Asian phase 1.
    - 支持关系：支持 PK 豁免问题
  - 原文引用：> An ad hoc PEC meeting will be planned for further endorsement.
    - 支持关系：支持后续审议安排
- [ ] 对照原始 DOCX/PDF/PPTX 完成人工核验。

### PEC-FACT-005 — fact — approved

- [ ] 核对问题：Metalyse 晚时间窗 HAI 材料给出的申请提交与 CDE 反馈预计时间分别是什么？
- 草稿答案（非 Ground Truth）：直接事实：材料计划在 2026 年 1 月底提交 HAI 申请（60 个工作日），并预计在 2026 年 4 月底左右收到 CDE 反馈。
- 修订原因：明确是材料中的计划/预计日期，不写成实际完成日期。
- 证据：`PEC-Dec 2025/PEC-Dec 2025/06. For endorsemnet- Metalyse Late Time Window HA Interaction Strategy.pptx` — **Slide 4** — `chunk-a861353db491`
  - 原文引用：> Timeline: Submit HAI Application (60WD) by the end of Jan 2026, CDE feedback expected around end of Apr 2026.
    - 支持关系：支持申请计划及反馈预计时间
- [ ] 对照原始 DOCX/PDF/PPTX 完成人工核验。

### PEC-FACT-006 — fact — approved

- [ ] 核对问题：Nerandomilast SSc 开发预读中，CDE 对直接进入 SSc 关键性试验的意见是什么？
- 草稿答案（非 Ground Truth）：直接事实：CDE 认为当前证据不足以支持直接进入 SSc 关键性试验；开展 SSc 剂量探索试验或补充证据，可支持后续推进至关键性试验。
- 修订原因：保留 CDE 条件式表述，不写成强制要求或最终决定。
- 证据：`PEC-Dec 2025/PEC-Dec 2025/10. China development strategy for Nerandomilast SSc indication__pre-reads for PEC Dec.pptx` — **Slide 17** — `chunk-9c5f8fdbc859`
  - 原文引用：> CDE: current evidence is insufficient to justify proceeding directly to a pivotal trial for SSc; a dose-exploratory trial for SSc or provide additional evidence would support advancing to a pivotal trial for SSc.
    - 支持关系：支持 CDE 对证据与后续路径的表述
- [ ] 对照原始 DOCX/PDF/PPTX 完成人工核验。

### PEC-FACT-007 — fact — approved

- [ ] 核对问题：2025年12月PEC终稿列出的 TA Inflammation RWE 成员是谁？
- 草稿答案（非 Ground Truth）：直接事实：终稿 “Members per TA (Inflammation)” 名单将 Guoze Feng 列为 RWE 成员。
- 修订原因：核对姓名并引用终稿 Page 4 的成员名单字段。
- 证据：`PEC-Dec 2025/PEC-Dec 2025/00 2025 Dec PEC meeting minutes - Final.pdf` — **Page 4** — `chunk-5bb40e665a17`
  - 原文引用：> RWE: Guoze Feng
    - 支持关系：支持 TA Inflammation 的 RWE 姓名
- [ ] 对照原始 DOCX/PDF/PPTX 完成人工核验。

### PEC-FACT-008 — fact — approved

- [ ] 核对问题：截至 2025-12-14，INFL 试验 1305-0046（BI 1015550）中国与全球分别筛查、随机入组、筛选失败多少人，筛选失败率分别是多少？
- 草稿答案（非 Ground Truth）：直接事实：China/Global 分别筛查 16/65 人、随机入组 6/21 人、筛选失败 4/13 人；筛选失败率分别为 25%/20%。数据截止日为 2025-12-14。
- 修订原因：改用未覆盖的 PEC_TA INFL 文件，扩展活动索引内文件覆盖；将数值与截止日绑定。
- 证据：`PEC-Dec 2025/PEC-Dec 2025/09. PEC_TA INFL 2025_12.pptx` — **Slide 13** — `chunk-7923c5e3dfde`
  - 原文引用：> Cut-off date: 2025.12.14
    - 支持关系：支持数据截止日
  - 原文引用：> Total Screened = 16 (China) /  65 (Global)
    - 支持关系：Supports China/global screen counts
  - 原文引用：> Total Randomized = 6 (China) /  21 (Global)
    - 支持关系：Supports China/global randomized counts
  - 原文引用：> Total Screen failure = 4 (China) /  13 (Global)
    - 支持关系：Supports China/global screen failure counts
  - 原文引用：> Screen failure rate = 25% (China) / 20% (Global)
    - 支持关系：Supports China/global screen failure rates
- [ ] 对照原始 DOCX/PDF/PPTX 完成人工核验。

### PEC-CROSS_DOCUMENT-009 — cross_document — approved

- [ ] 核对问题：FAP/TRAILR2 MASH 肝硬化亚洲 I 期材料列出的两种监管情景分别是什么，PEC 据此作出什么决定并要求后续核实什么？
- 草稿答案（非 Ground Truth）：直接事实：情景 1 为 PMDA 与 CDE 均要求亚洲数据后再进入 Ph2b；情景 2 为 PMDA 要求亚洲数据、CDE 同意 PK backloading，中国仍参与亚洲 I 期以获取早期数据并可能免除 extensive PK。终稿记录 PEC 未背书，并要求评估有限中国患者入组价值、资源及运营复杂性，澄清咨询问题与 intensive PK 豁免可能性，之后再开临时 PEC。
- 修订原因：替换主席名单/后续行动机械拼接；两份文件共同回答同一试验的假设情景与决定。
- 证据：`PEC-Dec 2025/PEC-Dec 2025/07. CRM_FAP TRAILR2 Asian Phase 1 For PEC endorsement.pptx` — **Slide 7** — `chunk-2b7e4069b4bd`
  - 原文引用：> Scenario 1: Both PMDA and CDE request  Asian data before moving forward to phase 2b.
    - 支持关系：支持情景 1
  - 原文引用：> Scenario 2: PMDA requests Asian data, while CDE agrees to the PK backloading; China will remain involved in the Asian phase 1 to gather early data, potentially allowing for the waiver of an extensive PK study.
    - 支持关系：支持情景 2
- 证据：`PEC-Dec 2025/PEC-Dec 2025/00 2025 Dec PEC meeting minutes - Final.pdf` — **Page 2** — `chunk-12e96f2426ee`
  - 原文引用：> Not Endorsed
    - 支持关系：支持 PEC 决定
  - 原文引用：> PEC suggested team to evaluate the value by enrollment of limited Chinese patients
    - 支持关系：支持后续评估要求
  - 原文引用：> An ad hoc PEC meeting will be planned for further endorsement.
    - 支持关系：支持后续审议
- [ ] 对照原始 DOCX/PDF/PPTX 完成人工核验。

### PEC-EVIDENCE_CONFLICT-031 — cross_document（recommendation_disagreement）— pending_review

- [ ] 核对问题：材料中本地团队与 BUPC/全球对 Metalyse 中国 sister trial HAI 路径的建议如何分歧？PEC 终稿是否记录已经选定最终路径？
- 草稿答案（非 Ground Truth）：直接事实：策略页记录本地团队认为因当前开发策略复杂，pre-IND 是必要的；BUPC/全球建议不先咨询 CDE 而直接提交 CTA，且中国 sister trial 的监管策略仍待 CN RA 同意。PEC 终稿背书的是中国 HA 沟通计划，并要求与全球团队对齐、说明不进行 pre-IND 而直接提交 CTA 时的 NDA 审批不确定性；终稿未记录已选定最终提交路径。
- 修订原因：依据原始文件审核报告重新分类为跨文档监管建议分歧；不是已发生事实的互斥陈述，不得计为 factual-conflict 正例。候选是否保留仍须人工决定。
- 子类：`recommendation_disagreement`；`factual_conflict_positive=false`。
- 证据：`PEC-Dec 2025/PEC-Dec 2025/06. For endorsemnet- Metalyse Late Time Window HA Interaction Strategy.pptx` — **Slide 2** — `chunk-41ab694c0c3b`
  - 原文引用：> Current proposal from the local team is not aligned with BUPC recommendations.
    - 支持关系：支持提案不一致
  - 原文引用：> Global recommends submitting the CTA directly, without prior CDE consultation.
    - 支持关系：支持全球建议
  - 原文引用：> The regulatory strategy for the China sister trial is pending CN RA agreement.
    - 支持关系：支持策略待定
- 证据：`PEC-Dec 2025/PEC-Dec 2025/00 2025 Dec PEC meeting minutes - Final.pdf` — **Page 2** — `chunk-12e96f2426ee`
  - 原文引用：> PEC endorsed the China health authority communication plan.
    - 支持关系：支持 PEC 背书事项
  - 原文引用：> PEC suggested team to fully align with global team for China HA interaction plan
    - 支持关系：支持后续对齐要求
  - 原文引用：> understand the uncertainty for NDA approval if China directly submits the CTA without the pre-IND
consultation
    - 支持关系：支持需沟通的不确定性
- [ ] 对照原始 DOCX/PDF/PPTX 完成人工核验。

### PEC-CROSS_DOCUMENT-011 — cross_document — approved

- [ ] 核对问题：关于 Nerandomilast SSc 中国开发，预读中 CDE 指出的直接进入关键性试验障碍是什么，PEC 终稿要求团队如何处理相应风险？
- 草稿答案（非 Ground Truth）：直接事实：CDE 认为当前证据不足以支持直接进入 SSc 关键性试验，并指出剂量探索试验或额外证据可支持后续推进；PEC 终稿要求正式向全球团队沟通中国 SSc/SSc-ILD 获批的潜在风险并保留书面记录。合理推论：这是该适应症的证据补强及跨团队风险记录要求，不能外推为所有治疗领域的一般规则。
- 修订原因：两份文件共同支撑同一中国 SSc 监管风险问题；删除原题无关 roster/策略拼接。
- 证据：`PEC-Dec 2025/PEC-Dec 2025/10. China development strategy for Nerandomilast SSc indication__pre-reads for PEC Dec.pptx` — **Slide 17** — `chunk-9c5f8fdbc859`
  - 原文引用：> CDE: current evidence is insufficient to justify proceeding directly to a pivotal trial for SSc
    - 支持关系：支持监管障碍
  - 原文引用：> a dose-exploratory trial for SSc or provide additional evidence would support advancing to a pivotal trial for SSc.
    - 支持关系：支持证据补充路径
- 证据：`PEC-Dec 2025/PEC-Dec 2025/00 2025 Dec PEC meeting minutes - Final.pdf` — **Page 3** — `chunk-2dd973a881d9`
  - 原文引用：> PEC suggested team to formally communicate with the global team regarding the potential risks associated
with obtaining approval for the SSc/SSc-ILD indication in China and maintain a written record of the
communication.
    - 支持关系：支持 PEC 风险沟通及留档
- [ ] 对照原始 DOCX/PDF/PPTX 完成人工核验。

### PEC-CROSS_DOCUMENT-012 — cross_document — approved

- [ ] 核对问题：关于 Zongertinib-1L NSCLC，项目更新记录了哪项近期向 CDE 提交的数据计划，PEC 终稿又要求在何种事件后复核 NDA 审批时间线？
- 草稿答案（非 Ground Truth）：直接事实：项目更新称 12 个月更新数据（DCO Aug）计划约于 2026 年 1 月滚动提交 CDE；PEC 终稿要求在 CFDI inspection 后、约 2026 年 2 月中旬重新审视 NDA 审批时间线。这是相邻但不同的计划节点，不表示已发生时间线变更。
- 修订原因：改为同一 Zongertinib-1L NSCLC 监管时间线问题；不把计划说成实际完成。
- 证据：`PEC-Dec 2025/PEC-Dec 2025/03. Oncology Portfolio Update_PEC_Dec2025.pptx` — **Slide 7** — `chunk-d089df29053f`
  - 原文引用：> 12-month updated data with DCO Aug will submit to CDE around Jan 2026 as rolling submission.
    - 支持关系：支持近期提交计划
- 证据：`PEC-Dec 2025/PEC-Dec 2025/00 2025 Dec PEC meeting minutes - Final.pdf` — **Page 2** — `chunk-12e96f2426ee`
  - 原文引用：> Zongertinib-1L NSCLC: to revisit the NDA approval timeline after CFDI inspection around mid of Feb 2026.
    - 支持关系：支持检查后复核计划
- [ ] 对照原始 DOCX/PDF/PPTX 完成人工核验。

### PEC-CROSS_DOCUMENT-013 — cross_document — approved

- [ ] 核对问题：VicaEMPA 两项 RWE 研究的议题中，CKD GDMT 研究采用什么研究类型/数据源、其主要目标是什么，PEC 终稿又记录了什么决定？
- 草稿答案（非 Ground Truth）：直接事实：议程将议题列为“(E) Two RWE studies for VicaEMPA”。相关 CKD GDMT 预读描述为使用 China Kidney Disease Network (CK-NET) 既有数据开展非干预性横断面研究；原始 Slide 5 的主要目标是“量化既存 CKD 患者单药及联合治疗的使用率”。PEC 终稿记录认可两项 RWE 研究的价值（尤其是业务价值），并无附加意见地背书研究提案。待人工确认：须核实 CKD GDMT 预读是否确切对应议程中两项 VicaEMPA RWE 提案之一。
- 修订原因：依据原始文件审核报告修复错误：Slide 5 primary objective 改为“量化既存 CKD 患者单药及联合治疗的使用率”；移除议程原文不存在的“Topic:”前缀。CKD deck 与两项提案的映射仍需业务人员确认。
- 证据：`PEC-Dec 2025/PEC-Dec 2025/00 2025 Dec PEC agenda.docx` — **Table 2 row 11** — `chunk-3c823ee34a6d`
  - 原文引用：> (E) Two RWE studies for VicaEMPA
    - 支持关系：支持议题范围；原始议程无“Topic:”前缀
- 证据：`PEC-Dec 2025/PEC-Dec 2025/08. CRM_RWE_CKD GDMT & CVRR_v2.pptx` — **Slide 2** — `chunk-c752c056e432`
  - 原文引用：> A non-interventional, cross-sectional study utilizing existing data from the China Kidney Disease Network (CK-NET).
    - 支持关系：支持研究类型与数据源
  - 原文引用：> The vision of vica-empa is to replace SGLT2i/SGLTi and ns-MRA becoming 1st line foundational therapy for CKD management
    - 支持关系：支持预读与 VicaEMPA 的主题关联，需人工确认提案映射
- 证据：`PEC-Dec 2025/PEC-Dec 2025/08. CRM_RWE_CKD GDMT & CVRR_v2.pptx` — **Slide 5** — `chunk-796c0ea7e878`
  - 原文引用：> To quantify rates of individual & combination treatment in prevalent CKD
    - 支持关系：支持原始 Slide 5 所列主要研究目标；替换先前错误引用
- 证据：`PEC-Dec 2025/PEC-Dec 2025/00 2025 Dec PEC meeting minutes - Final.pdf` — **Page 2** — `chunk-12e96f2426ee`
  - 原文引用：> Two RWE studies for VicaEMPA
    - 支持关系：支持终稿对应议题
  - 原文引用：> PEC highly recognized the value of the two RWE studies, especially the business value and endorsed the study
proposals without any comments.
    - 支持关系：支持 PEC 的决定及说明
- [ ] 对照原始 DOCX/PDF/PPTX 完成人工核验。

### PEC-INSIGHT-015 — insight — approved

- [ ] 核对问题：从 2025 年 12 月 PEC 议程的常设成员与 standing invitation 名单，能作出什么有限范围的组织覆盖观察？
- 草稿答案（非 Ground Truth）：直接事实：常设成员列有 CRM、INFL、Oncology 等治疗领域负责人及 RA、CDO、BDS、RWE 等职能；standing invitation 明列 Head of HK Medicine 与 Head of TW Medicine。合理推论（仅限本议程）：名单呈现跨治疗领域/职能的常设成员，并将 HK/TW 负责人列为 standing invitees；不能证明实际出席、决策权或其他会议普遍采用此安排。
- 修订原因：把未经证明的治理原则收窄为名单结构观察；区分原文事实与有限推论。
- 证据：`PEC-Dec 2025/PEC-Dec 2025/00 2025 Dec PEC agenda.docx` — **Table 3 row 2** — `chunk-e6cccc744213`
  - 原文引用：> Head of TA CRM Medicine: Emma Xie
    - 支持关系：支持 CRM 代表
  - 原文引用：> Head of TA INFL Medicine: Helen Tao
    - 支持关系：支持 INFL 代表
  - 原文引用：> Head of TA Oncology Medicine: Sally Chen
    - 支持关系：支持 Oncology 代表
  - 原文引用：> Head of HK Medicine: Karen Yuen
    - 支持关系：支持 standing invitation
  - 原文引用：> Head of TW Medicine: Carol Yang
    - 支持关系：支持 standing invitation
- [ ] 对照原始 DOCX/PDF/PPTX 完成人工核验。

### PEC-INSIGHT-016 — insight — approved

- [ ] 核对问题：PEC 对中国 SSc/SSc-ILD 获批风险提出了什么沟通动作；对此最多能作出什么个案层面的治理推论？
- 草稿答案（非 Ground Truth）：直接事实：PEC 建议团队正式向全球团队沟通中国 SSc/SSc-ILD 获批的潜在风险，并保留书面记录。合理推论（仅该议题）：PEC 希望风险沟通可追溯；这不证明风险已消除，也不证明所有治疗领域采用同一规则。
- 修订原因：删除不相关主题拼接；只保留 SSc/SSc-ILD 决策并限制推论范围。
- 证据：`PEC-Dec 2025/PEC-Dec 2025/00 2025 Dec PEC meeting minutes - Final.pdf` — **Page 3** — `chunk-2dd973a881d9`
  - 原文引用：> PEC suggested team to formally communicate with the global team regarding the potential risks associated
with obtaining approval for the SSc/SSc-ILD indication in China and maintain a written record of the
communication.
    - 支持关系：支持沟通与留档
- [ ] 对照原始 DOCX/PDF/PPTX 完成人工核验。

### PEC-INSIGHT-017 — insight — approved

- [ ] 核对问题：FAP/TRAILR2 亚洲 I 期个案中，PEC 明确要求团队补充评估哪些因素；对此可以作出什么有限推论，不能外推什么？
- 草稿答案（非 Ground Truth）：直接事实：PEC 要求评估情景 2 下有限中国患者入组价值及资源/运营复杂性，澄清咨询问题，并判断该亚洲 I 期能否免除 intensive PK；当时提案未获背书并拟后续召开临时 PEC。合理推论：该个案再次审议前需更清楚说明患者价值、监管咨询与操作投入；不能据此宣称所有早期试验使用同一门槛。
- 修订原因：修正从一次未背书推断通用标准的过度概括；分开个案事实与推论。
- 证据：`PEC-Dec 2025/PEC-Dec 2025/00 2025 Dec PEC meeting minutes - Final.pdf` — **Page 2** — `chunk-12e96f2426ee`
  - 原文引用：> Not Endorsed
    - 支持关系：支持决定状态
  - 原文引用：> PEC suggested team to evaluate the value by enrollment of limited Chinese patients
    - 支持关系：支持患者价值评估
  - 原文引用：> invested resource
    - 支持关系：支持资源投入因素；按原文审查意见拆为可核验短引文
  - 原文引用：> operational complexity increased under scenario 2
    - 支持关系：支持情景 2 下运营复杂性增加；按原文审查意见拆为可核验短引文
  - 原文引用：> clearly clarify the two questions for consultation
    - 支持关系：支持咨询问题澄清
  - 原文引用：> if an intensive PK could be waived
through this Asian phase 1.
    - 支持关系：支持 PK 问题
  - 原文引用：> An ad hoc PEC meeting will be planned for further endorsement.
    - 支持关系：支持后续审议
- [ ] 对照原始 DOCX/PDF/PPTX 完成人工核验。

### PEC-INSIGHT-018 — insight — approved

- [ ] 核对问题：Oncology portfolio slide 对 BI 4015092 的竞争定位写了什么；从同页竞争格局最多能推断什么，不能推断什么？
- 草稿答案（非 Ground Truth）：直接事实：材料称 BI 4015092 will be a “Late Follower”，并称若干竞争项目至少领先约两年。合理推论：按该竞争格局快照，其开发进度相对靠后；不能据此判断疗效、安全性、上市成败或未来竞争结果。
- 修订原因：把未经证明的战略优劣改为明示的开发阶段定位与有限推论；不混淆进度与疗效。
- 证据：`PEC-Dec 2025/PEC-Dec 2025/03. Oncology Portfolio Update_PEC_Dec2025.pptx` — **Slide 41** — `chunk-add734fd05cb`
  - 原文引用：> G12D Competitors _ focus on CRC + anti-EGFR | BI 4015092 will be a Late Follower
    - 支持关系：支持定位
  - 原文引用：> Several competitors are at least two years ahead
    - 支持关系：支持竞争阶段差距
- [ ] 对照原始 DOCX/PDF/PPTX 完成人工核验。

### PEC-INSIGHT-019 — insight — approved

- [ ] 核对问题：CRM portfolio update 对 red-light 中国研究列出了哪些招募缓解措施；这些措施最多支持什么有限推论？
- 草稿答案（非 Ground Truth）：直接事实：材料列出站点访问/新增站点筛选、招聘供应商、NCI/全球沟通、研究者费用和患者补贴调整、病理培训等措施，并报告截至 2025-12-01 已完成 85 次站点访问、另有 18 个计划访问。合理推论：措施覆盖多个执行层面；材料未证明措施已经改善招募结果。
- 修订原因：删除与 pFSGS 设计无关的证据，只依据 red-light 同页内容作有边界的推论。
- 证据：`PEC-Dec 2025/PEC-Dec 2025/0405. CRM Portfolio Update_20251215.pptx` — **Slide 17** — `chunk-3d557190f7ea`
  - 原文引用：> By 1 Dec, Totally 85 site engagement visits completed by BI China team. 18 sites planned to have engagement visit.
    - 支持关系：支持进度快照
  - 原文引用：> 2 Recruitment Vendors to be involved for Liverage Cirrhosis in Mid Dec 2025
    - 支持关系：支持供应商措施
  - 原文引用：> Global approved the CN Investigator Fee (IF)s increase and pt stipends increase, currently under implementation by PXL
    - 支持关系：支持费用/补贴措施
  - 原文引用：> Global Pathologist and Vendor for biopsy review/collection training on 17Dec2025
    - 支持关系：支持培训措施
  - 原文引用：> Recruitment is behind the plan in LIVERAGETM in China
    - 支持关系：支持仍落后
- [ ] 对照原始 DOCX/PDF/PPTX 完成人工核验。

### PEC-INSIGHT-020 — insight — approved

- [ ] 核对问题：MHEH portfolio update 对 Anti-C3/PAFR 的中国 GA 咨询选择和全球对齐状态记录了什么；最多能据此推断决策处于什么阶段？
- 草稿答案（非 Ground Truth）：直接事实：材料称 11 月与 Anti-C3 CDL 和 GRA 初步讨论后，偏向进行 Anti-C3 与 PAFR 的 GA pipeline consultation，但仍需在 global TA 层面进一步对齐；与 Prof. Xiaodong Sun 团队的科学交流会因时间可行性改期至 2026 年 12 月（TBD）。合理推论：本地偏好已形成，但全球对齐/日程仍未完成；不能表述为已获最终批准或已完成全球共识。
- 修订原因：改用此前未覆盖的 MHEH portfolio 文件；明确 preferred/further aligned/TBD 均表示未完结状态。
- 证据：`PEC-Dec 2025/PEC-Dec 2025/11. MHEH Portfolio Update_PEC_Dec2025.pptx` — **Slide 9** — `chunk-2e1f8a101e0d`
  - 原文引用：> After initial discussion with Anti-C3 CDL and GRA in Nov
    - 支持关系：支持已有初步讨论
  - 原文引用：> GA pipeline consultation of Anti-C3 and PAFR was preferred
    - 支持关系：支持当前偏好
  - 原文引用：> need to be further aligned with global TA level.
    - 支持关系：支持仍需全球对齐
  - 原文引用：> rescheduled to Dec 2026 due to time feasibility (TBD).
    - 支持关系：支持科学交流日程未定
- [ ] 对照原始 DOCX/PDF/PPTX 完成人工核验。

### PEC-HISTORICAL_CHANGE-021 — historical_change — approved

- [ ] 核对问题：Oncology portfolio update 明确记录 Obrixtamig 2L epNEC 研究的计划总样本量如何变化；主要分析仍基于多少例？
- 草稿答案（非 Ground Truth）：直接事实：材料称总样本量将由 50 增至 80，2025 年 11 月底发布新方案；主要分析仍基于 50 例。材料没有说明扩样原因，不能推断由疗效或安全性结果触发。
- 修订原因：采用同一研究明确的 from/to 状态替代单时点进度；不添加原文未给出的原因。
- 证据：`PEC-Dec 2025/PEC-Dec 2025/03. Oncology Portfolio Update_PEC_Dec2025.pptx` — **Slide 24** — `chunk-2f1e83ec44ea`
  - 原文引用：> Total sample size will increase from 50 to 80.
    - 支持关系：支持前后样本量
  - 原文引用：> New protocol was released in end of Nov 2025.
    - 支持关系：支持修订时间
  - 原文引用：> The primary analysis is based on 50 pts.
    - 支持关系：支持主要分析样本数
- [ ] 对照原始 DOCX/PDF/PPTX 完成人工核验。

### PEC-HISTORICAL_CHANGE-022 — historical_change — approved

- [ ] 核对问题：CRM portfolio update 对 BI 7703711 的 Ph2b planning 记录了什么明确的运营模式变化，后续还列出哪些行动？
- 草稿答案（非 Ground Truth）：直接事实：材料称运营模式由 Hybrid trial 改为 in-house module；后续行动包括启动 TDO 沟通草案与国家可行性工作，且全球团队未计划 Ph2b pre-IND。页面未提供变更的具体日期或原因。
- 修订原因：使用原文明确的 changed-from/to 两个状态；不把单次站点进度当作历史变化。
- 证据：`PEC-Dec 2025/PEC-Dec 2025/0405. CRM Portfolio Update_20251215.pptx` — **Slide 56 Table 1 row 2** — `chunk-8357e4c69252`
  - 原文引用：> The operation module changed from Hybrid trial to in housing module.
    - 支持关系：支持运营模式变化
  - 原文引用：> Initiate the draft TDO communication and country feasibility
    - 支持关系：支持后续工作
  - 原文引用：> Pre-IND for Ph2b is not planned by global team.
    - 支持关系：支持 pre-IND 状态
- [ ] 对照原始 DOCX/PDF/PPTX 完成人工核验。

### PEC-UNANSWERABLE-028 — unanswerable — approved

- [ ] 核对问题：在本次固定范围（2025年12月 PEC ZIP 的11份原始文件及其可提取文本）中，是否报告了按治疗领域划分的实际出席率（实到人数/应到人数）？
- 草稿答案（非 Ground Truth）：待人工确认：原始文件审核报告未在这11份文件的可提取文本中发现按治疗领域统计的实际出席率。该结论不覆盖图片、扫描或不可提取内容，也不代表完整知识库不存在相关信息。
- 修订原因：依据原始文件审核报告收窄无答案范围；图像/扫描内容及固定范围外知识库仍不能作无条件否定。
- expected evidence：无；扫描 generation `97760ae1341343db96d449161ea7fc37` 的 986 chunks / 11 sources。
- 扫描词：`attendance`; `attended`; `present`; `absent`; `出席`; `参会`; `到场`; `出席率`。
- 扫描记录：本轮全活动索引关键词扫描未定位到目标数值；不是无答案的人工确认。
- [ ] 人工检查全部活动来源及原始文件后，确认是否无答案。

### PEC-UNANSWERABLE-029 — unanswerable — approved

- [ ] 核对问题：在本次固定范围（2025年12月 PEC ZIP 的11份原始文件及其可提取文本）中，BI 3802876 的 MASH 肝硬化亚洲 I 期试验总预算具体金额是多少？
- 草稿答案（非 Ground Truth）：待人工确认：Slide 2 明确记载“Global confirmed budget for Asian phase 1 was secured”，即预算已落实，但未披露所问的具体总预算金额。因此仅对具体金额不可回答；不能称预算情况完全未提及，也不能据此否定图像/扫描内容或范围外资料。
- 修订原因：依据原始文件审核报告修正无答案边界；金额仍需人工确认。
- expected evidence：无；扫描 generation `97760ae1341343db96d449161ea7fc37` 的 986 chunks / 11 sources。
- 无答案判断的正向上下文（不是 expected evidence）：`PEC-Dec 2025/PEC-Dec 2025/07. CRM_FAP TRAILR2 Asian Phase 1 For PEC endorsement.pptx` — **Slide 2** — `chunk-031552a459cb` — “Global confirmed budget for Asian phase 1 was secured”。
- 扫描词：`BI 3802876`; `MASH Asian Phase I budget`; `budget`; `cost`; `RMB`; `USD`; `预算`; `费用`。
- 扫描记录：本轮全活动索引关键词扫描未定位到目标数值；不是无答案的人工确认。
- [ ] 人工检查全部活动来源及原始文件后，确认是否无答案。

### PEC-UNANSWERABLE-030 — unanswerable — approved

- [ ] 核对问题：在本次固定范围（2025年12月 PEC ZIP 的11份原始文件及其可提取文本）中，Nerandomilast 中国 SSc 剂量探索试验拟纳入多少患者？
- 草稿答案（非 Ground Truth）：待人工确认：原始文件审核报告未在这11份文件的可提取文本中发现该剂量探索试验的具体样本量。该结论不覆盖图片、扫描或不可提取内容，也不代表完整知识库不存在相关信息。
- 修订原因：依据原始文件审核报告收窄无答案范围；图像/扫描内容及固定范围外知识库仍不能作无条件否定。
- expected evidence：无；扫描 generation `97760ae1341343db96d449161ea7fc37` 的 986 chunks / 11 sources。
- 扫描词：`SSc dose-exploratory`; `Nerandomilast dose-finding`; `sample size`; `patients`; `N=`。
- 扫描记录：本轮全活动索引关键词扫描未定位到目标数值；不是无答案的人工确认。
- [ ] 人工检查全部活动来源及原始文件后，确认是否无答案。

## 暂缓题目：不得进入正式评测

以下仍为 `pending_review`，但 eligibility 暂缓；需独立原文证据并重写，或人工决定撤回。

### PEC-CROSS_DOCUMENT-014 — cross_document
- 原问题：Metalyse晚时间窗HA互动策略文件中本地团队提案与BUPC建议之间存在何种分歧，以及CRM FAP TRAILR2亚洲一期研究时间线文件中描述的两种情景假设分别是什么？
- 暂缓原因：撤回建议：原题将 Metalyse 策略与 FAP/TRAILR2 试验情景拼接，属于两个无关业务问题；未找到需同时依赖两份材料的真实问题。
- 分类：`不适用/未验证`
- 原 evidence IDs（仅供审计，不表示支持）：`chunk-41ab694c0c3b, chunk-2b7e4069b4bd`
- [ ] 人工决定撤回，或补充独立来源后重新拟题。

### PEC-HISTORICAL_CHANGE-023 — historical_change
- 原问题：How did the PEC's position on the Metalyse Late Time Window China health authority interaction strategy evolve by the December 2025 meeting, and what condition did the PEC attach going forward?
- 暂缓原因：撤回建议：只有 2025 年 12 月当前 PEC 立场，没有可核验的较早状态，不能称为立场演变。
- 分类：`不适用/未验证`
- 原 evidence IDs（仅供审计，不表示支持）：`chunk-12e96f2426ee, chunk-3d557190f7ea`
- [ ] 人工决定撤回，或补充独立来源后重新拟题。

### PEC-HISTORICAL_CHANGE-024 — historical_change
- 原问题：How did the number of site engagement visits completed by the BI China team change leading up to the December 2025 CRM portfolio update, and what remained to be done?
- 暂缓原因：撤回建议：85 次已完成访问与 18 个计划访问是同一截止日进度快照，不构成两个时间状态。
- 分类：`不适用/未验证`
- 原 evidence IDs（仅供审计，不表示支持）：`chunk-12e96f2426ee, chunk-3d557190f7ea`
- [ ] 人工决定撤回，或补充独立来源后重新拟题。

### PEC-EVIDENCE_CONFLICT-025 — evidence_conflict
- 原问题：According to the December 2025 PEC meeting records, who is the responsible person for the action item to revisit the Zongertinib-1L NSCLC NDA approval timeline after the CFDI inspection?
- 暂缓原因：撤回建议：结构化冲突把发言人 Jiaqi Lu 推断为负责人；原始 minutes 将 Zongertinib 行负责人列为 Lijie Yu。这是角色归属/抽取歧义，不是互斥事实。
- 分类：`role_attribution_ambiguity_not_conflict`
- 原 evidence IDs（仅供审计，不表示支持）：`chunk-12e96f2426ee, chunk-4b4749b1685b`
- [ ] 人工决定撤回，或补充独立来源后重新拟题。

### PEC-EVIDENCE_CONFLICT-026 — evidence_conflict
- 原问题：Based on the December 2025 PEC meeting documentation, which responsible person is assigned to follow up on the Zongertinib-1L NSCLC NDA approval timeline review scheduled for February 2026's PEC?
- 暂缓原因：撤回建议：与 025 是同一 Zongertinib action/负责人问题的近义重复，且同样混淆发言人与 action owner。
- 分类：`duplicate_not_independent`
- 原 evidence IDs（仅供审计，不表示支持）：`chunk-12e96f2426ee, chunk-4b4749b1685b`
- [ ] 人工决定撤回，或补充独立来源后重新拟题。

### PEC-EVIDENCE_CONFLICT-027 — evidence_conflict
- 原问题：Do the Final PDF and the draft document of the December 2025 PEC meeting minutes agree on who is responsible for revisiting the Zongertinib-1L NSCLC NDA approval timeline after the CFDI inspection?
- 暂缓原因：撤回建议：草稿与终稿记录同一行动项；已查原文未显示互斥负责人，属于版本/字段对齐复核，不是已证实真实冲突。
- 分类：`version_comparison_without_verified_conflicting_claims`
- 原 evidence IDs（仅供审计，不表示支持）：`chunk-12e96f2426ee, chunk-4b4749b1685b`
- [ ] 人工决定撤回，或补充独立来源后重新拟题。

## 审核操作

普通可回答题：逐题人工核对原文后再批准，不要批量操作。
```powershell
C:\BITrusted\work\python.exe -m src.pec.benchmark review `
  --dataset data\pec\benchmark\candidate_cases.jsonl `
  --case-id PEC-FACT-001 --status approved `
  --reviewer "reviewer-name" --note "已核对原始文件和引用位置。"
```

当前没有经来源核验的 factual-conflict 正例。031 是待审核的跨文档监管建议分歧候选，不得计入事实冲突指标；如业务审核者确认保留，请按 cross_document 样本审批，并在审核备注中明确确认该标签与范围。

无答案题需完整人工核查，例如：
```powershell
C:\BITrusted\work\python.exe -m src.pec.benchmark review `
  --dataset data\pec\benchmark\candidate_cases.jsonl `
  --case-id PEC-UNANSWERABLE-028 --status approved `
  --reviewer "reviewer-name" --note "已核对固定范围的11份原始文件及可提取文本；图像/扫描内容的核查范围已记录。" `
  --confirm-answerability "人工确认本题仅在固定11份来源的可提取文本范围内未提供按治疗领域统计的实际出席率。"
```

只有人工批准的样本进入正式评测；运行前复核索引 generation/manifest 和 Apollo Embedding 可用性。

## 覆盖扩展前置条件

将 PEC 2024-04、MAP 2025-05、PEC 2026-04 资料重新入库并发布到新 generation 后，再新增跨会议/年份题并重新核验 evidence IDs/locations。旧 root-level chunks 不能作为当前活动索引的正式证据。
