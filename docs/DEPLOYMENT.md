# Docker Compose 单机部署

## 目标与边界

先准备一套将来可放到 Linux 云服务器运行的配置：现有 FastAPI 页面和 API、
PostgreSQL、带共享访问密码的 Caddy 入口，以及文件持久化。不迁移 LLM 接口，
不上传代码、镜像或内部资料，不自动调用 Apollo、MinerU 或其他付费服务。

本方案适合少量可信用户共享工作区。访问密码保护整站，包括 API 和健康检查；
知道密码的用户仍可能访问共享的知识库和管理功能，不是独立账户或多租户授权。
长期记忆及可信代理身份默认关闭，代理剥离客户端传入的身份头与访问密码。

默认仅监听 `127.0.0.1`，用于本机准备。公网服务器、域名和 HTTPS 配置完成后，
才可让其他人通过链接访问。无需 Node 构建，前端由同一个 FastAPI 应用提供。

## 一、准备环境

- Docker Engine，以及支持可选 `env_file` 的 Docker Compose 2.24 或更新版本。
  Windows 本地可用 Docker Desktop，但必须启动 Linux 容器引擎。
- 未来服务器应能拉取 Python、PostgreSQL、Caddy 镜像并安装 Python 和系统依赖。
- 镜像包含 LibreOffice 和中文字体，用于现有 Office 文档预览；构建需要时间和磁盘。
- 当前不使用 GPU，不自动启动索引任务、关注清单定时轮询或邮件发送进程。

旧企业 `docker/Dockerfile`、Helm 和 Jenkins 模板不参与本方案。构建上下文是
仓库根目录，使用新的 `docker/Dockerfile.web`，不是 `docker` 子目录。

如果 Docker Hub 无法连接，可在部署配置中显式选择官方镜像的 ECR Public
分发入口，不需要修改全局代理或使用来源不明的镜像站：

```dotenv
PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.12-slim-bookworm
POSTGRES_IMAGE=public.ecr.aws/docker/library/postgres:17-alpine
CADDY_IMAGE=public.ecr.aws/docker/library/caddy:2-alpine
```

以上只改变下载入口，不会把项目代码推送到镜像仓库。默认仍使用 Docker Hub；
实际可用性取决于部署网络。Web 镜像固定标记为
`enterprise-research-agent-app:latest`，可以在隔离 Compose 项目中使用同一构建结果。

## 二、创建本地配置

在仓库根目录执行：

```powershell
Copy-Item docker\deployment.env.example docker\deployment.env
```

Linux 对应命令为 `cp docker/deployment.env.example docker/deployment.env`。
`docker/deployment.env` 已忽略，不提交 Git，也不写入镜像。

生成随机数据库密码：

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

将输出填入 `POSTGRES_PASSWORD`。本配置将密码用于数据库连接 URL，因此使用
十六进制格式，不直接填含 `@`、`:`、`/`、`%` 等特殊字符的密码。
默认数据库和用户名均为 `research`。使用持久化卷后，单独修改配置不会自动
修改数据库里的现有密码，轮换时需要同步修改数据库角色。

生成网站密码的 bcrypt 哈希：

```powershell
docker run --rm -it caddy:2-alpine caddy hash-password
```

交互输入你选择的访问密码，将生成的哈希填入 `APP_PASSWORD_HASH`，**保留配置
示例中的单引号**，避免 `$` 被 Compose 当成变量展开。不把明文网站密码写入文件。
`APP_USERNAME` 默认 `research`，如修改请使用字母、数字、下划线或连字符。

`POSTGRES_PASSWORD` 和 `APP_PASSWORD_HASH` 都是必填项，缺少时 Compose 拒绝继续，
没有内置默认密码。数据库密码属于服务端秘密；网站哈希也不要公开分享。

其余模型和 MinerU 配置可以保持为空：

- 首页、静态资源、健康检查和会话创建不需要模型凭证。
- 缺少 Apollo 配置的模型请求、缺少 MinerU 配置的解析请求明确返回 503，
  不使用假凭证，不返回模拟研究答案。
