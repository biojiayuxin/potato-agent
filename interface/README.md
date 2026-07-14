# Interface

`interface/` 是这个仓库里的轻量后端 + Lite 前端目录，负责把多用户 Hermes 运行时包装成统一的网页入口。

## 当前职责

- 认证：使用 `interface` 自己的 SQLite 用户库
- 注册：创建 signup job，由后台 worker 执行用户开通流程
- 会话列表/消息：直接读取每个用户自己的 Hermes `state.db`
- 聊天：通过受认证的 `tui_gateway` bridge 接入每用户会话化交互后端
- 模型：通过 `tui_gateway` 读取当前模型/模型列表
- 文件树/下载/上传：由 `interface` 自己提供
- 展示态消息：把页面展示用 transcript 持久化到 `interface.db`
- 会话归档：后台定时把旧会话归档到 `archive.db`
- 前端：Lite 页面位于 `interface/static/lite/`
- 空间转录组查看器：公开页面 `/spatial`，数据从 `/srv/spatial_data/current` 只读加载
- WGCNA 共表达网络查看器：公开页面 `/wgcna`，运行时通过 `WGCNA_DATABASE_URL` 查询 PostgreSQL
- Bulk RNA-Seq 表达查看器：公开页面 `/bulk-rnaseq`，数据从
  `/srv/bulk_rnaseq/current/bulk_rnaseq.sqlite` 只读加载
- Genome Browser：公开页面 `/genome-browser`，数据从
  `/mnt/data/public_data/Genome_browser_DB` 只读加载

## 目录

- `app.py`
  FastAPI 入口；同时包含 signup worker 和归档调度逻辑
- `tui_gateway_bridge.py`
  每登录用户一个 `tui_gateway` 子进程的 bridge 与 JSON-RPC 转发
- `auth_db.py`
  网页用户、密码、signup jobs
- `display_store.py`
  页面展示态 transcript 持久化
- `archive_store.py`
  归档会话和归档运行记录
- `mapping.py`
  加载 `users_mapping.yaml` 并解析每用户 Hermes 目标
- `hermes_service.py`
  写入每用户 `~/.hermes/config.yaml`、`.env` 和 systemd service；模型凭据写为本地 proxy token
- `model_proxy.py`
  root/systemd 运行的本地 OpenAI-compatible model proxy，负责校验 `{username}-local-token` 并转发到真实上游
- `spatial_viewer.py`
  空间转录组查看器的公开 FastAPI router；只读查询外部数据目录
- `wgcna_viewer.py`
  WGCNA 共表达网络查看器的公开 FastAPI router；只读查询 PostgreSQL
- `bulk_rnaseq_viewer.py`
  Bulk RNA-Seq 表达查看器的公开 FastAPI router；只读查询外部 SQLite
- `genome_browser.py`
  Genome Browser 的公开 FastAPI router；只读加载 bgzip FASTA/GFF3 及索引文件
- `build_bulk_rnaseq_db.py`
  从整理后的 bulk RNA-Seq TSV 构建只读 SQLite；默认排除非马铃薯材料
- `requirements.txt`
  最小运行依赖
- `static/lite/`
  Lite 前端页面、样式、脚本、图标
- `static/spatial/`
  空间转录组查看器前端页面、样式、脚本、图标
- `static/wgcna/`
  WGCNA 共表达网络查看器前端页面、样式、脚本和 vendor 资源
- `static/bulk_rnaseq/`
  Bulk RNA-Seq 表达热图前端页面、样式和脚本
- `static/genome_browser/`
  Genome Browser 前端页面、样式、脚本和 JBrowse vendor 资源

## 依赖的数据源

- `users_mapping.yaml`
- `model_proxy.yaml`
- `interface/data/interface.db`
- `interface/data/archive.db`
- 每用户 `~/.hermes/state.db`
- 空间转录组数据目录，默认 `/srv/spatial_data/current`
- WGCNA PostgreSQL 数据库，默认通过 `WGCNA_DATABASE_URL` 配置
- Bulk RNA-Seq SQLite 数据库，默认 `/srv/bulk_rnaseq/current/bulk_rnaseq.sqlite`
- Genome Browser 数据库，默认 `/mnt/data/public_data/Genome_browser_DB`

## 关键环境变量

