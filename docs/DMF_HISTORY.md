# DMF 历史快照与变更检测

## 启用

安装依赖后先执行数据库迁移：

```powershell
python -m alembic upgrade head
```

再配置：

```env
DMF_HISTORY_ENABLED=true
DATABASE_URL=sqlite:///./src/storage/dmf_history.db
```

生产 PostgreSQL 示例：

```env
DATABASE_URL=postgresql+psycopg://user:password@host:5432/dmf_history
```

SQLite 部署到容器时，`src/storage` 必须挂载持久卷。

## 业务规则

- `SUCCESS_NONEMPTY`：保存快照，与同一 query fingerprint 的 baseline 比较，并在事务成功后推进 baseline。
- `SUCCESS_EMPTY`：保存空观察并告警，不生成 `removed`，不创建或推进 baseline。
- `PARTIAL`、`FAILED`、`NOT_EXECUTED`：只保存 monitor run，不参与比较。
- 批量查询中途验证码失效时，剩余查询项全部记为 `NOT_EXECUTED`，并单独计入 `not_executed_count`。
- `query_fingerprint` 用于查询条件分区，`business_key` 用于记录匹配，两者相互独立。
- P0 仅将非空且在参与比较的快照中唯一的 DMF 编号作为稳定身份。
- 缺失或重复 DMF 编号的记录仍会保存，但不会自动产生新增、消失或字段变化事件。
- `removed` 只表示本次合格 FDA 快照未返回该记录，不代表注销、撤销或失效。

## 数据与安全

快照保存全部 FDA 分页响应、来源元数据和实际查询完成时间。入库前会移除验证码、`verifyCode`、Cookie 和 Authorization 等敏感字段。raw payload 不会进入 Agent 回答。

monitor run 先独立提交。snapshot、records、diff events 和 baseline 推进在第二个事务中原子执行；第二事务失败不会丢失 monitor run，也不会改变 FDA 查询本身的成功状态。

monitor run 的 `started_at` 和 `ended_at` 来自每个具体 FDA 查询的真实执行窗口；`NOT_EXECUTED` 项的两个字段为空。内部日志和 monitor run 可保留故障详情，对外仅返回 `HISTORY_STORAGE_ERROR` 和固定脱敏提示。

## P0 范围

历史记录覆盖 Agent/Web 查询以及文档确认后的查询。CLI、旧兼容查询入口、历史分页 API、定时重查和通知暂不包含在 P0 中。

## DMF Watchlist

Watchlist 在历史快照之上持续关注单个 DMF 编号。启用前执行迁移，并配置：

```env
DMF_HISTORY_ENABLED=true
DMF_WATCHLIST_ENABLED=true
DMF_WATCHLIST_POLL_SECONDS=60
```

运行一轮到期任务：

```powershell
python -m src.dmf_watchlist.worker --once
```

作为独立常驻 worker 运行：

```powershell
python -m src.dmf_watchlist.worker
```

Watchlist API 位于 `/api/watchlists`，支持创建、列表、暂停、恢复、软删除、手动执行、运行历史、事件列表和事件确认。当前为团队共享资源，用户级鉴权和 ACL 由部署网关负责。

Agent 通过现有 LangGraph 的单一 `manage_watchlist` 节点支持六项操作：添加、删除、列表、立即检查、事件列表和事件确认。普通单项读写直接执行，不创建第二套 Workflow 或自主 Tool Loop。立即检查是同步操作，响应时间包含验证码识别、FDA 分页查询和历史入库；每条 Web/CLI 消息会生成稳定请求 ID，用于同一次立即检查重试时的幂等键。

“关注刚才结果”只在当前可信结果中恰好存在一个唯一 DMF 编号时执行；存在多个编号时要求用户明确目标。列表和事件回答最多展示 10 条，完整数据仍可通过 API 获取。v1 不支持批量添加、批量删除、清空、批量确认或消息外发，不会将这些请求降级为单项操作；后续开放此类高影响操作时必须接入 LangGraph `interrupt/resume` 确认。

首次完整查询查到目标 DMF 时只建立关注 baseline，不生成 `added`。首次完整查询未返回目标时计数为 1，第二次完整查询仍未返回时生成 `absent_confirmed`。手动和定时查询具有相同观察语义；失败、部分完成和未执行查询不改变未返回计数。

History 的全局 baseline 与 Watchlist 游标相互独立。`SUCCESS_EMPTY` 仍不推进全局 baseline，但会作为 Watchlist 的一次完整未返回观察。事件状态首版只有 `unread` 和 `acknowledged`，重复确认是幂等操作。

