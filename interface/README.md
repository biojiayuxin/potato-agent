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
- 聊天分享：生成登录后导入的不可变快照链接，并在独立私有 SQLite 中维护有效期、接收者额度和导入幂等状态
- 会话归档：后台定时把旧会话归档到 `archive.db`
- 前端：Lite 页面位于 `interface/static/lite/`
- 空间转录组查看器：公开页面 `/spatial`，数据从 `/srv/spatial_data/current` 只读加载
- WGCNA 共表达网络查看器：公开页面 `/wgcna`，运行时通过 `WGCNA_DATABASE_URL` 查询 PostgreSQL
- Bulk RNA-Seq 表达查看器：公开页面 `/bulk-rnaseq`，数据从
  `/srv/bulk_rnaseq/current/bulk_rnaseq.sqlite` 只读加载
- Gene Catalog：公开页面 `/genes` 及 `/genes/<gene-id>`，数据从
  `/srv/gene_catalog/current/gene_catalog.sqlite` 以 SQLite immutable read-only mode 加载
- Pan-genome Orthogroups：公开 API `/api/pan-genome/`，数据从
  `/srv/pan_genome/current/pan_genome.sqlite` 以 SQLite immutable read-only mode 加载；构建和发布见
  [`PAN_GENOME.md`](PAN_GENOME.md)
- Genome Browser：Genomes 二级页面 `/genomes/browser`（旧 `/genome-browser`
  地址保留兼容跳转），数据从
  `/mnt/data/public_data/Genome_browser_DB` 只读加载
- Daily Updates：未登录页公开展示 PubMed 马铃薯研究，由独立 systemd worker 每日生成双语总结，
  Interface 从 `/srv/daily_updates/data/daily_updates.sqlite` 只读加载；部署和迁移见
  [`DAILY_UPDATES.md`](DAILY_UPDATES.md)
- Dashboard：公开英文页面 `/dashboard`，展示已记录 Token 用量与两类更新。

## Public Dashboard

