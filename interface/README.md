# Interface

`interface/` 是这个仓库里的轻量后端 + Lite 前端目录，负责把多用户 Hermes 运行时包装成统一的网页入口。

## 当前职责

- 认证：使用 `interface` 自己的 SQLite 用户库
- 注册：创建 signup job，由后台 worker 执行用户开通流程
- 会话列表/消息：直接读取每个用户自己的 Hermes `state.db`
- 聊天：代理对应用户 Hermes 的 `/v1/chat/completions`
- 模型：代理对应用户 Hermes 的 `/v1/models`
- 文件树/下载/上传：由 `interface` 自己提供
- 展示态消息：把页面展示用 transcript 持久化到 `interface.db`
- 会话归档：后台定时把旧会话归档到 `archive.db`
- 前端：Lite 页面位于 `interface/static/lite/`

## 目录

- `app.py`
  FastAPI 入口；同时包含 signup worker 和归档调度逻辑
- `auth_db.py`
  网页用户、密码、signup jobs
- `display_store.py`
  页面展示态 transcript 持久化
- `archive_store.py`
  归档会话和归档运行记录
- `mapping.py`
  加载 `users_mapping.yaml` 并解析每用户 Hermes 目标
- `hermes_service.py`
  写入每用户 `~/.hermes/config.yaml`、`.env` 和 systemd service
- `requirements.txt`
  最小运行依赖
- `static/lite/`
  Lite 前端页面、样式、脚本、图标

## 依赖的数据源

- `users_mapping.yaml`
- `interface/data/interface.db`
- `interface/data/archive.db`
- 每用户 `~/.hermes/state.db`

## 关键环境变量

- `POTATO_AGENT_MAPPING_PATH`
- `INTERFACE_AUTH_DB`
- `INTERFACE_SESSION_SECRET`
- `INTERFACE_SESSION_TTL_SECONDS`
- `INTERFACE_MAX_UPLOAD_BYTES`
- `INTERFACE_UPLOAD_DIR_NAME`
- `INTERFACE_ARCHIVE_RETENTION_DAYS`
- `INTERFACE_ARCHIVE_SCHEDULE_HOUR`

说明：

- 如果不设置 `INTERFACE_SESSION_SECRET`，进程启动时会临时生成一个随机值；生产环境通常应该固定它
- 上传文件会保存到每用户工作区下的 `.<INTERFACE_UPLOAD_DIR_NAME>` 目录，默认是 `.potato-interface-uploads/`

## 当前边界

- Hermes 当前在线只开了 API server，没开 `web_server`，所以会话列表不是走 Hermes HTTP，而是直接读 `state.db`
- `interface` 当前默认假设自己能够读取各用户的 home、`work` 和 `.hermes/state.db`
- signup worker 会调用系统级用户开通逻辑；如果进程权限不足，注册任务会失败
- `users_mapping.yaml` 里仍保留一些历史 `openwebui_*` 字段；`interface` 运行时不会使用它们

部署方式、systemd 启动、模型配置和根目录用户管理脚本的具体用法，请看仓库根目录 `README.md`。
