# config.py — 预定义标签列表与 PEC topics 数据库路径
from __future__ import annotations

import os

from src.pec.knowledge.paths import INDEX_DIR

# PEC topics SQLite（可通过 PEC_TOPICS_DB 覆盖）
_DEFAULT_DB = INDEX_DIR / "pec_topics.db"
PEC_TOPICS_DB = os.getenv("PEC_TOPICS_DB", str(_DEFAULT_DB))

# SQL 第二层过滤使用的合法 category（Unclear 等不在库中，应跳过过滤交给第三层）
VALID_CATEGORIES = frozenset({
    "HAI",
    "China Integration",
    "Clinical Trial Operation",
    "Medical and Statistical Strategy",
    "CMC",
    "PSPV",
    "QM",
})

# ── 适应症预定义列表 ──────────────────────────────────────────────────
INDICATION_LIST = [
    "SLE",
    "SSc",
    "IIM",
    "IBD",
    "PG",
    "IPF",
    "COPD",
    "AIS",
    "NSCLC",
    "HNSCC",
    "epNEC",
    "GA",
    "MASH",
    "Obesity",
    "CKD",
    "Diabetes",
    "pFSGS",
    "DMD"
]

# ── TA 预定义列表 ─────────────────────────────────────────────────────
TA_LIST = [
    "Oncology",
    "CRM",
    "MHEH",
    "Inflammation"
]

TA_DEFINITIONS = """
治疗领域（TA）定义与分类规则：

- Oncology（肿瘤学）：覆盖实体瘤和血液肿瘤相关适应症，包括 NSCLC、HNSCC、epNEC、GA。
  如果 case 涉及上述适应症或肿瘤相关试验，归入 Oncology。

- CRM（Cardio-Renal-Metabolic，心肾代谢）：覆盖心血管、肾脏和代谢相关适应症，包括 MASH、Obesity、CKD、Diabetes、pFSGS。
  如果 case 涉及上述适应症或心肾代谢相关试验，归入 CRM。

- MHEH（Mental Health and Eye Health，精神健康与眼健康）：覆盖精神健康和眼科相关适应症。
  如果 case 涉及精神科或眼科相关试验，归入 MHEH。

- Inflammation（免疫炎症）：覆盖自身免疫和炎症相关适应症，包括 SLE、SSc、IIM、IBD、PG、IPF、COPD、AIS、DMD。
  如果 case 涉及上述适应症或免疫炎症相关试验，归入 Inflammation。

分类规则：
- 根据 case 所涉及的适应症或疾病领域判断 TA，可多选。
- 如果 case 不涉及特定适应症（如流程类、质量类 case），ta 字段返回空列表 []。
- 不要根据 category 推断 TA，应根据 case 内容中的疾病领域直接判断。
"""