- `POTATO_AGENT_MAPPING_PATH`
- `INTERFACE_AUTH_DB`
- `INTERFACE_SESSION_SECRET`
- `INTERFACE_SESSION_TTL_SECONDS`
- `INTERFACE_MAX_UPLOAD_BYTES`
- `INTERFACE_FILE_BROWSER_MODE`
- `INTERFACE_UPLOAD_DIR_NAME`
- `INTERFACE_ARCHIVE_RETENTION_DAYS`
- `INTERFACE_ARCHIVE_SCHEDULE_HOUR`
- `SPATIAL_VIEWER_DATA_ROOT`
- `WGCNA_DATABASE_URL`
- `BULK_RNASEQ_DB_PATH`
- `GENOME_BROWSER_DB_ROOT`

说明：

- 如果不设置 `INTERFACE_SESSION_SECRET`，进程启动时会临时生成一个随机值；生产环境通常应该固定它
- `INTERFACE_FILE_BROWSER_MODE` 默认为 `home_only`
  - `home_only`：Files 面板只显示 `~/`，不显示目录输入框
  - `home_and_public_data`：在 `~/` 之外额外允许解析到 `/mnt/data/public_data` 的路径，仍不显示目录输入框
  - `user_readable`：显示目录输入框，允许打开任意当前 Linux 用户有权限读取的目录
- `INTERFACE_MAX_UPLOAD_BYTES` 默认为 200 MB，用于限制单个上传请求，并限制单条消息的附件总大小
- 上传文件会保存到每用户工作区下的 `.<INTERFACE_UPLOAD_DIR_NAME>` 目录，默认是 `.potato-interface-uploads/`
- `SPATIAL_VIEWER_DATA_ROOT` 默认 `/srv/spatial_data/current`；建议目录 owner 为 `root`、group 为 `potato-interface`，目录 `0750`、文件 `0640`
- `WGCNA_DATABASE_URL` 指向 WGCNA PostgreSQL 数据库，例如 `postgresql:///potato_wgcna?host=/var/run/postgresql`
- `BULK_RNASEQ_DB_PATH` 默认 `/srv/bulk_rnaseq/current/bulk_rnaseq.sqlite`；建议 `/srv/bulk_rnaseq` owner 为 `root`、group 为 `potato-interface`，目录 `0750`、SQLite 文件 `0640`
- `GENOME_BROWSER_DB_ROOT` 默认 `/mnt/data/public_data/Genome_browser_DB`；目录内 FASTA/GFF3 需要是 bgzip 压缩并带 `.fai/.gzi/.tbi` 索引

## 当前边界

- Hermes 每用户运行时由 systemd 管理；会话列表不是走 Hermes HTTP，而是直接读 `state.db`
- `interface` 当前默认假设自己能够读取各用户的 home、`work` 和 `.hermes/state.db`
- signup worker 会调用系统级用户开通逻辑；如果进程权限不足，注册任务会失败
- `users_mapping.yaml` 里仍保留一些历史 `openwebui_*` 字段；`interface` 运行时不会使用它们

## 新增的 TUI Gateway Bridge 骨架

- `GET /api/tui/ws`
  - 受登录态保护的 WebSocket
  - 每个登录用户首次连接时，`interface` 会以该用户自己的 Linux 身份拉起一个 `python -m tui_gateway.entry` 子进程
  - 浏览器通过 WebSocket 发送 `{id, method, params}`，`interface` 负责转成 `tui_gateway` JSON-RPC，并把事件流转回浏览器
- 这一层就是 Lite 前端当前的主聊天链路

### Lite 前端当前状态

- Lite 前端默认已切换到 `tui_gateway` 聊天主链路
- Lite 前端不再保留浏览器侧 `api_server` 回退开关，聊天固定走 `tui_gateway`

### 已验证的最小 bridge 探针

- 脚本：`python3 interface/test_tui_bridge.py <mapping_username>`
- 当前已确认：
  - bridge 能以目标 Linux 用户身份启动
  - `session.create` 能返回 live `tui_gateway` session id
  - `prompt.submit` 能进入 streaming 状态
  - 能收到 `message.delta`、`reasoning.delta`、`message.complete`

部署方式、systemd 启动、模型配置和根目录用户管理脚本的具体用法，请看仓库根目录 `README.md`。
