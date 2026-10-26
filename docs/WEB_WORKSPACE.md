# Research Workspace

前端位于 `src/web/templates/index.html`、`src/web/static/app.js` 和
`src/web/static/style.css`，使用本地固定版本 Lucide 图标，无需 Node 构建。

## 当前边界

- 对话继续使用现有 `/api/agent/sessions` 接口。
- 本页打开的对话、草稿、资料选择和下载记录保存在页面内存中；刷新不恢复历史。
- 会话内 PPTX 上传作为 Project Slide 源文件，发送生成请求时注入当前选中的 `file_id`。
- 会议知识库上传是独立入口，不复用会话文档上传。
- DMF 关注清单使用现有 `/api/watchlists` 资源接口。
- 知识库管理接口尚未接入；此次按要求仅完成前端，未修改新替换的知识库模块。
- 后端不可用时显示错误，禁用上传和入库操作，不显示虚构数据。

## 知识库接口约定

以下是前端需要的接口约定，不代表当前后端已经实现。

| 接口 | 请求 | 响应 |
| --- | --- | --- |
| `GET /api/knowledge/sources` | 无 | `{sources: [...], scopes: [...]}` |
| `GET /api/knowledge/status` | 无 | 索引概况对象 |
| `POST /api/knowledge/sources` | multipart：`folder`、一个或多个 `files` | `{uploaded: [相对路径]}` |
| `GET /api/knowledge/jobs` | 无 | `{jobs: [...]}`，按新到旧排序 |
| `POST /api/knowledge/jobs` | `{kind: "check" 或 "index", scope, confirmed}` | 已接受的任务对象，建议 HTTP 202 |

资料字段：`path`、`filename`、`format`、`state`、`chunk_count`。
`state` 支持 `pending`、`indexed`、`changed`、`missing`。
`scopes` 为可选择的相对文件夹路径。

概况字段：`file_count`、`chunk_count`、`embeddings_ready`、`updating`、
`updated_at`、`embedding_model`、`sources_dir`、`index_dir`。

任务字段：`id`、`kind`、`scope`、`status`、`created_at`、`finished_at`、
`result`、`error`。状态支持 `queued`、`running`、`succeeded`、`failed`。
解析检查的 `result.files` 每项包含 `path`、`status`（`ok` / `empty` / `failed`）、
`chunks` 和可选 `error`。
入库结果包含 `added`、`updated`、`removed`、`skipped` 路径数组，
`chunk_count` 和 `embedded_chunks`。

前端只有在实际存在运行任务且用户停留在知识库页时刷新任务状态，
不模拟百分比。入库前弹窗确认范围，取消不发送请求。
页面按“当前服务运行记录”展示任务；如后端提供持久化任务，需要调整此标签。

后续后端适配须确保范围校验、同名保护、上传大小限制、索引写入互斥和错误脱敏。
不能仅靠前端的 `confirmed` 字段或禁用按钮作为服务端权限控制。
此次入库约定不自动更新独立离线 Topic 库。

## 预览与验证

正常应用入口：`python -m uvicorn src.main:app --host 127.0.0.1 --port 8010`。
应用页面和健康检查可在未配置外部模型及 MinerU 凭证时启动。相应外部服务
操作仍需真实配置，缺少配置时明确返回错误，不生成模拟结果。
容器部署、访问密码、数据库和公网 HTTPS 步骤见[部署说明](DEPLOYMENT.md)。

独立静态预览：

```powershell
python -m http.server 8011 --bind 127.0.0.1 --directory src/web
```

打开 `http://127.0.0.1:8011/templates/index.html`。
静态服务不提供业务 API，出现连接失败是预期状态，不能用于实际生成或入库。

本轮验证包含脚本语法、DOM 接线、编辑器诊断及桌面/手机浏览器布局。
会话切换、PPT 选择、确认、下载链接、知识库上传/确认、关注清单暂停与记录弹窗，
使用浏览器内隔离接口响应测试；不构成新知识库后端或真实模型的端到端验收。