### 有效日期过期提醒

- `valid_date_expired` 只表示 FDA 返回的有效日期早于台湾当地日期，不表示注销、撤销或其他监管结论。
- 按 `Asia/Taipei` 当地日期判断；有效日期当天仍视为有效，次日才产生过期提醒。
- 首次关注查到目标 DMF 已过期时立即产生提醒，但不产生 `added`。
- 同一过期周期、同一规范化有效日期只提醒一次；日期恢复有效后再次过期可开启新的提醒周期。
- 支持公历横线、斜杠、点号格式，以及数字或中文民国年格式，例如 `2026-09-03`、`2026/9/3`、`115/9/3`、`民國115年9月3日`。
- 日期为空、占位或无法严格解析时不判断为过期，本次 Watchlist run 会返回 warning。
- 有效日期变化仍会产生 `field_changed`；从过期恢复为有效不额外产生恢复事件。
- 手动执行和定时执行使用相同的过期判断及去重游标。

## 可靠性与验收记录 — 2026-10-22

- 新增端到端测试覆盖“真实 History/Watchlist 持久化 → 保存快照 → 检测字段变化和新增记录 → 生成 Watchlist 事件 → 写入通知 outbox → fake 邮件发送 → 确认事件”。测试不调用真实 FDA 或 SMTP。
- 故障与状态边界覆盖：网络异常、验证码/上游失败、部分结果、未执行查询均不能推进 Watchlist baseline 或把记录误判为消失；重复 DMF 身份进入 `identity_ambiguous` 且不生成告警；完整空结果仍按连续两次观察确认消失。
- Worker 故障注入在 outbox 写入时失败，验证事务回滚、锁释放、重启后复用同一个运行记录并成功重试，最终仅生成一条业务事件和一条通知。并发测试验证同一到期 Watchlist 只能被一个 worker claim；通知 dispatcher 另有并发 claim、重试、租约过期和失败终态测试。
- 标注变更检测集位于 `tests/dmf_change_labels.json`，包含 6 组人工标注的合成前后快照，涵盖新增、消失、多字段变化、规范化后无变化、重复身份和缺少身份。测得 **TP=4、FP=0、FN=0，Precision=1.000、Recall=1.000**。样本是小规模合成集，不代表真实 FDA 数据上的泛化准确率；空查询排除 baseline 比较由 History 状态测试单独验证。
- 运行状态接口 `GET /api/watchlists/status` 汇总活跃/暂停关注项、到期与运行中的任务、近 7 天成功率和 P50/P95 执行耗时、未确认事件及通知队列状态；Watchlist 页面展示汇总、每项下次检查时间和连续失败次数。成功率以 `changed`/`no_change` 为成功，以查询失败、历史失败和身份歧义为失败；运行中任务不进入成功率分母。
- 回归结果：DMF、History、Watchlist、通知和 Workflow 相关测试 **75 passed**；`tests/test_agent_watchlist.py` 单独运行 **11 passed**。前端 `app.js` 通过 `node --check`。测试有一条 Starlette/httpx 弃用警告。
- 将这两组测试放在同一个 pytest 进程时，出现过一个 Agent 日志捕获断言失败（`caplog` 未捕获日志）；该 Agent 测试文件单独运行 11 项通过。此测试顺序交互尚未定位，故仍应分组运行并保留此记录。
- 状态摘要基于持久化运行和通知记录，不是 worker 进程心跳；页面能显示积压到期任务和最近运行情况，但不能单独证明 worker 进程当前存活。

可重复运行：

```powershell
C:\BITrusted\work\python.exe -m pytest tests\test_dmf_collection_status.py tests\test_dmf_history_repository.py tests\test_dmf_query_history.py tests\test_dmf_query_service.py tests\test_dmf_schemas.py tests\test_dmf_watchlist_repository.py tests\test_dmf_watchlist_service.py tests\test_dmf_watchlist_settings.py tests\test_dmf_watchlist_state.py tests\test_dmf_watchlist_web_routes.py tests\test_dmf_monitoring_e2e.py tests\test_dmf_change_detection_metrics.py tests\test_notification_dispatcher.py tests\test_notification_migration.py tests\test_notification_worker.py tests\test_watchlist_workflow.py -q
C:\BITrusted\work\python.exe -m pytest tests\test_agent_watchlist.py -q
node --check src\web\static\app.js
```