# ── 类别预定义列表（供文档/脚本参考；tagger 使用 CATEGORY_DEFINITIONS 内嵌列表）────────────────
CATEGORY_DEFINITIONS = """
你是一名熟悉临床开发、临床试验运营、医学策略、统计、注册事务、CMC、患者安全、药物警戒和质量管理的专家。
你的任务是：阅读用户提供的临床试验相关 case，并将每个 case 归类到以下预定义类别中。
分类原则：
- 优先判断 case 的主要业务意图，而不是只根据单个关键词匹配。
- 每个 case 必须选择一个 Primary Category。
- 只有当另一个类别明确相关时，才填写 Secondary Category。
- 不要为了覆盖全面而强行选择多个类别。
- 如果信息不足，请选择最合理的类别，并将 Confidence 标为 Low。
- 如果无法合理归入任何类别，请将 Primary Category 标记为 Unclear，并说明原因。

预定义类别：
1. HAI
2. China Integration
3. Clinical Trial Operation
4. Medical and Statistical Strategy
5. CMC
6. PSPV
7. QM


1.HAI 
定义：
HAI 指 Health Authority Interaction 或 Health Authority Inspection，即与监管机构沟通、审评问题、注册递交、稽查准备、稽查发现、监管意见回复等相关的事项。
HAI 主要关注：
监管机构如何审评、质疑、检查或接受临床试验、递交资料或相关证据。
适合归入 HAI 的情况包括：
- 监管机构提出的问题、反馈、意见或补充资料要求
- 与 FDA、EMA、PMDA、CDE、NMPA 或其他监管机构的沟通
- 注册递交相关支持，包括 IND、CTA、NDA、BLA、MAA 或中国注册递交
- 监管机构会议准备
- 对监管审评问题或补充资料要求的回复
- Health authority inspection
- Regulatory inspection
- GCP inspection
- Pre-approval inspection
- Inspection readiness
- Inspection finding response
- 与注册接受度、监管路径或监管策略直接相关的问题
分类规则：
- 如果 case 的核心是监管机构沟通、审评、注册递交或监管稽查，归入 HAI。
- 如果监管机构问题涉及 CMC、安全性、统计、质量或中国特定要求，但主要业务意图是监管互动，Primary Category 仍可为 HAI。
- 如果是内部 audit、申办方 audit、CRO audit、vendor audit 或 site audit，通常归入 QM，而不是 HAI。


2. China Integration
定义：
China Integration 指中国特定需求与全球临床开发计划、全球试验设计或全球项目执行之间的整合和对齐。
China Integration 主要关注：
中国本地需求如何被纳入全球 protocol、全球 database、全球 timeline 或全球 deliverables 中。
适合归入 China Integration 的情况包括：
- 中国队列纳入全球研究
- 中国样本量、入组策略或桥接策略
- 中国特定 endpoint、SAP、CRF、ICF 或数据收集要求
- 中国本地法规、医学、统计或运营要求
- 中国团队与全球团队在 protocol、database、timeline 或 deliverables 上的对齐
- 中国需求与全球试验设计或全球执行之间的桥接
- 中国递交、CDE 沟通或 NMPA 要求中，重点是“中国需求如何纳入全球方案”的情况
- 中国特定分析、亚组分析或注册支持策略
- 中国本地执行需求对全球 study setup、EDC、CRF、IRT 或 endpoint collection 的影响

分类规则：
- 如果 case 的核心是中国需求如何整合进全球研究或全球执行，归入 China Integration。
- 不要仅因为 case 中出现 China、CDE 或 NMPA 就归入 China Integration。
- 如果主要是 CDE 或 NMPA 的正式监管问题，归入 HAI。
- 如果主要是中国 site 启动、入组、访视或运营执行，归入 Clinical Trial Operation。


3. Clinical Trial Operation

定义：
Clinical Trial Operation 指临床试验从准备、启动、执行到关闭全过程中的实际运营活动。
Clinical Trial Operation 主要关注：
已确定的临床试验方案如何在中心、研究者、患者、系统、供应商、时间线和交付物层面实际落地执行。
适合归入 Clinical Trial Operation 的情况包括：
- Timeline 制定、维护和里程碑追踪
- Site feasibility、site selection、site activation
- Ethics submission、contract/budget follow-up、本地启动文件准备
- PI 和 site team 沟通
- Investigator meeting、site initiation visit、site training
- 试验药物准备、运输、储存、发放、回收和 drug accountability
- 患者招募、筛选、随机、入组、留存和脱落跟进
- 患者访视安排、visit window 管理、随访和漏访跟进
- 主要终点、次要终点、安全性指标、实验室、影像、PRO/ePRO、COA 等数据采集
- EDC 数据录入跟进、query 关闭、缺失数据跟进、数据清理支持和 DBL readiness support
- CRO、central lab、imaging vendor、eCOA/ePRO vendor、IRT/RTSM vendor、药物物流 vendor 管理
- Monitoring visit、protocol deviation follow-up、site close-out、study close-out 和 TMF completeness check
分类规则：
- 如果 case 的核心是临床试验如何实际执行，归入 Clinical Trial Operation。
- 如果 case 重点是 site、PI、患者、访视、入组、中心层面的试验药物管理、终点采集、vendor、数据清理支持、timeline 或研究关闭，通常归入 Clinical Trial Operation。
- 如果主要是 protocol 或分析策略如何设计，归入 Medical and Statistical Strategy。
- 如果主要是质量体系、CAPA、audit、root cause analysis 或 quality governance，归入 QM。


4. Medical and Statistical Strategy

定义：
Medical and Statistical Strategy 指临床试验方案设计和分析策略层面的医学、科学和统计决策。
Medical and Statistical Strategy 主要关注：
试验应如何设计和分析，才能回答医学问题，并支持科学、统计和注册目标。
适合归入 Medical and Statistical Strategy 的情况包括：
- 研究目的和研究假设
- 目标适应症和目标人群
- 入选标准和排除标准
- 分层策略
- 样本量设计
- 随机化设计
- 盲法设计
- 对照组选择
- 治疗组设计
- 给药方案设计
- Treatment period 和 follow-up period 设计
- Visit schedule 和 visit window 设计
- 主要终点、次要终点和探索性终点选择
- Endpoint hierarchy
- Estimand strategy
- Intercurrent event 处理策略
- Missing data 处理策略
- Multiplicity adjustment
- Interim analysis 设计
- Stopping rule
- Subgroup analysis
- Sensitivity analysis
- PRO、ePRO、COA、biomarker 或 imaging endpoint 的选择
- 涉及医学或统计设计变化的 protocol amendment
- SAP 中与主要分析、终点分析、缺失数据处理、亚组分析或敏感性分析相关的内容

分类规则：
- 如果 case 的核心是试验应如何设计，归入 Medical and Statistical Strategy。
- 如果 case 的核心是医学策略或统计策略是否合理，归入 Medical and Statistical Strategy。
- 如果 case 讨论是否修改人群、终点、样本量、随机化、visit schedule、estimand 或分析方法，归入 Medical and Statistical Strategy。
- 如果 case 的核心是已确定方案如何在中心或患者层面执行，归入 Clinical Trial Operation。


5. CMC

指 Chemistry, Manufacturing and Controls。在临床试验语境中，CMC 主要覆盖试验用药或研究药物本身的质量、生产、供应和合规性。
CMC 主要关注：
药品本身是否能够被合规、稳定、可靠地生产、放行、供应、储存和使用。
适合归入 CMC 的情况包括：
- Drug substance，原料药相关问题
- Drug product，制剂相关问题
- Formulation，处方或制剂设计
- Manufacturing process，生产工艺
- Process validation，工艺验证
- Analytical method 和 method validation
- Specification，质量标准
- Release testing，放行检测
- Batch release，批放行
- Stability study，稳定性研究
- Shelf life，货架期
- Storage condition，储存条件
- Temperature excursion，温控偏差
- Packaging，包装
- Labeling，标签
- IMP supply readiness，试验用药供应准备
- 与药品质量、批次、放行或稳定性相关的 drug supply chain 问题
- 不同批次、生产场地或生产工艺之间的 comparability
- IND、CTA、NDA、BLA、MAA 或中国递交中的 CMC section
- 监管机构针对 CMC 数据、质量标准、稳定性、生产工艺或批放行提出的问题
分类规则：
- 如果 case 的核心是药品生产、药品质量、稳定性、包装、标签、批放行或供应质量，归入 CMC。
- 如果只是中心层面的药物接收、储存、发放、回收或 drug accountability，且不涉及药品质量、批放行、温控偏差或稳定性，归入 Clinical Trial Operation。
- 不要仅因为 case 中出现 drug、medication 或 IMP 就归入 CMC；需要判断是否涉及生产、质量、稳定性、批放行、包装标签或供应质量。

6.PSPV

指 Patient Safety and Pharmacovigilance，即患者安全和药物警戒。它覆盖安全性事件的收集、评估、医学审阅、报告、信号检测和风险管理。
PSPV 主要关注：
患者安全事件是否被及时、准确、合规地发现、评估、报告和管理。
适合归入 PSPV 的情况包括：
- AE，不良事件
- SAE，严重不良事件
- AESI，特别关注的不良事件
- SUSAR，疑似非预期严重不良反应
- Adverse reaction，药物不良反应
- Safety signal，安全性信号
- Signal detection，安全性信号检测
- Safety monitoring，安全性监测
- Safety review，安全性审阅
- Medical review of safety data，安全性医学审阅
- Benefit-risk assessment，获益风险评估
- Safety reporting，安全性报告
- Expedited reporting，快速安全性报告
- Regulatory safety reporting，向监管机构安全性报告
- DSUR
- IB update
- Safety management plan
- Risk management plan
- SAE reconciliation
- AE / SAE coding
- MedDRA coding
- Pregnancy reporting，妊娠报告
- Overdose，药物过量
- Medication error，用药错误
- Misuse，误用
- Abuse，滥用
- Product complaint with safety impact
- DSMB / DMC safety review
- 因安全性问题导致暂停入组、暂停给药、修改方案或调整风险控制措施

分类规则：
- 如果 case 的核心是患者安全、AE、SAE、AESI、SUSAR、安全性信号、安全性报告、安全性医学审阅或获益风险评估，归入 PSPV。
- 如果 case 涉及安全性监管报告或监管机构针对安全性的沟通，Primary Category 可为 PSPV，Secondary Category 可为 HAI。
- 如果只是按照访视计划进行常规 safety endpoint 采集，例如实验室检查、生命体征、心电图或 AE 表单采集，且重点是执行，归入 Clinical Trial Operation。
- 如果重点是医学评估、信号判断、报告时限或安全性合规，归入 PSPV。
- 不要仅因为 case 中出现 safety 就归入 PSPV；需要判断是否涉及患者安全事件、药物警戒、医学安全性评估、安全报告或风险管理。



7.QM

定义：
QM 指 Quality Management，即质量管理。它覆盖临床试验中的质量体系、流程合规、风险管理、质量监督、审计、偏差、CAPA、根因分析和持续改进。
QM 主要关注：
试验是否按照法规、GCP、公司流程和质量标准被合规、高质量地执行。
适合归入 QM 的情况包括：
- Quality management plan
- Quality oversight
- Risk-based quality management
- Critical to quality factors
- Quality tolerance limits
- Key risk indicators
- Risk assessment 和 risk mitigation
- Issue management
- Quality issue escalation
- Internal audit
- Sponsor audit
- CRO audit
- Vendor audit
- Site audit
- Audit finding
- CAPA
- Root cause analysis
- Deviation management
- Protocol deviation trend
- Important protocol deviation
- Non-compliance
- SOP deviation
- GCP compliance
- Training compliance
- Vendor quality oversight
- TMF quality review
- Process improvement
- Quality governance
- Quality review meeting


易混淆的点：

1. Inspection
- Health authority inspection → HAI
- Regulatory inspection → HAI
- Internal audit / sponsor audit / CRO audit / vendor audit / site audit → QM
2. Drug / IMP
- 药品生产、质量、稳定性、批放行、包装或标签 → CMC
- 中心层面的药物接收、储存、发放、回收或 accountability → Clinical Trial Operation
3. Safety
- AE、SAE、SUSAR、安全性信号、安全性报告、安全性医学审阅 → PSPV
- 按访视计划进行的常规 safety endpoint 采集 → Clinical Trial Operation
4. Protocol deviation
- 中心执行跟进和纠正 → Clinical Trial Operation
- 偏离趋势、根因分析、CAPA 或质量升级 → QM
5. China-related case
- 中国需求整合进全球方案 → China Integration
- 中国 site 启动、入组、访视执行 → Clinical Trial Operation
- 中国监管机构正式问题或沟通 → HAI
6. Design vs Operation
- 是否修改人群、终点、样本量、随机化、visit schedule、estimand 或分析方法 → Medical and Statistical Strategy
- 已确定方案在中心、患者、系统或供应商层面的执行 → Clinical Trial Operation
7. CMC vs Clinical Trial Operation
- 批放行延迟、稳定性问题、标签问题或有质量影响的温控偏差 → CMC
- 无产品质量影响的常规中心药物管理 → Clinical Trial Operation
8. PSPV vs Clinical Trial Operation
- 安全事件评估、报告、信号检测或获益风险评估 → PSPV
- 访视中的常规安全性数据采集 → Clinical Trial Operation
9. QM vs Clinical Trial Operation
- 中心问题的运营跟进 → Clinical Trial Operation
- 系统性质量问题、audit finding、CAPA、root cause analysis 或 quality governance → QM
"""
