# Enterprise Research Agent

面向研究任务的 Web Agent，提供对话式工具调用、文档分析、DMF 查询与导出、
会议知识库检索，以及请求级 Trace 和模型用量记录。

## 部署方式

使用 Docker Compose 在单台 Linux 服务器运行：

```text
浏览器 → Caddy（访问密码 / HTTPS）→ FastAPI Web Agent → PostgreSQL
                                         ↓
                              外部模型服务与持久化文件
```

**完整步骤见[中文部署说明](docs/DEPLOYMENT.md)。**

- [compose.yaml](compose.yaml)：应用、数据库、网站入口与持久化卷。
- [运行配置示例](docker/deployment.env.example)：复制并填入本地配置，不提交真实值。
- [Web 镜像](docker/Dockerfile.web)：运行现有 Python 应用，不包含本地知识库和凭证。
- 默认仅监听本机，网站受密码保护。域名配置完成后可启用自动 HTTPS。
- 未配置模型也可启动页面与健康检查；真正问答、解析和索引功能仍需各自服务配置，
  不会生成模拟结果。

本部署面向少量可信用户共享工作区，不是具有独立租户隔离的公开 SaaS。
共享访问密码不代表应用内各用户拥有独立资料权限。不要向访客开放企业内部资料。

## 本地 Python 运行

建议使用独立虚拟环境：

```powershell
python -m pip install -r requirements.txt
python -m alembic upgrade head
python -m uvicorn src.main:app --host 127.0.0.1 --port 8010
```

打开 `http://127.0.0.1:8010`。本地默认配置与 Compose 不同：未启用 PostgreSQL
检查点时，对话图状态保存在内存中，进程重启后不保证完整恢复。
不要将开发服务器直接暴露到公网。已有 `.env` 会被本地 Python 入口读取，
可能影响数据库和服务设置；Compose 镜像禁用自动读取 `.env`。

## 相关说明

- [Web 工作区](docs/WEB_WORKSPACE.md)
- [PEC 知识库](docs/PEC_KNOWLEDGE.md)
- [上下文预算与最终回答协议](docs/AGENT_CONTEXT_BUDGET.md)
- [Trace 与 Usage 更新](docs/OBSERVABILITY_UPDATE.md)

## 公开发布前

确认代码和资料允许公开，检查 Git 历史及文件中是否存在凭证和企业信息。
构建上下文不包含本地资料、索引、上传、数据库、日志或生成结果，不等于整个
仓库及历史已经通过公开发布审查。本次没有上传 GitHub、推送镜像或部署公网服务。

原有 `docker/Dockerfile`、Helm Chart 和 Jenkins 配置属于旧企业部署模板，
本套独立 Compose 不使用它们，也没有自动改造原 CI 流程。