- 已填非空但错误的凭证仍可能在真实调用时被供应商拒绝，配置检查不是连通性检查。
- 后续改接自己的 LLM 仍需修改现有接口实现；本次没有假装已支持任意模型供应商。
- 开启问答、Embedding、Rerank、解析或入库之后，仍可能产生外部调用费用。
  共享访问密码不是调用预算或限流系统。

不要把原有企业 `.env` 整份复制到该配置，也不要将它挂载进容器。
部署命令始终显式指定 `--env-file`，避免误用仓库根目录 `.env`。

## 三、检查、构建和启动

```powershell
docker compose --env-file docker\deployment.env config --quiet
docker compose --env-file docker\deployment.env build app
docker compose --env-file docker\deployment.env up -d --wait
docker compose --env-file docker\deployment.env ps
```

不要将不带 `--quiet` 的完整配置输出粘贴到公开 Issue，其中可能包含密码和模型密钥。

默认地址为 `http://127.0.0.1:8080`。浏览器会要求输入网站用户名和明文访问密码。
本地 HTTP 仅限回环地址验证，不能用于向公网传输访问密码。

启动顺序为：数据库就绪 → Alembic 迁移 → 单进程 Uvicorn → 应用健康 → Caddy。
迁移或启动失败会显示错误，代理不会把页面正常可用作为兜底结果。
健康检查不调用外部模型，也不代表模型服务可用。

检查入口与日志：

```powershell
curl.exe --user research http://127.0.0.1:8080/health
docker compose --env-file docker\deployment.env logs --tail 100 app proxy db
```

`curl` 交互提示输入密码，不在命令历史中写明文密码。未认证访问应返回 401；
认证成功的健康响应应包含 `status: "ok"`。

## 四、以后部署到公网

有 Linux 服务器和域名后，先把域名 A/AAAA 记录指向正确服务器地址，再调整：

```dotenv
SITE_ADDRESS=research.example.com
BIND_ADDRESS=0.0.0.0
HTTP_PORT=80
HTTPS_PORT=443
```

示例域名必须替换成你自己的域名。防火墙和云安全组只放行需要的 Web 端口
80/443；数据库 5432 和应用 8000 不映射到宿主机，不对外开放。
默认 `0.0.0.0` 是 IPv4 监听配置，需要 IPv6 时还需核实宿主机和 Docker 网络。

在 Linux 仓库根目录执行：

```sh
docker compose --env-file docker/deployment.env up -d --build --wait
```

Caddy 在真实域名、DNS、80/443 入站及证书申请出站正常时自动申请和续期 HTTPS
证书。仅修改域名不保证证书一定成功；检查代理日志后再分享
`https://你的域名`。证书数据保存在持久化卷中。

只运行一个应用实例和一个 Uvicorn Worker。当前后台解析任务、知识库运行任务表、
会话锁等仍有进程内状态，不能仅靠增加副本实现可靠扩容。
PostgreSQL 检查点保留图状态，但不会使所有后台任务和前端页面状态都自动持久化。

## 五、数据与升级

| 命名卷 | 保存内容 |
| --- | --- |
| `postgres_data` | 会话元数据、PostgreSQL 图检查点及启用功能的数据库内容 |
| `app_storage` | 上传、解析结果和临时文件 |
| `app_data` | 知识库源资料、索引、预览等 |
| `app_outputs` | 导出、生成文件和工具产物 |
| `app_logs` | 审计、Trace 和业务文件日志 |
| `app_pdf` | 现有 PDF 中间产物目录 |
| `caddy_data`、`caddy_config` | 证书与代理运行数据 |

这些卷不会自动导入你本地的 PEC 文档、索引或历史数据库。新环境的知识库为空，
必须只导入有权使用的资料。`.dockerignore` 使用允许列表，不把本地资料、
凭证、数据库、日志和验收业务答案装进镜像。

