# Agent 长期记忆

## 范围

第一阶段只保存用户明确要求记住的结构化偏好，例如语言、称谓和输出格式。普通对话不会自动写入记忆；记忆内容不保存完整对话、DMF 原始 payload、凭据或内部路径。

长期记忆与 LangGraph checkpoint 分开：记忆是用户级偏好，checkpoint 是可恢复的会话状态。记忆只能作为低信任数据注入模型上下文，不能授予权限、覆盖当前请求或单独触发工具副作用。

## 生产配置

生产环境使用 PostgreSQL，并先执行迁移：

```powershell
$env:DATABASE_URL = "postgresql+psycopg://..."
python -m alembic upgrade head
```

启用功能：

```text
AGENT_MEMORY_ENABLED=true
AGENT_CHECKPOINT_ENABLED=true
AGENT_TRUST_PROXY_IDENTITY=true
```

反向代理必须在转发前清除客户端同名 header，并注入已认证的用户和租户主体：

```text
X-Authenticated-User
X-Authenticated-Tenant
```

应用不信任请求体中的 `user_id`。未启用 `AGENT_TRUST_PROXY_IDENTITY` 时，跨会话记忆 API 会拒绝请求，Web session 仍保持匿名单会话行为。

## 本地开发

默认仍使用进程内 checkpoint。可以使用 SQLite 临时验证记忆仓储，但不应把 SQLite 当作多副本共享状态；多 worker 或多副本部署必须使用 PostgreSQL。

启用记忆后，用户可以通过 Agent API 显式列出、写入和忘记记忆。部署前执行 `alembic upgrade head`，不要依赖运行时自动建表。