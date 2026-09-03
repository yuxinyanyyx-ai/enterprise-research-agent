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