停止使用 `docker compose --env-file docker/deployment.env down`，不加 `--volumes`。
删除卷会丢失持久化数据。固定使用同一项目名 `enterprise-research-agent`，
迁移服务器时同时备份数据库和所需文件卷；数据库备份不是文件卷备份。

更新前备份，随后重新构建并启动：

```sh
docker compose --env-file docker/deployment.env up -d --build --wait
```

迁移随启动执行。数据库迁移未必可逆，不能只切回旧镜像就认为已经完整回滚。
已存在的数据库也不要在未备份时覆盖或重置。

## 六、验证边界

部署测试覆盖无模型及 MinerU 凭证的应用启动、迁移、页面、静态资源、会话创建、
明确的未配置错误、Compose 必填项、端口隔离和持久化配置。
离线测试不调用真实模型，不代表容器或公网已完成验收。

首次准备配置时，Linux Docker 引擎未启动，构建未能执行。后续继续验收时，
引擎已可用，现已真实完成镜像构建、三服务启动、密码校验、本地 HTTPS 和
容器重建恢复验证。未申请云服务器或域名，仍没有公网访问地址。

本轮验证记录：

- 部署、Web、配置、Agent Eval、协议和 Trace 共 21 个测试模块合并运行，
  **310 项通过**。测试使用隔离数据库和离线配置，不读取本机业务凭证。
  无凭证启动案例在独立进程内测试，并阻止外部网络连接。
- 使用真实 Uvicorn 在本机回环地址临时启动，首页、`/health`、JavaScript
  均实际 HTTP 200；已停止该临时进程。这不是 Docker 容器验收。
- 初次构建被未启动的 Docker 引擎阻断；继续验收时 Docker Hub 网络不可达，
  改用上文列出的官方 ECR Public 分发入口，成功构建 Web 镜像并启动容器。
  没有改变系统网络、上传代码或发布镜像。
- 额外运行通知配置测试时，有 3 项因原有 `StructuredIntentModel` 替身不支持
  `include_raw` 而失败。本轮未修改该通知路径、删除用例或放宽断言；这是另行
  保留的测试兼容性问题，不宣称整个项目测试全部通过。

### 2026-10-26：真实容器验收

- [HTTP 容器验收报告](../outputs/deployment-validation/20261026T044244Z/report.md)：
  14 项检查通过。
- [HTTPS 与持久化验收报告](../outputs/deployment-validation/20261026T044448Z/report.md)：
  17 项检查通过，旁边保存对应 `report.json`。
- 使用独立 Compose 项目和临时随机密码，无真实模型凭证，不导入本机 PEC 资料，
  外部模型请求为零；不是把本地 Python 启动成功当作容器启动成功。
- 未认证和错误密码访问返回 401；正确密码访问首页、JavaScript 和健康检查
  成功，实际 HTTP 会话创建成功。
- HTTPS 使用 Caddy 为 `localhost` 生成的本地证书；测试信任该临时本地 CA，
  并验证证书链和主机名，没有关闭 TLS 验证。这不等于公网可信证书已签发。
- Web 进程 UID 为 10001，`pip check` 通过；镜像中不包含根目录 `.env`
  和本地部署配置；应用及数据库不映射宿主端口。
- 强制重建数据库、应用和代理容器后，会话元数据和图检查点仍可读取，
  五个应用文件卷中的测试文件完整保留，健康接口仍可访问。
- 验收后仅清理本次独立项目的容器、测试卷和临时本地 CA，不影响已有业务数据。
  没有留下正在运行的网站，也没有生成可复用的明文访问密码。
- 本机已构建的 Web 镜像 ID：
  `sha256:13d61ba13cb725d5ae473d525d21d15154260adbeefbfdec092e21b6d2bdb8de`。
  官方下载入口配置新增后，5 项部署单元测试再次通过。

剩余步骤是准备服务器和域名、设置你自己的访问密码、按上述命令启动，
随后验证公网 DNS、可信 HTTPS 和实际外部访问；模型问答仍等后续接入自己的
LLM 后单独验收。当前不能把本地容器验收报告称为公网部署完成。
