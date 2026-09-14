## Plan: DMF 历史快照与变更检测

为 Agent/Web 的每个具体 DMF 查询先独立保存 monitor run，再对符合资格的非空 FDA 结果保存快照，并仅与相同查询条件的有效 baseline 比较。P0 使用 SQLAlchemy + Alembic，开发默认 SQLite、保留切换 PostgreSQL 的能力；严格区分 query fingerprint 与 DMF business key，无稳定身份时不自动匹配。SUCCESS_EMPTY 只留存空观察并告警，不生成变化或推进 baseline。历史入库故障不得改变 FDA 查询本身的成功状态。

**Steps**

### 阶段一：明确查询与历史契约
1. 扩展 `src/schemas/dmf.py`：为单项查询结果增加显式采集完成状态和可选历史比较结果；新增变更类型、字段变化、记录变化、快照比较摘要等 Pydantic 模型。保持现有字段兼容，确保未启用历史功能时原调用方不受影响。
2. 统一 `src/dmf_query/dmf_service.py::search_all_dmf` 的返回契约：区分 `SUCCESS_NONEMPTY`、`SUCCESS_EMPTY`、`PARTIAL`、`FAILED` 和 `NOT_EXECUTED`。零记录可审计但不创建或更新 baseline；分页部分失败保留已抓取记录和进度，但不可参与比较。

### 阶段二：数据库与迁移基础
3. 在 `requirements.txt` 增加 SQLAlchemy、Alembic 和 PostgreSQL 驱动依赖；建立 Alembic 配置及首个迁移。数据库 URL 统一由配置提供，不使用 SQLite 专属 SQL。
4. 修改 `src/settings.py`：增加 `DMF_HISTORY_ENABLED` 和 `DATABASE_URL`；本地默认指向 `src/storage/dmf_history.db`，测试可注入临时 SQLite，生产可配置 PostgreSQL。敏感连接信息不写日志。
5. 新增 `src/dmf_history/models.py`，建立以下表和索引：
   - `dmf_monitor_runs`：一次具体查询的条件、查询指纹、开始/完成时间、采集状态、上游总数、实际记录数和错误信息。
   - `dmf_snapshots`：保存 `raw_payload`、结构化 `source`、UTC `queried_at`、raw/normalized hash 和 baseline 资格。
   - `dmf_snapshot_records`：保存 FDA 原始字段、独立的 business key、身份类型、匹配资格和未匹配原因。
   - `dmf_change_events`：保存 `added`、`removed`、`field_changed` 事件及变化前后 JSON。
   - `dmf_baselines`：按 query fingerprint 保存当前有效 baseline 和并发版本号。
   对查询指纹、快照时间、DMF 编号和事件类型建立索引；外键和唯一约束保证同一次运行不会重复入库。
6. 新增 `src/dmf_history/repository.py` 与会话/引擎工厂：monitor run 使用第一事务独立提交；snapshot、records、diff events 与 baseline 推进使用第二事务保持原子；失败后用补偿事务标记 run。SQLite 与 PostgreSQL 共用同一接口。

### 阶段三：比较算法与业务接入
7. 实现标准化和比较服务：
   - 查询指纹基于单个实际执行条件 `DMFSingleQuery`，对文本做 Unicode NFKC、首尾及连续空白归一和 `casefold()`；不删除标点、企业后缀或药物盐型。
   - query fingerprint 只用于查询序列分区；business key 只用于跨快照记录匹配；payload hash 只用于内容校验，三者不可互换。
   - P0 仅将非空且在参与比较的快照中唯一的 `dmf_no` 作为 business key。缺失或重复身份的记录仍入库，但不自动生成变化；不使用成分、申请商或顺序强行匹配。
   - 前一快照无、当前有为 `added`；前一有、当前无为 `removed`；身份相同但申请商、成分、有效日期任一字段不同为 `field_changed`，一个事件可列出多个字段的 before/after。
   - 首次完整查询只建立基线，返回 `baseline_created=true`，不产生变化事件；相同数据返回空变化集。
8. 修改 `src/dmf_query/multi_query_service.py::search_dmf_queries`：每个具体查询项分别持久化。仅 `SUCCESS_NONEMPTY` 创建合格快照并比较；`SUCCESS_EMPTY` 保存不可作为 baseline 的空观察且不生成 removed；失败或分页不完整项仅记录 monitor run。
9. 将历史结果附加到对应 `DMFQueryResult`，由现有 Agent/Web 返回链自然传递。数据库不可用、迁移未执行或写入失败时，保留 FDA 查询的原始 `success`，只设置结构化 `history_error` 并记录服务端日志。
10. 保持 `src/dmf_query/main.py` CLI 和 `search_multiple_ingredients` 兼容入口不接入 P0 历史；在代码/文档中明确该范围，防止“所有入口都已覆盖”的误解。