部署依赖、两侧代码发布与重启顺序、匿名验收和更新记录维护见
[`HPC_DEPLOYMENT.md` 的 16.2 节](../HPC_DEPLOYMENT.md#162-公开-dashboard)。Dashboard 无需独立服务或定时任务。

`/api/dashboard/usage` 固定返回北京时间截至昨日的 30 个完整自然日，前端从同一份数据计算
7/30 天概览。Interface 复用 admin 用户目录，包含当前正式用户、临时用户和已清理临时用户，
排除服务账户；公开响应仅包含日期、时区、可用状态及 Token 汇总。
代理侧 `/internal/admin/usage/daily` 复用内部认证与时间范围校验，通过只读 SQLite 查询按日期、
用户聚合，无数据库迁移。总量包含输入、输出、缓存读和缓存写，口径与 admin 相同；
页面展示总量、输入、输出和缓存读，不单独展示缓存写。
用量缓存在每个 Interface 进程中按北京时间日期失效，并合并并发请求；失败后冷却 60 秒。
缺失数据源返回 `unavailable`，完整空窗口返回零用量。

`/api/dashboard/resources` 复用 Genome Browser manifest、Gene Catalog 和 Bulk RNA-Seq
统计入口，仅输出计数及模块信息，缓存五分钟，各资源独立处理失败；Dashboard 页面不再请求此接口。
Potato Agent 更新直接读取 `static/lite/update-notes.json`；PotatoOmics 更新手工维护于
`static/dashboard/potato-omics-updates.json`，保留 `source_commits` 供溯源，运行时不读取 Git。

后端测试：`python -m pytest interface/test_dashboard.py`。
浏览器测试：`POTATO_DASHBOARD_BROWSER_TESTS=1 python -m pytest interface/test_dashboard_browser.py`，
需要 Playwright/Chromium，可通过 `POTATO_PLAYWRIGHT_EXECUTABLE` 指定浏览器，
`POTATO_DASHBOARD_SCREENSHOTS` 指定截图目录。浏览器测试使用模拟用量数据。

## 目录

- `app.py`
  FastAPI 入口；同时包含 signup worker 和归档调度逻辑
- `tui_gateway_bridge.py`
  每登录用户一个 `tui_gateway` 子进程的 bridge 与 JSON-RPC 转发
- `auth_db.py`
  网页用户、密码、signup jobs
- `display_store.py`
  页面展示态 transcript 持久化
- `chat_share_store.py`
  聊天分享快照、bearer token 哈希、接收者计数、导入 receipt 和限流账本
- `session_db_rpc.py`
  通过用户自己的 Linux 身份访问 Hermes `state.db`，并把分享快照导入为真实 `tui` 会话
- `archive_store.py`
  归档会话和归档运行记录
- `feedback_store.py`
  公开反馈提交的全站限流、内容存储和邮件投递元数据；不保存客户端 IP
- `mapping.py`
  加载 `users_mapping.yaml` 并解析每用户 Hermes 目标
- `hermes_service.py`
  写入每用户 `~/.hermes/config.yaml`、`.env` 和 systemd service；模型凭据写为本地 proxy token
- `model_proxy.py`
  由独立 `potato-model-proxy` systemd 身份运行的本地 OpenAI-compatible proxy，负责校验逐用户随机
  `pmp_...` token、执行配额并转发到真实上游
- `secret_config.py`
  从 systemd credential 或私有 `*_FILE` 加载 Interface session secret 和 Resend key
- `spatial_viewer.py`
  空间转录组查看器的公开 FastAPI router；只读查询外部数据目录
- `wgcna_viewer.py`
  WGCNA 共表达网络查看器的公开 FastAPI router；只读查询 PostgreSQL
- `bulk_rnaseq_viewer.py`
  Bulk RNA-Seq 表达查看器的公开 FastAPI router；只读查询外部 SQLite
- `gene_catalog.py`
  Gene Catalog 的公开页面和 `/api/v1/genes/` API；以 immutable read-only connection 查询外部 SQLite
- `build_gene_catalog_db.py`
  从经过审查的注释、序列、文献、相似性和功能预测来源构建版本化 Gene Catalog SQLite
- `pan_genome.py`
  Pan-genome Orthogroups 的公开只读 API router
- `build_pan_genome_db.py`
  将 OrthoFinder `Orthogroups.tsv` 规范化为带索引的版本化 SQLite
- `genome_browser.py`
  Genome Browser 的公开 FastAPI router；只读加载 bgzip FASTA/GFF3 及索引文件
- `import_genome_browser_assembly.py`
  校验、排序并索引单倍体 FASTA/GFF3，成功后原子更新 Genome Browser manifest 和元数据
- `build_bulk_rnaseq_db.py`
  从整理后的 bulk RNA-Seq TSV 构建只读 SQLite；默认排除非马铃薯材料
- `requirements.txt`
  直接依赖维护输入；不能用于生产安装
- `requirements-py312-linux-x86_64.lock`、`wheelhouse-py312-linux-x86_64.json`
  CPython 3.12/Linux x86_64 生产依赖的全量 hash lock 和精确 wheel inventory
- `static/lite/`
  Lite 前端页面、样式、脚本、图标
- `static/about/`、`static/shared/`
  公开 About 页面、架构图，以及所有公开页面共用的 Feedback 前端资源
- `static/spatial/`
  空间转录组查看器前端页面、样式、脚本、图标
- `static/wgcna/`
  WGCNA 共表达网络查看器前端页面、样式、脚本和 vendor 资源
- `static/bulk_rnaseq/`
  Bulk RNA-Seq 表达热图前端页面、样式和脚本
- `static/genes/`
  Gene Catalog 搜索、详情和 deep-link 前端页面、样式及脚本
- `static/genome_browser/`
  Genome Browser 前端页面、样式、脚本和 JBrowse vendor 资源

## 依赖的数据源

- `/var/lib/potato-agent/config/users_mapping.yaml`
- `/var/lib/potato-agent/config/model_proxy.yaml`
- `/var/lib/potato-agent/data/interface.db`
- `/var/lib/potato-agent/data/archive.db`
- `/var/lib/potato-agent/data/feedback.db`
- `/var/lib/potato-agent/model-proxy/usage.db`
- 每用户 `~/.hermes/state.db`
- 空间转录组数据目录，默认 `/srv/spatial_data/current`
- WGCNA PostgreSQL 数据库，默认通过 `WGCNA_DATABASE_URL` 配置
- Bulk RNA-Seq SQLite 数据库，默认 `/srv/bulk_rnaseq/current/bulk_rnaseq.sqlite`
- Gene Catalog SQLite 数据库，默认 `/srv/gene_catalog/current/gene_catalog.sqlite`
- Pan-genome SQLite 数据库，默认 `/srv/pan_genome/current/pan_genome.sqlite`
- Genome Browser 数据库，默认 `/mnt/data/public_data/Genome_browser_DB`
- Daily Updates 数据库，默认 `/srv/daily_updates/data/daily_updates.sqlite`

## 关键环境变量

- `POTATO_AGENT_STATE_DIR`
- `POTATO_AGENT_MAPPING_PATH`
- `POTATO_MODEL_PROXY_CONFIG_PATH`
- `POTATO_MODEL_PROXY_USAGE_DB`
- `INTERFACE_AUTH_DB`
- `INTERFACE_ARCHIVE_DB`
- `INTERFACE_FEEDBACK_DB`
- `INTERFACE_ENVIRONMENT`
- `INTERFACE_ALLOW_INSECURE_HTTP`
- `INTERFACE_BIND_HOST`
- `INTERFACE_SESSION_SECRET_FILE`
- `INTERFACE_SESSION_COOKIE_SECURE`
- `INTERFACE_RESEND_API_KEY_FILE`
- `INTERFACE_MAIL_FROM`
- `INTERFACE_MAIL_REPLY_TO`
- `INTERFACE_SESSION_TTL_SECONDS`
- `INTERFACE_MAX_UPLOAD_BYTES`
- `INTERFACE_FILE_BROWSER_MODE`
- `INTERFACE_UPLOAD_DIR_NAME`
- `INTERFACE_ARCHIVE_RETENTION_DAYS`
- `INTERFACE_ARCHIVE_STORAGE_RETENTION_DAYS`
- `INTERFACE_ARCHIVE_SCHEDULE_HOUR`
- `SPATIAL_VIEWER_DATA_ROOT`
- `WGCNA_DATABASE_URL`
- `BULK_RNASEQ_DB_PATH`
- `GENE_CATALOG_DB_PATH`
- `PAN_GENOME_DB_PATH`
- `GENOME_BROWSER_DB_ROOT`
- `GENOME_BROWSER_FEATURE_INDEX_PATH`
- `GENOME_BROWSER_SAMTOOLS`
- `DAILY_UPDATES_DB_PATH`

说明：

- 开发环境未设置 session secret 时会临时生成随机值；生产环境缺少 credential/私有文件、直接把 secret
  放进进程环境，或 secret 少于 32 bytes 时都会拒绝启动
- 生产默认 profile 使用 `INTERFACE_ALLOW_INSECURE_HTTP=false`、`Secure` Cookie 和
  `127.0.0.1:3000`，由 HTTPS 反向代理提供外部入口
- 无域名隔离测试服务器只有同时显式设置 `INTERFACE_ALLOW_INSECURE_HTTP=true`、
  `INTERFACE_SESSION_COOKIE_SECURE=false` 并通过 `INTERFACE_BIND_HOST` 指定一个明确的非 loopback IPv4
  时才能启动；该 profile 会明文传输密码、session 和聊天内容，只能用于可信隔离 LAN
- `INTERFACE_ARCHIVE_RETENTION_DAYS` 默认和生产 unit 均为 `99999`，即默认不开启常规自动
  会话归档；未经 owner 明确要求不得降低该值
- 归档正文默认保留 30 天；启动和每日归档任务都会清理更早的数据
- `INTERFACE_FILE_BROWSER_MODE` 默认为 `home_only`
  - `home_only`：Files 面板只显示 `~/`，不显示目录输入框
  - `home_and_public_data`：在 `~/` 之外额外允许解析到 `/mnt/data/public_data` 的路径，仍不显示目录输入框
  - `user_readable`：显示目录输入框，允许打开任意当前 Linux 用户有权限读取的目录
- `INTERFACE_MAX_UPLOAD_BYTES` 默认为 200 MB，用于限制单个上传请求，并限制单条消息的附件总大小
- 上传文件会保存到每用户工作区下的 `.<INTERFACE_UPLOAD_DIR_NAME>` 目录，默认是 `.potato-interface-uploads/`
- `POTATO_AGENT_STATE_DIR` 只为未显式配置的 mapping、运行时状态库、proxy 配置和 usage DB 推导默认前缀；
  它不会重定位 credentials、外部数据集、每用户状态、源码或 release。生产 unit 应使用各专用路径变量
- `SPATIAL_VIEWER_DATA_ROOT` 默认 `/srv/spatial_data/current`；建议目录 owner 为 `root`、group 为 `potato-interface`，目录 `0750`、文件 `0640`
- `WGCNA_DATABASE_URL` 指向 WGCNA PostgreSQL 数据库，例如 `postgresql:///potato_wgcna?host=/var/run/postgresql`
- `BULK_RNASEQ_DB_PATH` 默认 `/srv/bulk_rnaseq/current/bulk_rnaseq.sqlite`；建议 `/srv/bulk_rnaseq` owner 为 `root`、group 为 `potato-interface`，目录 `0750`、SQLite 文件 `0640`
- `GENE_CATALOG_DB_PATH` 默认 `/srv/gene_catalog/current/gene_catalog.sqlite`；建议 `/srv/gene_catalog` owner
  为 `root`、group 为 `potato-interface`，目录 `0750`、SQLite 文件 `0640`
- `PAN_GENOME_DB_PATH` 默认 `/srv/pan_genome/current/pan_genome.sqlite`；建议 `/srv/pan_genome` owner
  为 `root`、group 为 `potato-interface`，目录 `0750`、SQLite 文件 `0640`
- `GENOME_BROWSER_DB_ROOT` 默认 `/mnt/data/public_data/Genome_browser_DB`；目录内 FASTA/GFF3 需要是 bgzip 压缩并带 `.fai/.gzi/.tbi` 索引
- `GENOME_BROWSER_FEATURE_INDEX_PATH` 指向集中式多 assembly 特征索引；未设置时默认使用
  `$GENOME_BROWSER_DB_ROOT/feature_index.sqlite`
- `GENOME_BROWSER_SAMTOOLS` 可指定坐标序列 API 使用的 `samtools` 可执行文件，默认从 `PATH` 解析 `samtools`

## 公开 Feedback

- 所有公开页面加载 `/static/shared/feedback.css` 和 `/static/shared/feedback.js`，通过无需登录的
  `POST /api/feedback` 投递反馈；Lite 登录工作区实际显示时会隐藏入口，并关闭尚未成功提交的反馈弹窗
- 请求正文上限为 32 KiB；反馈正文必填且最多 5,000 字符，联系邮箱可选且最多 254 字符，来源仅接受站内绝对路径
- 邮件沿用 Resend 配置，收件人在 `interface/mailer.py` 中固定为 `jiayuxin@ynnu.edu.cn`；发件人使用
  `INTERFACE_MAIL_FROM`，Reply-To 只使用服务端 `INTERFACE_MAIL_REPLY_TO`，客户端不能指定收件人、
  发件人、主题或 Reply-To
- 全站采用滚动一小时最多 20 次的原子 SQLite claim；邮件发送失败也占用额度，超限返回 `429` 和
  `Retry-After`。该 API 不认证用户、不刷新运行时活跃时间，也不启动 Hermes 运行时
- `feedback.db` 以明文保存反馈正文、可选联系邮箱、提交 ID、`pending/sent/failed` 状态、Resend ID
  和时间戳，保留 30 天；不保存客户端 IP。数据库及其目录使用私有权限，日志仍不得包含反馈正文、
  联系邮箱或 Resend 响应正文

## 聊天分享

- 正式账号可以为已保存的聊天生成分享链接；接收者登录正式账号或使用 Quick Start 后，聊天会作为独立副本
  自动导入，不提供未登录公开预览
- 分享只复制页面可见的问答内容，不复制推理、工具过程、附件或原会话身份。有效期、限额、私有存储、删除、
  归档、账号清理和恢复规则统一见根目录 [`HPC_DEPLOYMENT.md`](../HPC_DEPLOYMENT.md) 的聊天分享章节

## 当前边界

- Hermes 每用户运行时由 systemd 管理；会话列表不是走 Hermes HTTP，而是直接读 `state.db`
- `interface` 只能按已认证用户的唯一 `mapping_username` 解析目标；mapping 中共享 Linux 用户、home、
  `HERMES_HOME/state.db`、systemd service、API port 或 proxy token 的条目会 fail closed
- `interface` 服务身份能够按 mapping 读取目标用户的 home、`work` 和 `.hermes/state.db`，普通用户之间不能
  互读；归档状态和正文查询也按当前 `mapping_username` 隔离
- 真实模型 API key 仅允许独立 proxy 身份读取；`potato-interface` 和普通 Hermes 用户都不应有权限
- proxy 专用 `usage.db` 只保存计量和配额，不保存认证表、聊天正文或 Interface session
- signup worker 会调用系统级用户开通逻辑；如果进程权限不足，注册任务会失败
- `users_mapping.yaml` 里仍保留一些历史 `openwebui_*` 字段；`interface` 运行时不会使用它们

## 生产部署要求

- Interface 生产 venv 只支持 CPython 3.12/Linux x86_64，并必须按
  `requirements-py312-linux-x86_64.lock` 使用 `--require-hashes --no-index` 从通过
  `wheelhouse-py312-linux-x86_64.json` 精确校验的 wheelhouse 安装；不得直接安装 `requirements.txt`
- 正式生产必须使用 HTTPS 反向代理、loopback listener 和 Secure Cookie；owner 明确批准的无域名隔离测试
  环境可使用根目录 README 记录的显式 HTTP site drop-in，不得改成 development 绕过生产 secret 检查
- HTTP profile 必须从实际 LAN origin 验证登录后刷新、Cookie 的 `Secure=false`/`HttpOnly=true`/
  `SameSite=Lax`/`Path=/` 和退出清除；不得记录 Cookie 值，完整步骤见根目录 README
- 宿主 `/proc` 必须持久化 `hidepid=2`；unit 级 `ProtectProc` 不能替代宿主隔离，必要的监控例外只能使用
  不包含普通用户或 Potato 服务身份的专用 GID
- session secret 和 Resend key 使用 root-only systemd credentials 或私有 `*_FILE`，不能放进 unit
  `Environment=`、argv、源码或日志
- `model_proxy.yaml` 必须为 `root:potato-model-proxy 0640`，专用 proxy 目录为
  `potato-model-proxy:potato-model-proxy 0700`，`usage.db` 为 `0600`
- 首次升级会轮换 session secret并注销现有浏览器会话；现有 Resend key 保留原值，只迁移 secret source
- Resend key 是本次升级中唯一明确保留的旧 key；曾写入普通用户目录的 primary、fallback 和所有 model
  option 上游 key 都必须创建新值，并在代理验收后撤销旧值
- 最终验收必须逐用户确认 `model.api_key` 精确等于 mapping 中的随机 proxy token，配置其它位置没有
  `api_key`，`.env` 没有 `OPENAI_API_KEY`；不能只看 cleanup dry-run 的布尔摘要
- 新版首次启动会清理归档时间超过 30 天的正文；生产升级必须先由受保护 cutover 生成并校验
  `archive.db` 一致性快照，且存在 `sensitive-state.complete`
- 生产 unit 固定使用 99999 天在线会话归档阈值和 30 天归档正文保留；cutover 会清除旧
  drop-in 中的历史覆盖值
- 一次性迁移、严格停服顺序、随机 proxy token 下发、usage/quota 迁移和普通用户权限复测见根目录
  `README.md` 的“升级已有部署”章节

### Gene Catalog 数据发布

- builder 的个人开发默认路径不是生产配置；生产构建必须显式传入全部来源文件、catalog version、prediction
  release 和新的 staging output
- 发布前必须关闭 builder connection，执行 SQLite `integrity_check`、schema/metadata 校验，并确认不存在
  `-wal`、`-shm` 或 `-journal` sidecar
- 每版数据库安装到 `/srv/gene_catalog/releases/<release-id>/gene_catalog.sqlite`，owner/mode 为
  `root:potato-interface 0640`，版本目录为 `root:potato-interface 0750`；通过原子替换 `current` symlink 发布，
  不得原地修改活动数据库
- Interface 必须可读该文件，普通 mapped Linux 用户必须不可读；发布后验收 `/genes`、
  `/api/v1/gene-catalog` 和 `/api/v1/genes/search`，完整构建、发布及回滚保留命令见根 README

### Genome Browser 特征索引与序列 API

- `interface.build_genome_feature_index --full` 从 `assemblies.json` 构建一个集中式 SQLite；
  `--sync --assembly CATEGORY/SAMPLE` 每次事务性替换一个 assembly，`--check` 用于发布前校验。
- 有来源明确、可溯源的代表转录本表时，可将表放在数据库根目录内，并在对应 assembly 中用相对路径
  `representativeMap` 声明；构建器会校验文件哈希。命令行的 `--representative-map` 仅用于一次性构建
  覆盖。不能把 GO/KEGG ID 归一化表当作代表转录本依据；没有可信映射或明确 GFF 选择标记时，按最长
  CDS、最长 exon、transcript ID 的确定性顺序选择。
- 构建器先写同目录临时数据库，并在 schema、行为版本、外键和 SQLite 检查通过后原子替换输出；已有输出的
  owner、group 和 mode 会被保留。生产发布也可先在 staging 路径构建，再用明确 owner/group/mode 安装。
- `GET /api/genome-browser/features/resolve` 按 assembly + gene/transcript ID 返回基因、代表转录本及有序 exon/CDS 坐标。
- 当前站点公网边缘只暴露原有 assembly 列表和 JBrowse 数据文件，不会转发新增语义 API；服务器内技能应使用
  Interface 内部 origin。若后续在边缘放行清晰 API 路径，外部调用方可直接使用同一后端契约。
- `phased_tetraploid/*` assembly 内跨 `seqid` 重复的原始 gene/transcript ID 会索引为 `raw_id@seqid`，原始 ID 保留为 alias；
  用未限定的原始 ID 查询时返回 `409` 及全部候选，调用方必须使用响应中的精确候选重试，不能任意选择或合并位点。
- `POST /api/genome-browser/sequences` 按 1-based inclusive 坐标批量提取正链或反向互补序列；每段及每个请求的
  总请求长度上限均为 1,000,000 bp，最多 256 段。只有显式 `clip=true` 时才裁剪越界区间。
- Genome Browser 数据下载路由只允许 manifest 明确列出的 JBrowse 文件；集中索引不会作为静态文件公开。

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

- 默认探针：`python3 interface/test_tui_bridge.py <mapping_username>`
- 自定义 prompt 必须通过 `--prompt-stdin` 或 caller-only 的 `--prompt-file` 提供；三个 bridge 诊断脚本都拒绝
  明文 `--prompt`，避免内容进入进程 argv
- `tui_bridge_trace.py` 默认只记录事件结构和长度；只有显式使用 `--include-content` 才会把完整 prompt、回复、
  stderr 和 RPC payload 写入 trace，且 `--log-file` 的直接父目录必须由调用者所有并为 `0700`
- 当前已确认：
  - bridge 能以目标 Linux 用户身份启动
  - `session.create` 能返回 live `tui_gateway` session id
  - `prompt.submit` 能进入 streaming 状态
  - 能收到 `message.delta`、`reasoning.delta`、`message.complete`

部署方式、systemd 启动、模型配置和根目录用户管理脚本的具体用法，请看仓库根目录 `README.md`。