### 阶段四：测试与文档
11. 新增 repository 与比较算法单测：首次基线、无变化、新增、消失、申请商变化、成分变化、有效日期变化、多字段同时变化、零记录快照、从空到有/从有到空、重复写入幂等、事务回滚、同编号多行和空 DMF 编号低置信度场景。
12. 扩展查询服务集成测试：完整成功入库；分页部分失败不更新基线；验证码/网络失败不产生消失；批量部分失败时成功项仍更新；历史库故障不改变查询成功；相同条件文本规范化后命中同一基线，不同条件不会交叉比较。
13. 更新环境变量示例、部署说明和 `MODIFICATION_LOG.md`：记录 SQLite 本地运行、Alembic 初始化/升级、PostgreSQL 连接方式、P0 覆盖范围及“removed 仅表示本次 FDA 完整查询未返回，不等同于法规注销”。

**Relevant files**
- `src/schemas/dmf.py` — 扩展 `DMFQueryResult`，增加历史比较与采集完成状态模型。
- `src/dmf_query/dmf_service.py` — 统一 `search_all_dmf` 完整/部分失败语义。
- `src/dmf_query/multi_query_service.py` — Agent/Web 的单项查询持久化和比较接入点。
- `src/settings.py` — 历史开关与数据库 URL 配置。
- `src/dmf_history/__init__.py` — 新历史模块导出。
- `src/dmf_history/models.py` — SQLAlchemy 持久化模型。
- `src/dmf_history/repository.py` — 事务、快照读取和事件写入。
- `src/dmf_history/comparison.py` — 查询指纹、记录身份、字段差异算法。
- `alembic.ini`、`alembic/env.py`、`alembic/versions/*` — 数据库迁移。
- `requirements.txt` — SQLAlchemy、Alembic、PostgreSQL 驱动。
- `tests/test_dmf_history_repository.py` — 数据层和比较边界测试。
- `tests/test_dmf_query_history.py` — 查询链路集成测试。
- `MODIFICATION_LOG.md` — 功能、验证与范围记录。

**Verification**
1. 对临时 SQLite 执行 `alembic upgrade head`，校验四张表、约束与索引；再执行一次升级确认幂等。
2. 运行 `pytest tests/test_dmf_history_repository.py tests/test_dmf_query_history.py tests/test_dmf_query_service.py`。
3. 运行既有 Agent、文档查询和 Web 路由回归，确认新增可选响应字段不破坏原合同。
4. 用模拟 FDA 响应连续执行：首次基线 → 相同结果 → 字段变化 → 新增记录 → 记录消失；核对 API 摘要与数据库事件完全一致。
5. 模拟第二页超时和数据库锁/断连，确认不产生 `removed`、不更新基线，且 FDA 查询成功/失败语义不被历史模块覆盖。
6. 使用 PostgreSQL 测试连接执行迁移和 repository 测试，验证代码未依赖 SQLite 特性。

**Decisions**
- P0 只覆盖 Agent/Web 正常对话查询及文档确认后的查询；CLI 和旧兼容入口暂不沉淀。
- P0 只将本次变化摘要随查询结果返回；不提供历史 REST API、Web 历史页面、定时重查或通知。
- query fingerprint 与 business key 严格分离；记录仅在 DMF 编号非空且唯一时参与自动匹配，成分不作为辅助身份。
- `removed` 表示同一条件下，上一份完整 FDA 快照中的记录在本次完整查询中未返回；不解释为注销、撤销或失效。
- 当前可监控字段仅为 `dmf_no`、`applicant_name`、`ingredient`、`valid_date`。P0 将有效日期变化作为字段变化，不虚构独立状态字段；未来解析出 FDA 明确状态字段后纳入通用字段比较。
- 仅 `SUCCESS_NONEMPTY` 更新比较基线；`SUCCESS_EMPTY` 只保存空观察并告警，不产生 removed；网络、验证码、解析和分页部分失败均不参与比较。
- monitor run 独立提交；snapshot/diff/baseline 在第二事务中原子处理。
- snapshot 保存脱敏的完整分页 raw JSON、结构化 source 和 UTC queried_at，不向 Agent/Web 直接透传 raw。
- 历史持久化是旁路能力，其故障可见但不得导致真实 FDA 查询被标记失败。

**Further Considerations**
1. PostgreSQL 上线需要持久数据库和密钥配置；SQLite 部署到容器时必须挂载持久卷，否则 Pod 重建会丢失历史。该部署基础设施不属于当前 P0 功能代码，但上线前必须落实。
2. P1 可在现有事件表之上增加历史分页 API、工作台时间线、定时重查、事件确认和邮件/企业消息通知，无需重做快照模型。
