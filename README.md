# Potato Agent

Potato Agent 是 Hermes Agent 的多用户网页入口。当前部署模型面向共享服务器：网页服务不以
root 运行，每个网页账号绑定到一个独立 Linux 用户，每个 Linux 用户拥有自己的 Hermes
runtime、home、工作目录和 systemd service。

这份 README 是当前安全部署方式的唯一说明。不要把运行时数据库、用户映射文件或密钥放在
Git checkout 里。

## 架构

- 网页入口：`interface/` 中的 FastAPI 应用，前端路径是 `/lite`。
- interface 服务用户：`potato-interface`，非 root Linux 用户。
- 提权入口：`/usr/local/libexec/potato-agent-privileged-helper`，只通过 sudoers 放行固定命令。
- 用户运行时：每个网页用户对应一个 Linux 用户和一个 `hermes-<username>.service`。
- 代码目录：`/srv/potato_agent`。
- interface 状态目录：`/var/lib/potato-agent`。
- 空间转录组查看器数据目录：`/srv/spatial_data`，运行时默认读取
  `/srv/spatial_data/current`。
- WGCNA 共表达网络查看器入口：`/wgcna`；运行数据快照放在 `/srv/wgcna_data/current`，
  在线查询使用 PostgreSQL 数据库 `potato_wgcna`。
- Bulk RNA-Seq 表达查看器入口：`/bulk-rnaseq`；运行数据库放在
  `/srv/bulk_rnaseq/current/bulk_rnaseq.sqlite`，从公开整理结果目录构建只读 SQLite。
- Gene Catalog 入口：`/genes`；运行数据库放在
  `/srv/gene_catalog/current/gene_catalog.sqlite`，Interface 以 SQLite immutable read-only mode 查询。
- 新 Hermes 源码和构建入口：仓库内的 `hermes-lite/`。
- 当前 immutable 运行时：`/opt/potato-hermes-lite/current`；精确版本和 wheel hash 见下方
  “Hermes Lite 运行时”章节。
- `/opt/hermes-agent-src` 和 `/opt/hermes-agent-venv` 已不参与在线进程，仅在实际用户验收和观察期完成前
  保留作 legacy 回滚来源。
- interface Python 环境：`/opt/interface-env`。

`POTATO_AGENT_STATE_DIR` 只是在未设置专用变量时，为 mapping、Interface auth/archive DB、model proxy 配置和
usage DB 推导默认路径的前缀；它不会迁移 credentials、外部数据集、每用户 `HERMES_HOME`、源码或 release。
生产 unit 已显式固定各专用路径，通常不应设置这个总前缀。确需更换状态位置时，应分别设置
`POTATO_AGENT_MAPPING_PATH`、`INTERFACE_AUTH_DB`、`INTERFACE_ARCHIVE_DB`、
`POTATO_MODEL_PROXY_CONFIG_PATH` 和 `POTATO_MODEL_PROXY_USAGE_DB`，并同步审查 owner/mode、备份迁移及 systemd
sandbox 路径。

interface 进程不应该以 root 运行。需要 root 的动作由 privileged helper 完成，包括创建用户、
安装或启停每用户 Hermes service、读取每用户 Hermes session 数据库、按目标 Linux 用户权限
处理文件浏览和下载上传。

## Hermes Lite 运行时

[`hermes-lite/`](hermes-lite/) 是 Potato Hermes 唯一的新产品源码、构建输入和运行入口。完整的
[`hermes-agent/`](hermes-agent/) 以及 [`packaging/hermes/`](packaging/hermes/) 只保留为 upstream 审计和
回滚资料；新 release 不得从它们导入模块、editable install 或生成 wheel。Interface 只启动 Lite wheel 中的
`python -m tui_gateway.entry`；每用户 unit 使用的 `hermes gateway run --replace` 只是 systemd 前台兼容
guard，不会启动上游消息平台 gateway。

Lite 源码中的主要边界如下：

| 路径 | 职责 |
| --- | --- |
| `potato_hermes_lite/` | 最小 CLI、运行时 guard、附件和 skills 边界 |
| `agent/`、`tools/`、`providers/` | 从 Hermes 保留的核心 agent loop、允许的工具和 provider 基础设施 |
| `tui_gateway/` | Potato Web 使用的 stdio RPC gateway |
| `runtime-profile.yaml` | provider、API mode、工具、plugin、MCP 和自动安装的 fail-closed 策略 |
| `manifests/` | 精确 source/wheel inventory、依赖、入口、浏览器资产、工具和 forbidden path 合约 |
| `scripts/` | 隔离验证、可复现构建、inactive 安装、状态指纹和生产切换 |
| `tests*` | Lite 单测、打包边界测试和真实 stdio gateway mock E2E |

保留的产品能力：

- Potato Web 的 create/resume/prompt/interrupt/approval/command RPC 和 message/tool/error 流式事件；
- `custom` model provider，以及 `codex_responses`、`chat_completions` 两种 API mode；
- terminal/process、文件、现有模型视觉链路、本地 browser、skills、代码执行、todo、内置 memory、
  session search 和 delegation；
- 现有图片附件、approval 和 interrupt 契约。Lite 不再额外增加原生图片协议，也不增加
  `clarify`、`sudo`、`secret` 网页交互。

物理删除或由 profile 禁用的能力：

- 完整 Hermes CLI/TUI、dashboard、ACP、cron、kanban、MoA、MCP 和消息平台 gateway；
- web/search、外部 provider adapter、媒体生成、voice、computer use 和运行时自动依赖安装；
- user/project/entry-point plugin，以及 Lite 不支持的 Codex App Server 运行面。

profile 最多允许 27 个模型工具。27 是逻辑上限，不是每次请求的固定数量：availability check 可以隐藏
当前不可用的 `vision_analyze`、`browser_cdp`、`browser_dialog` 等工具，但请求不能增加清单外工具。在线
runtime 在 `HERMES_RUNTIME_PROFILE_PATH` 缺失或 provider/API mode 不在 allowlist 时 fail closed，不回退到
完整 Hermes。source/wheel inventory 漂移和 forbidden path/import 则由 verifier/build 门禁阻止候选 release
生成；它们不是在线进程的持续完整性监控。

用户数据不属于任何源码或 release。`HERMES_HOME`、用户工作目录、mapping、Interface 数据库和托管 skills
始终保留在外部路径；构建和切换 release 不得复制、清空或重建这些数据。

当前生产状态：

```text
release:      /opt/potato-hermes-lite/releases/20260729T070500Z-0.19.0-potato.lite.3-f2336202
current:      /opt/potato-hermes-lite/current
version:      0.19.0+potato.lite.3
wheel SHA256: f23362028b3f1ab3f85d929e83eb4e88d3b6b18495d2f86a3508476bfe0777b9
```

本次切换前基线是
`20260728T141258Z-0.19.0-potato.lite.2-a96c1091`；该 immutable release 在新版本验收和观察期结束前保留作
回滚目标。当前 mapping 中的全部用户 unit 和 Interface gateway Python 均使用 Lite；用户数量必须从受保护的
mapping 动态读取。构建、切换、验收和回滚流程见
本文第 6.3 至 6.7 小节及 [`hermes-lite/README.md`](hermes-lite/README.md)。

`0.19.0+potato.lite.2` 修复辅助请求参数构造器对已裁剪 `agent.anthropic_adapter` 的无条件导入；
`vision_analyze` 和 `browser_vision` 在允许的 `custom` Codex/OpenAI-wire 路径上不再于 API 调用前报模块缺失。

`0.19.0+potato.lite.3` 已于 2026-07-29 完成安全切换，包含私有 runtime 临时目录、命令正文移出 argv、
`processes.json` 私有化、模型代理凭据隔离和 Interface credential 边界修复。新部署仍必须完成本文列出的
systemd credential、专用模型代理账号、固定 privileged helper、宿主 `hidepid=2` 和 6.7 小节验收；这些
宿主设置不会仅靠同步源码自动生效。

### 0.19.0 Potato Lite 依赖更新

`0.19.0+potato.lite.3` 不使用安装时动态解析的依赖集合。Lite 与 Interface 均以 CPython 3.12、Linux x86_64
全量 hash lock 安装，wheelhouse 文件名、大小和 SHA256 另由 manifest 绑定：

| 边界 | 固定版本或范围 | 说明 |
| --- | --- | --- |
| Lite | `PyJWT[crypto]==2.13.0` | JWT crypto extra 固定到已审计版本 |
| Lite | `urllib3>=2.7,<3` | 阻止回退到旧主版本并限制未来破坏性升级 |
| Lite | `cryptography==48.0.1` | 替代已被 OSV 标记 HIGH 的 `46.0.7` |
| Lite | `certifi==2026.5.20` | 固定 TLS 根证书集合 |
| Lite | `Pillow==12.3.0` | 修复 `12.2.0` 的多项 HIGH 图像解析公告 |
| Interface | `fastapi==0.136.1` | 固定 ASGI 框架版本 |
| Interface | `starlette==1.3.1` | 固定请求/WebSocket 基础实现版本 |

Lite 完整闭包见
[`hermes-lite/manifests/requirements-py312-linux-x86_64.lock`](hermes-lite/manifests/requirements-py312-linux-x86_64.lock)，
Interface 完整闭包见
[`interface/requirements-py312-linux-x86_64.lock`](interface/requirements-py312-linux-x86_64.lock)。安装必须同时使用
`--require-hashes --no-index` 和对应 wheelhouse。Lite 项目 wheel 单独使用 `--no-deps` 安装，Lite installer
还会校验 distribution/file fingerprint；Interface 则在安装前核对 lock hash 和 wheelhouse 精确 inventory，
安装后执行 `pip check`。

## 安全边界

目标安全部署要求以下边界；本次仓库改动不会自动改变现网，必须完成本文安全切换步骤后逐项验收：

1. `/srv/potato_agent` 只允许 `root` 和 `potato-interface` 组读取，普通 Hermes 用户不能读源码。
2. `/var/lib/potato-agent/data` 由 `potato-interface` 独占，普通 Hermes 用户不能读
   interface 用户数据库和归档数据库。
3. `/srv/spatial_data` 由 `root:potato-interface` 只读维护，空间转录组页面可公开访问，但底层
   SQLite 和轮廓数据不暴露给普通 Linux 用户直接读取。
4. `/srv/wgcna_data` 由 `root:potato-interface` 只读维护，WGCNA 页面可公开访问，但底层导出
   TSV 和 PostgreSQL 写入权限不开放给普通 Linux 用户。
5. `/srv/bulk_rnaseq` 由 `root:potato-interface` 只读维护，Bulk RNA-Seq 页面可公开访问，但底层
   SQLite 不暴露给普通 Linux 用户直接读取。
6. `/srv/gene_catalog` 由 `root:potato-interface` 只读维护，Gene Catalog 页面可公开访问，但底层
   SQLite 不暴露给普通 Linux 用户直接读取，也不允许 Interface 原地修改。
7. 每用户 Hermes service 以各自 Linux 用户运行，并在 systemd unit 中隐藏
   `/srv/potato_agent`、`/var/lib/potato-agent`、`/etc/potato-agent` 和
   `/opt/interface-env`。
8. Interface session secret 和 Resend key 只通过 systemd credential 提供，不写入 unit
   `Environment=`、源码或进程 argv。
9. 本地模型代理以独立 `potato-model-proxy` 身份运行；真实上游 key 文件为
   `root:potato-model-proxy 0640`，`potato-interface` 和普通 Hermes 用户都不能读取。
10. Interface 默认只监听 `127.0.0.1:3000`，公网入口由 HTTPS 反向代理提供，Cookie 使用 `Secure`、
   `HttpOnly` 和 `SameSite=Lax`。只有经 owner 明确批准的无域名隔离测试环境，才可使用本文记录的显式
   HTTP profile；该例外仍保留 `HttpOnly` 和 `SameSite=Lax`，但网络传输不再加密。
11. 宿主 `/proc` 使用 `hidepid=2`，普通登录用户看不到其它 UID 的进程目录、argv 或 environ；即使每用户 unit
    另设 `ProtectProc=invisible`/`ProcSubset=pid`，也只是纵深防御，不能替代宿主级挂载选项。
12. Daily Updates 由 `potato-daily-updates` oneshot 独占写入 `/srv/daily_updates`；Interface 仅通过专用
   数据组读取，模型调用使用独立 systemd credential，不借用普通 Web 用户 token。完整部署、历史三库迁移、
   验收和回滚步骤见 [`interface/DAILY_UPDATES.md`](interface/DAILY_UPDATES.md)。

推荐权限：

```bash
chown -R root:potato-interface /srv/potato_agent
chmod 0750 /srv/potato_agent

chown root:potato-interface /var/lib/potato-agent
chown root:potato-interface /var/lib/potato-agent/config
chown potato-interface:potato-interface /var/lib/potato-agent/data
chmod 0750 /var/lib/potato-agent /var/lib/potato-agent/config
chmod 0700 /var/lib/potato-agent/data

chown root:potato-interface /var/lib/potato-agent/config/users_mapping.yaml
chmod 0640 /var/lib/potato-agent/config/users_mapping.yaml
chown potato-interface:potato-interface /var/lib/potato-agent/data/*.db 2>/dev/null || true
chmod 0600 /var/lib/potato-agent/data/*.db 2>/dev/null || true

chown root:potato-model-proxy /var/lib/potato-agent/config/model_proxy.yaml
chmod 0640 /var/lib/potato-agent/config/model_proxy.yaml
chown potato-model-proxy:potato-model-proxy /var/lib/potato-agent/model-proxy
chmod 0700 /var/lib/potato-agent/model-proxy
chown potato-model-proxy:potato-model-proxy /var/lib/potato-agent/model-proxy/*.db 2>/dev/null || true
chmod 0600 /var/lib/potato-agent/model-proxy/*.db 2>/dev/null || true

chown root:root /etc/potato-agent/credentials
chmod 0700 /etc/potato-agent/credentials
chown root:root /etc/potato-agent/credentials/interface-session-secret \
  /etc/potato-agent/credentials/resend-api-key
chmod 0600 /etc/potato-agent/credentials/interface-session-secret \
  /etc/potato-agent/credentials/resend-api-key

chown -R root:potato-interface /srv/spatial_data 2>/dev/null || true
find /srv/spatial_data -type d -exec chmod 0750 {} + 2>/dev/null || true
find /srv/spatial_data -type f -exec chmod 0640 {} + 2>/dev/null || true

chown -R root:potato-interface /srv/wgcna_data 2>/dev/null || true
find /srv/wgcna_data -type d -exec chmod 0750 {} + 2>/dev/null || true
find /srv/wgcna_data -type f -exec chmod 0640 {} + 2>/dev/null || true

chown -R root:potato-interface /srv/bulk_rnaseq 2>/dev/null || true
find /srv/bulk_rnaseq -type d -exec chmod 0750 {} + 2>/dev/null || true
find /srv/bulk_rnaseq -type f -exec chmod 0640 {} + 2>/dev/null || true

chown -R root:potato-interface /srv/gene_catalog 2>/dev/null || true
find /srv/gene_catalog -type d -exec chmod 0750 {} + 2>/dev/null || true
find /srv/gene_catalog -type f -exec chmod 0640 {} + 2>/dev/null || true
```

共享服务器或公网部署如果需要在 Files 面板访问共享数据，应使用
`INTERFACE_FILE_BROWSER_MODE=home_and_public_data`；不需要共享数据时使用 `home_only`。只有在可信
内网机器上，并且你确实希望用户可以浏览 Linux 账号本身有权限读取的任意目录时，才使用
`user_readable`。

### Files 预览、下载和上传 worker

文件预览、下载和上传必须同时满足源码隔离与目标用户权限检查。privileged helper 以 root 身份校验映射、
浏览器根目录和敏感路径，并在降权前读取可信的 `interface/file_stream_worker.py` 或
`interface/file_upload_worker.py` 源码；预览和下载还会生成完整的只读路径策略。随后通过
`runuser -u <linux_user> -- python -I -c ...` 执行内联 worker。文件内容仍由目标 Linux 用户身份打开或
写入，worker 不需要、也不允许在降权后重新读取 `/srv/potato_agent`。

部署时必须保持以下约束：

- `/srv/potato_agent` 继续使用 `root:potato-interface`、`0750`，不要为了修复预览而改成 `0751`/`0755`，
  也不要把普通 Hermes 用户加入 `potato-interface` 组；
- `interface/file_stream_worker.py`、`interface/file_upload_worker.py` 和
  `interface/file_browser_policy.py` 必须作为同一次代码发布同步，文件由 root 拥有且不能由
  `potato-interface` 写入；
- 代码更新后必须重启 `potato-interface.service`。生产升级应使用后文的
  `cutover_lite_production.sh`，由脚本在同步 Interface 代码前停止服务，避免新旧策略代码混用；
- 不需要在 `/usr/local/libexec` 额外安装 worker，也不需要为目标用户添加任何 `/srv` ACL。

发布前至少运行对应权限回归：

```bash
python3 -m pytest -c /dev/null \
  interface/test_security_boundaries.py \
  interface/test_privileged_helper.py \
  interface/test_file_preview.py \
  interface/test_file_browser_policy.py

test "$(stat -c '%a %U %G' /srv/potato_agent)" = "750 root potato-interface"
sudo -u potato-interface test -r /srv/potato_agent/interface/file_stream_worker.py
```

## 前置条件

以下命令默认以 root 执行，目标机器需要 x86_64 Linux 和 systemd。Lite 与 Interface 的构建、wheelhouse
校验及生产 venv 统一要求 CPython 3.12；其它 Python 实现、Python minor 或 CPU 架构不能复用这里的 lock 和
wheelhouse manifest。

需要安装：

- CPython 3.12 和对应的 `venv`
- `git`
- `curl`
- `rsync`
- `sudo`
- `systemctl`
- `micromamba`，用于生信技能共享环境和按用户隔离安装工具环境
- `PostgreSQL` server/client，用于 WGCNA 共表达网络查看器
- 系统默认 Python 可直接导入 GO/KEGG 分析依赖
- Hermes Python 依赖需要的编译工具

Debian 或 Ubuntu 可先安装基础包：

```bash
apt-get update
apt-get install -y python3.12 python3.12-venv python3-pip git curl rsync sudo build-essential postgresql postgresql-client
```

`micromamba` 推荐系统级安装到 `/opt/micromamba/bin/micromamba`，但环境根目录使用每个
Linux 用户自己的 `$HOME/.micromamba`。这样网页用户运行 Hermes skill 时，可以在自己的 home
下创建和维护隔离环境，不需要写入 `/opt` 或项目目录。

已部署服务器上的约定配置是：

```text
binary: /opt/micromamba/bin/micromamba
profile: /etc/profile.d/micromamba.sh
MAMBA_ROOT_PREFIX: $HOME/.micromamba
```

## 全新部署

下面假设你已经把仓库 clone 或复制到一个临时工作目录。

### 1. 同步代码到 `/srv/potato_agent`

本步骤只用于空主机初装。已有生产环境不要直接对 `/srv/potato_agent` 运行这条 `rsync --delete`；应使用后文
的 inactive release + `cutover_lite_production.sh` 流程，让脚本先备份代码、记录服务状态并比较用户状态指纹。

```bash
mkdir -p /srv/potato_agent
rsync -a --delete \
  --exclude '.git/' \
  --exclude '.codex-tmp/' \
  --exclude '.deploy-backups/' \
  --exclude '.mypy_cache/' \
  --exclude '.pytest_cache/' \
  --exclude '.ruff_cache/' \
  --exclude '.tox/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '*.pyo' \
  --exclude '*.egg-info/' \
  --exclude 'build/' \
  --exclude 'dist/' \
  --exclude 'htmlcov/' \
  --exclude 'node_modules/' \
  --exclude '.coverage' \
  --exclude '.DS_Store' \
  --exclude '*.bak' \
  --exclude '*.bak-*' \
  --exclude '*.orig' \
  --exclude '*~' \
  --exclude '*.db' \
  --exclude '*.db-wal' \
  --exclude '*.db-shm' \
  --exclude '*.db-journal' \
  --exclude '*.sqlite' \
  --exclude '*.sqlite-wal' \
  --exclude '*.sqlite-shm' \
  --exclude '*.sqlite-journal' \
  --exclude '*.sqlite3' \
  --exclude '*.sqlite3-wal' \
  --exclude '*.sqlite3-shm' \
  --exclude '*.sqlite3-journal' \
  --exclude '.env' \
  --exclude 'hermes-agent/' \
  --exclude 'packaging/hermes/' \
  --exclude 'hermes-lite/build/' \
  --exclude 'interface/data/' \
  --exclude 'users_mapping.yaml' \
  --exclude 'model_proxy.yaml' \
  ./ /srv/potato_agent/
cd /srv/potato_agent
```

同步结果不得包含 legacy Hermes 或运行时状态：

```bash
test ! -d /srv/potato_agent/hermes-agent
test ! -e /srv/potato_agent/users_mapping.yaml
test ! -e /srv/potato_agent/model_proxy.yaml
find /srv/potato_agent -type f \
  \( -name '*.db' -o -name '*.db-wal' -o -name '*.db-shm' -o -name '*.db-journal' -o \
     -name '*.sqlite' -o -name '*.sqlite-wal' -o -name '*.sqlite-shm' -o -name '*.sqlite-journal' -o \
     -name '*.sqlite3' -o -name '*.sqlite3-wal' -o -name '*.sqlite3-shm' -o -name '*.sqlite3-journal' -o \
     -name '.env' \
     -o -name 'users_mapping.yaml' -o -name 'model_proxy.yaml' \
     -o -name '*.pyc' -o -name '*.pyo' \) -print
```

最后一条命令应无输出。mapping、Interface 数据库和所有用户 `HERMES_HOME` 必须在 `/var/lib` 或用户 home
等外部状态路径中，不能通过源码同步创建。

同步后确认 Lite 源码自带的旧 `plan` 技能已被删除，避免覆盖托管的 `plan-mode` 技能：

```bash
test ! -e /srv/potato_agent/hermes-lite/skills/software-development/plan/SKILL.md
```

### 2. 创建服务用户和状态目录

```bash
useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin potato-interface 2>/dev/null || true
groupadd --system potato-model-proxy 2>/dev/null || true
useradd --system --gid potato-model-proxy --groups potato-interface \
  --home-dir /nonexistent --shell /usr/sbin/nologin \
  potato-model-proxy 2>/dev/null || true
usermod -a -G potato-interface potato-model-proxy

mkdir -p /var/lib/potato-agent/config /var/lib/potato-agent/data
install -d -o potato-model-proxy -g potato-model-proxy -m 0700 \
  /var/lib/potato-agent/model-proxy
if [ ! -s /var/lib/potato-agent/config/users_mapping.yaml ]; then
  printf 'users: []\n' >/var/lib/potato-agent/config/users_mapping.yaml
fi

chown -R root:potato-interface /srv/potato_agent
chmod 0750 /srv/potato_agent

chown root:potato-interface /var/lib/potato-agent /var/lib/potato-agent/config
chown potato-interface:potato-interface /var/lib/potato-agent/data
chmod 0750 /var/lib/potato-agent /var/lib/potato-agent/config
chmod 0700 /var/lib/potato-agent/data

chown root:potato-interface /var/lib/potato-agent/config/users_mapping.yaml
chmod 0640 /var/lib/potato-agent/config/users_mapping.yaml
```

`potato-model-proxy` 加入 `potato-interface` 组仅用于只读穿越 `/srv/potato_agent` 和读取
`users_mapping.yaml`；不要反向把 `potato-interface` 加入 `potato-model-proxy` 组。代理专用目录只存
usage/quota 数据，不存聊天正文，也不能与 `/var/lib/potato-agent/data` 共用权限。

### 3. 安装 micromamba

如果机器上还没有 `/opt/micromamba/bin/micromamba`，按下面方式安装。该安装只放置
micromamba 二进制和系统 profile 配置；实际 conda-style 环境默认创建到各 Linux 用户自己的
`$HOME/.micromamba`。

```bash
mkdir -p /opt/micromamba/bin
curl -L https://micro.mamba.pm/api/micromamba/linux-64/latest \
  | tar -xvj -C /opt/micromamba/bin --strip-components=1 bin/micromamba

chown -R root:root /opt/micromamba
chmod 0755 /opt/micromamba /opt/micromamba/bin
chmod 0755 /opt/micromamba/bin/micromamba

cat >/etc/profile.d/micromamba.sh <<'EOF'
# micromamba setup - system-wide
export PATH="/opt/micromamba/bin:$PATH"
export MAMBA_ROOT_PREFIX="$HOME/.micromamba"
EOF

chown root:root /etc/profile.d/micromamba.sh
chmod 0644 /etc/profile.d/micromamba.sh
```

验证：

```bash
/opt/micromamba/bin/micromamba --version
su -s /bin/bash -c 'source /etc/profile.d/micromamba.sh && command -v micromamba && micromamba info | sed -n "1,30p"' potato-interface
```

部署脚本、skill 或 Slurm 作业里不要假设登录 shell 一定已加载 profile。需要可靠调用时，直接使用
`/opt/micromamba/bin/micromamba`，或者先执行：

```bash
source /etc/profile.d/micromamba.sh
```

### 4. 安装系统 Python GO/KEGG 分析依赖

GO 富集和 KEGG 分析脚本会从用户 shell、Slurm 作业或 skill 脚本里直接调用系统默认
`python3`。新部署不能只把这些包安装到 `/opt/interface-env` 或 Hermes runtime venv，必须保证
普通 Linux 用户运行 `/usr/bin/python3` 时可以直接 `import`。

在 Ubuntu 24.04 上，下面的 pip 安装会写入系统 Python 可见的
`/usr/local/lib/python3.12/dist-packages`。该路径默认在 `/usr/bin/python3` 的 `sys.path` 中，
所有普通用户都能读取：

```bash
python3 -m pip install --only-binary=:all: --break-system-packages --root-user-action=ignore \
  numpy==1.26.4 \
  pandas==2.2.3 \
  matplotlib==3.10.9 \
  scipy==1.17.1 \
  statsmodels==0.14.6 \
  goatools==1.6.5
```

版本固定如下，保证 GO/KEGG 分析结果环境可复现：

- `numpy==1.26.4`
- `pandas==2.2.3`
- `matplotlib==3.10.9`
- `scipy==1.17.1`
- `statsmodels==0.14.6`
- `goatools==1.6.5`

验证默认 Python 和普通用户都能导入这些包：

```bash
python3 -c "import numpy, pandas, matplotlib, scipy, statsmodels, goatools; print('system python GO/KEGG deps ok')"
sudo -u potato-interface /usr/bin/python3 -c "import numpy, pandas, matplotlib, scipy, statsmodels, goatools; print('shared users can import GO/KEGG deps')"
```

如果分析脚本在没有可写 home 的 service 用户下使用 Matplotlib，需要给该进程设置可写的
`MPLCONFIGDIR`。普通 Hermes Linux 用户有自己的 home 目录，通常不需要额外设置。

### 5. 安装 sgRNA Design 共享依赖

`skills/potato-knowledge-bioinformatics/sgrna-design` 默认调用系统 PATH 中的
`crispor`、`crispor-add-genome`、`flashfry` 和 `samtools`。这些工具必须作为共享依赖安装到
`/opt` 和 `/usr/local/bin`，不要让每个 Hermes Linux 用户在自己的 home 下重复安装。

约定路径：

```text
CRISPOR source: /opt/crispr_design/crisporWebsite
CRISPOR env:    /opt/crispor_py39
FlashFry jar:   /opt/crispr_design/flashfry/FlashFry.jar
tool env:       /opt/crispr_tools
global PATH:    /usr/local/bin
```

安装 CRISPOR 源码和 Python 3.9 环境。这里固定 `scikit-learn==1.0.2`，用于兼容 CRISPOR
自带的 Azimuth/Doench 模型 pickle；`rs3` 用于 Rule Set 3 评分：

```bash
mkdir -p /opt/crispr_design
if [ ! -d /opt/crispr_design/crisporWebsite ]; then
  git clone https://github.com/maximilianh/crisporWebsite.git /opt/crispr_design/crisporWebsite
fi
mkdir -p /opt/crispr_design/crisporWebsite/genomes

/opt/micromamba/bin/micromamba create -y -p /opt/crispor_py39 \
  -c conda-forge -c bioconda \
  python=3.9 \
  bwa=0.7.19 \
  biopython=1.85 \
  numpy=1.26.4 \
  scipy=1.13.1 \
  pandas=2.3.1 \
  matplotlib=3.9.4 \
  scikit-learn=1.0.2 \
  rs3=0.0.18 \
  pytabix=0.1 \
  twobitreader=3.1.7 \
  lmdbm=0.0.6 \
  xlwt=1.3.0
```

安装 FlashFry、Java 和 `samtools`。FlashFry 官方 quickstart 使用
`FlashFry-assembly-1.15.jar`：

```bash
mkdir -p /opt/crispr_design/flashfry
curl -L \
  -o /opt/crispr_design/flashfry/FlashFry-assembly-1.15.jar \
  https://github.com/mckennalab/FlashFry/releases/download/1.15/FlashFry-assembly-1.15.jar
ln -sf FlashFry-assembly-1.15.jar /opt/crispr_design/flashfry/FlashFry.jar

/opt/micromamba/bin/micromamba create -y -p /opt/crispr_tools \
  -c conda-forge -c bioconda \
  openjdk=11 \
  samtools=1.23.1 \
  htslib=1.23.1
```

创建全局 wrapper。`crispor` wrapper 使用每用户独立的 Matplotlib cache，避免多个普通用户共享
`/tmp/matplotlib-cache` 造成权限冲突：

```bash
cat >/usr/local/bin/crispor <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

ENV=/opt/crispor_py39
CRISPOR_DIR=/opt/crispr_design/crisporWebsite

if [[ -z "${MPLCONFIGDIR:-}" ]]; then
  export MPLCONFIGDIR="/tmp/matplotlib-cache-${UID}"
  mkdir -p "$MPLCONFIGDIR"
  chmod 700 "$MPLCONFIGDIR" 2>/dev/null || true
fi

exec /opt/micromamba/bin/micromamba run -p "$ENV" python "$CRISPOR_DIR/crispor.py" "$@"
EOF

cat >/usr/local/bin/crispor-add-genome <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

ENV=/opt/crispor_py39
CRISPOR_DIR=/opt/crispr_design/crisporWebsite

exec /opt/micromamba/bin/micromamba run -p "$ENV" python "$CRISPOR_DIR/tools/crisporAddGenome" --baseDir "$CRISPOR_DIR/genomes" "$@"
EOF

cat >/usr/local/bin/flashfry <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

ENV=/opt/crispr_tools
JAR=/opt/crispr_design/flashfry/FlashFry.jar

exec /opt/micromamba/bin/micromamba run -p "$ENV" java -jar "$JAR" "$@"
EOF

ln -sf /opt/crispr_tools/bin/samtools /usr/local/bin/samtools

chown root:root /usr/local/bin/crispor /usr/local/bin/crispor-add-genome /usr/local/bin/flashfry
chown -h root:root /usr/local/bin/samtools
chmod 0755 /usr/local/bin/crispor /usr/local/bin/crispor-add-genome /usr/local/bin/flashfry
chmod -R a+rX /opt/crispr_design /opt/crispor_py39 /opt/crispr_tools
```

验证共享安装在干净 PATH 下可用：

```bash
env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  bash -lc 'command -v crispor crispor-add-genome flashfry samtools'

env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  crispor --help >/dev/null
env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  flashfry >/dev/null
env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  samtools --version | sed -n '1p'

/opt/crispor_py39/bin/python -c "import Bio, lmdbm, matplotlib, numpy, pandas, rs3, scipy, sklearn, tabix, twobitreader, xlwt; print('CRISPOR Python deps ok')"
sudo -u potato-interface env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  bash -lc 'crispor --help >/dev/null && flashfry >/dev/null && samtools --version >/dev/null'
```

不要在共享部署时给普通 Hermes 用户开放 `/opt/crispr_design/crisporWebsite/genomes` 写权限。用户需要
自定义基因组时，在自己的 home 或任务目录中建 index，并在运行时显式传同一个目录：

```bash
mkdir -p "$HOME/crispor_genomes"
crispor-add-genome --baseDir "$HOME/crispor_genomes" fasta genome.fa \
  --desc "genomeId|Scientific name|Common name|Version" \
  --gff annotation.gff3
crispor --genomeDir "$HOME/crispor_genomes" genomeId targets.fa guides.tsv -o offs.tsv -p NGG --mm 4
```

### 6. 构建和安装 Hermes Lite 运行时

#### 6.1 准备独立构建环境

Lite 生产 venv 故意不安装 `setuptools`、`packaging`、pytest 等构建依赖，不能用
`/opt/potato-hermes-lite/current/venv` 或任一 immutable release venv 构建下一版。legacy Hermes venv 也不再是
支持的 Lite 构建输入；所有机器都使用独立 build venv：

```bash
python3.12 -m venv /opt/potato-hermes-lite-build-env
BUILD_PYTHON=/opt/potato-hermes-lite-build-env/bin/python3

"$BUILD_PYTHON" -c \
  'import platform,sys; assert sys.implementation.name == "cpython"; assert sys.version_info[:2] == (3, 12); assert sys.platform == "linux"; assert platform.machine() == "x86_64"'

"$BUILD_PYTHON" -m pip install --upgrade pip
mapfile -t lite_requirements < <(
  "$BUILD_PYTHON" -c \
    'import json; print(*json.load(open("hermes-lite/manifests/direct-dependencies.json"))["requirements"], sep="\n")'
)
"$BUILD_PYTHON" -m pip install \
  setuptools==82.0.1 wheel packaging pytest \
  "${lite_requirements[@]}"
```

`hermes-lite/manifests/source-inventory.json` 和 `wheel-inventory.json` 是经过审查的精确 hash、size、mode
allowlist。普通构建只能验证它们；不能为了消除 verifier 报错就运行 `--write-source-inventory` 或
`--write-wheel-inventory`。真正修改 Lite 产品源码时，应先提升 Lite 版本、审查源码差异，再按维护者流程更新
两份 inventory 并检查重复构建的 wheel SHA。

#### 6.2 准备 clean browser assets

可部署 release 必须给 builder 传 `--browser-assets`。资产根是一个真实目录，布局至少包含：

```text
browser/bin/agent-browser
browser/chrome/chrome-linux64/chrome
browser/chrome/chrome-linux64/chrome_sandbox
```

版本、下载地址、archive size 和 SHA256 以
[`hermes-lite/manifests/browser-assets.json`](hermes-lite/manifests/browser-assets.json) 为准。Chrome archive
必须在解压前核对 size 和 SHA；`agent-browser` 必须来自可信发布资产。builder 会再次检查 agent-browser hash
以及两个可执行文件的 `--version`。

资产树只能包含上游的 `chrome_sandbox`，不能预先包含 `chrome-sandbox`。installer 会在 immutable release
中创建后者的 root-owned hardlink 并设置 `04755`；这是明确的 SUID 信任边界。不要直接把已安装的
`/opt/potato-hermes-lite/current` 传给 builder：它既是 symlink，内容中也已经有 `chrome-sandbox`。
升级现有 Lite 时，可以先从当前 release 复制一份 clean tree：

```bash
BROWSER_ASSETS=/var/tmp/potato-hermes-lite-browser-assets
test ! -e "$BROWSER_ASSETS"
test ! -L "$BROWSER_ASSETS"
install -d -o root -g root -m 0755 "$BROWSER_ASSETS/browser"
rsync -a --exclude 'chrome-sandbox' \
  "$(readlink -f /opt/potato-hermes-lite/current)/browser/" \
  "$BROWSER_ASSETS/browser/"
test -x "$BROWSER_ASSETS/browser/bin/agent-browser"
test -x "$BROWSER_ASSETS/browser/chrome/chrome-linux64/chrome"
test -f "$BROWSER_ASSETS/browser/chrome/chrome-linux64/chrome_sandbox"
test ! -e "$BROWSER_ASSETS/browser/chrome/chrome-linux64/chrome-sandbox"
```

#### 6.3 测试、隔离验证和重复构建

Lite 单测与 packaging 测试必须联合收集，避免 CI 与本地验证边界不一致：

```bash
PYTHONDONTWRITEBYTECODE=1 "$BUILD_PYTHON" -B -m pytest \
  -q -p no:cacheprovider -c /dev/null \
  hermes-lite/tests hermes-lite/tests_packaging

PYTHONDONTWRITEBYTECODE=1 "$BUILD_PYTHON" -B -m pytest \
  -q -p no:cacheprovider -c /dev/null \
  hermes-lite/tests_e2e/test_mock_provider_e2e.py

PYTHONDONTWRITEBYTECODE=1 "$BUILD_PYTHON" -B \
  hermes-lite/scripts/verify_lite.py --python "$BUILD_PYTHON"
```

verifier 使用 `python -S -B -P`，只注入 Lite tree 和明确的 dependency site-packages，并检查固定依赖、
console entrypoint、27 工具上限、forbidden paths/imports 和关键模块 origin。正式发布建议从相同输入独立构建
两次并比较 wheel；manifest 的创建时间可以不同，wheel 内容必须一致：

```bash
BUILD_ROOT=/var/tmp/potato-hermes-lite-build-$(date -u +%Y%m%dT%H%M%SZ)
RELEASE_A=$BUILD_ROOT/release-a
RELEASE_B=$BUILD_ROOT/release-b
test ! -e "$BUILD_ROOT"
test ! -L "$BUILD_ROOT"
install -d -o root -g root -m 0700 "$BUILD_ROOT"

"$BUILD_PYTHON" -B hermes-lite/scripts/build_lite_release.py \
  --dry-run \
  --python "$BUILD_PYTHON" \
  --browser-assets "$BROWSER_ASSETS"

"$BUILD_PYTHON" -B hermes-lite/scripts/build_lite_release.py \
  --python "$BUILD_PYTHON" \
  --browser-assets "$BROWSER_ASSETS" \
  --output "$RELEASE_A"

"$BUILD_PYTHON" -B hermes-lite/scripts/build_lite_release.py \
  --python "$BUILD_PYTHON" \
  --browser-assets "$BROWSER_ASSETS" \
  --output "$RELEASE_B"

sha256sum "$RELEASE_A"/wheel/*.whl "$RELEASE_B"/wheel/*.whl
cmp "$RELEASE_A"/wheel/*.whl "$RELEASE_B"/wheel/*.whl
```

`--output` 和可选的 `--work-dir` 必须位于 `/opt`、`/srv` 和源码树之外，且目标不能预先存在。build 只生成
候选 release，不创建生产 venv、不切换 symlink、不重启服务，也不读取或写入用户状态。

#### 6.4 准备离线 wheelhouse 并安装 inactive release

installer 全程使用 `--no-index`。候选 release 已复制经过审查的 CPython 3.12/Linux x86_64 hash lock 和
wheelhouse manifest；必须按该 lock 下载全部直接及传递依赖，不能从项目 wheel 重新解析一套依赖：

```bash
WHEELHOUSE=$BUILD_ROOT/wheelhouse
LITE_LOCK=$RELEASE_A/config/manifests/requirements-py312-linux-x86_64.lock
LITE_WHEELHOUSE_MANIFEST=$RELEASE_A/config/manifests/wheelhouse-py312-linux-x86_64.json
install -d -o root -g root -m 0755 "$WHEELHOUSE"
"$BUILD_PYTHON" -m pip download \
  --require-hashes --no-deps --only-binary=:all: \
  --dest "$WHEELHOUSE" -r "$LITE_LOCK"
find "$WHEELHOUSE" -maxdepth 1 -type f ! -name '*.whl' -print
```

最后一条命令应无输出。不要向 wheelhouse 添加 Lite 项目 wheel：installer 会从候选 release 的 `wheel/`
单独以 `--no-deps` 安装它。`install_lite_release.sh` 会在创建 venv 前核对 manifest 的 target 和 lock SHA256，
并要求 wheelhouse 的文件名集合、文件大小和 SHA256 与 `$LITE_WHEELHOUSE_MANIFEST` 完全一致；任何缺失、额外或
重新打包的 wheel 都会失败。安装后还会归档 `config/installed-distributions.json`。

release、wheelhouse 和后文的 code staging 在交给 root 脚本前必须归 root 所有且不可由普通用户写入：

```bash
chown -R root:root "$BUILD_ROOT" "$BROWSER_ASSETS"
chmod -R go-w "$BUILD_ROOT" "$BROWSER_ASSETS"
install -o root -g root -m 0755 \
  hermes-lite/scripts/install_lite_release.sh \
  "$BUILD_ROOT/install_lite_release.sh"
```

从构建 manifest 生成从未使用过的 release ID；时间戳避免同一版本的失败安装复用旧目录，version 中的 `+`
需要替换为 release ID 允许的 `-`：

```bash
RELEASE_VERSION=$(
  "$BUILD_PYTHON" -c \
    'import json,sys; print(json.load(open(sys.argv[1]))["project"]["version"])' \
    "$RELEASE_A/manifest.json"
)
WHEEL_SHA=$(
  "$BUILD_PYTHON" -c \
    'import json,sys; print(json.load(open(sys.argv[1]))["wheel"]["sha256"])' \
    "$RELEASE_A/manifest.json"
)
RELEASE_ID="$(date -u +%Y%m%dT%H%M%SZ)-${RELEASE_VERSION//+/-}-${WHEEL_SHA:0:8}"
printf 'release id: %s\n' "$RELEASE_ID"

sudo "$BUILD_ROOT/install_lite_release.sh" \
  "$RELEASE_A" "$RELEASE_ID" "$WHEELHOUSE"
```

`install_lite_release.sh` 会检查 manifest 的项目元数据及其指定的 wheel、runtime profile、agent-browser SHA
和 Chrome version，建立独立 venv、离线安装、执行 `pip check`、拒绝 `hermes-agent` 泄漏、检查关键模块
origin，并创建 root-owned immutable release：

```text
/opt/potato-hermes-lite/releases/<release-id>
```

此时 release 仍是 inactive：脚本不修改 `current`、不重启服务、不接触 mapping、Interface 数据库或任何
`HERMES_HOME`。如果 venv 或 pip 阶段失败，可能留下 incomplete final 目录；确认它未被 `current` 引用后再
人工处理，不能直接复用同一个 release ID。

#### 6.5 空主机首次激活

本小节只适用于没有旧 runtime、没有 mapped 用户数据，并且 `current` 与 `/usr/local/bin/hermes` 都不存在的
空主机。任何路径已经存在时都不要用 `ln -sf` 覆盖，应改走下一小节的受保护 cutover。

```bash
test ! -e /opt/potato-hermes-lite/current
test ! -L /opt/potato-hermes-lite/current
test ! -e /usr/local/bin/hermes
test ! -L /usr/local/bin/hermes

ln -s "/opt/potato-hermes-lite/releases/$RELEASE_ID" \
  /opt/potato-hermes-lite/current
ln -s /opt/potato-hermes-lite/current/venv/bin/hermes \
  /usr/local/bin/hermes
```

随后继续安装 Interface、privileged helper 和 systemd unit，再 provision 用户。Interface unit 的
`INTERFACE_TUI_GATEWAY_PYTHON` 必须是 `/opt/potato-hermes-lite/current/venv/bin/python3`，每用户 unit 的
executable、skills、browser 和 runtime profile 也必须指向 current release。

#### 6.6 已有生产环境的受保护切换

`cutover_lite_production.sh` 只适用于已有生产：要求现有 `/usr/local/bin/hermes` 是 symlink、mapping 至少有
一个用户、全部 mapped unit 已存在，并且 inactive release 已由上一小节安装完成。切换会停止 Interface 和
切换前 active 的 Hermes 服务；Interface 停止后，同端口的独立维护服务会返回 HTTP 503 页面。应安排维护窗口
并先完成独立数据备份。状态指纹用于证明停服后的切换期间零变化，不是备份；它不读取用户 workdir，
`.hermes/home` 只做 metadata tree 摘要。

维护服务首次安装会覆盖 `/etc/systemd/system/potato-maintenance.service` 并写入 `/usr/local`，必须先取得 owner
明确批准，不能把下列命令当作普通代码部署的一部分自动执行。批准后在 Interface 仍在线时安装；脚本只执行
`daemon-reload`，不会 start 或 enable 维护服务，也不会修改已有站点配置。首次启用时应先按下文创建
root-owned `CODE_SOURCE` staging，再从 staging 安装，不能依赖尚未完成 cutover 的旧 `/srv` 源码：

```bash
sudo "$CODE_SOURCE/packaging/install_maintenance_mode.sh" "$CODE_SOURCE"

test "$(stat -c '%U:%G:%a' /etc/potato-maintenance.conf)" = root:root:644
grep -Fx 'bind_host = 10.186.0.25' /etc/potato-maintenance.conf
grep -Fx 'bind_port = 3000' /etc/potato-maintenance.conf
! systemctl is-active --quiet potato-maintenance.service
sudo /usr/local/sbin/potato-maintenancectl status
```

维护 server 使用系统 Python 标准库和安装到 `/usr/local/share/potato-agent/maintenance/` 的只读 HTML/logo，
不读取 `/srv/potato_agent`、Interface venv、数据库或 Hermes 状态。unit 使用 `DynamicUser`、只读文件系统和空
capability set；不要为它增加写目录。站点配置只接受具体 IPv4 地址，拒绝 `0.0.0.0`。正常人工切换命令为
`sudo potato-maintenancectl enter|leave|status`；`enter` 失败会恢复 Interface，`leave` 失败会恢复维护页。
不要 enable 维护 unit，开机正常入口仍是 `potato-interface.service`。

维护页只保证 LAN `10.186.0.25:3000`；Interface 停止期间，依赖它的 ZeroTier proxy 可以不可用。已经加载且
没有继续请求服务器的旧标签页不会被主动替换，用户刷新、导航或重新访问后才会看到维护页。未发送草稿和
进行中的上传不会由维护服务恢复。

常规 runtime 或 wheel 内容变更必须使用新建并验证的 inactive release。只有代码切换不改变 Lite wheel，且
`verify_lite.py` 已同时证明当前 immutable release 的 wheel inventory、依赖 lock 和 staging source boundary
全部匹配时，才可显式复用当前 release ID；这类切换仍执行完整备份、源码镜像、unit refresh 和健康验收。

脚本会在停服前强制检查 `CODE_SOURCE` 和 inactive release 归 root 所有且不可由 group/other 写入，并拒绝
staging 中的 symlink 或特殊文件；mapping 必须为 `root:potato-interface 0640`，Interface data 目录必须为
`potato-interface:potato-interface 0700`，`interface.db` 必须为 `0600`。不满足时先按“安全边界”的权限命令
修复并复核，不能跳过 preflight。

不能把 dirty checkout 直接作为 `CODE_SOURCE`。先创建全新的 root-owned staging，排除 legacy 源码、状态、
缓存和生成文件：

```bash
REPO=$PWD
CODE_SOURCE=$BUILD_ROOT/code-source
test ! -e "$CODE_SOURCE"
install -d -o root -g root -m 0755 "$CODE_SOURCE"
rsync -a \
  --exclude '.git/' \
  --exclude '.codex-tmp/' \
  --exclude '.deploy-backups/' \
  --exclude '.mypy_cache/' \
  --exclude '.pytest_cache/' \
  --exclude '.ruff_cache/' \
  --exclude '.tox/' \
  --exclude '__pycache__/' \
  --exclude '*.egg-info/' \
  --exclude 'build/' \
  --exclude 'dist/' \
  --exclude 'htmlcov/' \
  --exclude 'node_modules/' \
  --exclude '.coverage' \
  --exclude '.DS_Store' \
  --exclude '*.bak' \
  --exclude '*.bak-*' \
  --exclude '*.orig' \
  --exclude '*~' \
  --exclude '*.pyc' \
  --exclude '*.pyo' \
  --exclude '*.db' \
  --exclude '*.db-wal' \
  --exclude '*.db-shm' \
  --exclude '*.db-journal' \
  --exclude '*.sqlite' \
  --exclude '*.sqlite-wal' \
  --exclude '*.sqlite-shm' \
  --exclude '*.sqlite-journal' \
  --exclude '*.sqlite3' \
  --exclude '*.sqlite3-wal' \
  --exclude '*.sqlite3-shm' \
  --exclude '*.sqlite3-journal' \
  --exclude '.env' \
  --exclude '/users_mapping.yaml' \
  --exclude '/model_proxy.yaml' \
  --exclude '/interface/data/' \
  --exclude '/hermes-agent/' \
  --exclude '/packaging/hermes/' \
  "$REPO/" "$CODE_SOURCE/"

chown -R root:root "$CODE_SOURCE"
chmod -R a+rX,go-w "$CODE_SOURCE"
test -f "$CODE_SOURCE/interface/app.py"
test ! -e "$CODE_SOURCE/hermes-agent"
test ! -e "$CODE_SOURCE/users_mapping.yaml"
test ! -e "$CODE_SOURCE/model_proxy.yaml"
test -z "$(find "$CODE_SOURCE" -type l -print -quit)"
test -z "$(find "$CODE_SOURCE" -type d ! -perm -0050 -print -quit)"
test -z "$(find "$CODE_SOURCE" -type f ! -perm -0040 -print -quit)"
test -z "$(find "$CODE_SOURCE" -type f \
  \( -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' -o \
     -name '*.db-wal' -o -name '*.db-shm' -o -name '*.db-journal' -o \
     -name '*.sqlite-wal' -o -name '*.sqlite-shm' -o -name '*.sqlite-journal' -o \
     -name '*.sqlite3-wal' -o -name '*.sqlite3-shm' -o -name '*.sqlite3-journal' -o \
     -name '.env' -o -name 'users_mapping.yaml' -o -name 'model_proxy.yaml' -o \
     -name '*.pyc' -o -name '*.pyo' \) -print -quit)"
```

cutover 还会拒绝 `/srv/potato_agent` 中任何遗留的 SQLite/DB 及 WAL/SHM 文件。命中时必须先辨认其用途，
完成独立备份和受控迁移并验证新位置，再人工删除源码树中的旧副本；不能把预检报出的文件当作缓存盲删，
尤其不能直接删除可能承载用户聊天记录的数据库。

每次切换都从当次受保护 mapping 动态读取 mapped user 数，并在执行前人工核对；README 不记录固定数量：

```bash
MAPPING=/var/lib/potato-agent/config/users_mapping.yaml
EXPECTED_USER_COUNT=$(
  /opt/interface-env/bin/python -c \
    'import sys,yaml; print(len((yaml.safe_load(open(sys.argv[1])) or {}).get("users") or []))' \
    "$MAPPING"
)
printf 'reviewed mapped users: %s\n' "$EXPECTED_USER_COUNT"
```

人工确认数量、inactive release 和独立备份后执行：

```bash
sudo "$CODE_SOURCE/hermes-lite/scripts/cutover_lite_production.sh" \
  "$CODE_SOURCE" "$RELEASE_ID" "$EXPECTED_USER_COUNT"
```

cutover 会：

- 从预检到最终验收持有 `/run/lock/potato-agent/maintenance.lock`，拒绝并行维护切换或 cutover，并要求
  Interface、maintenance、model proxy 和全部 mapped unit 处于稳定状态；
- 在 Interface 在线时最多等待 60 秒，直到没有 `pending` 或 `provisioning` signup job；超时保持 Interface 在线
  并中止。随后进入维护模式，依靠 Interface graceful shutdown 收尾已经开始的 provisioning，并在停服后确认
  没有遗留 `provisioning`；活动 Agent 回合、runtime lease 和 turn receipt 不阻塞维护切换；
- 备份 mapping、全部 mapped unit、旧 `current`/`hermes` target、Interface drop-in，并在停服后把整个现有源码树
  保存到 `code-before/`，写完后才创建 `code-before.complete`；
- 记录切换前 active 的服务，只停止并最终恢复这些服务，原本 inactive 的 unit 保持 inactive；
- 停止写入方后采集 mapping、Interface data 和 mapped `HERMES_HOME` 指纹；
- 仅在专用 model proxy usage DB 尚不存在时迁移旧 usage/quota；已有专用 DB 时保持其当前配额和 usage 不变；
- 使用 `rsync -aHAX --checksum --delete-delay` 把部署树精确同步到 `CODE_SOURCE`，删除 staging 中不存在的旧代码
  和构建残留，再以相同 metadata/checksum 规则 dry run 确认零 drift；随后原子切换 `current` 与
  `/usr/local/bin/hermes` 并刷新既有 unit 集；
- 再次采集并比较指纹，要求 `added`、`changed`、`removed` 全为空；
- 先恢复 model proxy 和原 active Hermes 服务，最后停止维护服务并恢复 Interface；检查真实 `/health`、进程使用
  新 release、无 `slash_worker`、`pip check` 和 CLI help。

备份写入 root-only 的
`/var/backups/potato-agent/hermes-lite-cutover/<timestamp>-<release-id>`。切换开始后的错误、中断或终止信号会
恢复旧 unit、drop-in、symlink 和原 active 服务，并从带 complete marker 的 `code-before/` 以
`rsync --delete-delay` 恢复整个旧源码树；因此切换期间新增的代码也会被删除，源码内容和 metadata 回到切换前
快照。成功后的人工回滚仍没有独立的一键脚本，必须保留旧 immutable release 和对应 backup，在维护窗口中按
backup 受控执行；`code-before/` 只覆盖源码树，不替代 mapping、Interface DB 和每用户状态的独立备份。
自动回滚只有在源码、symlink、unit、drop-in、model proxy 和 Hermes 服务全部恢复并验证后，才撤下维护页、
启动旧 Interface、验证真实 `/health` 并写 `rolled_back`；任何前置恢复步骤失败都会写 `rollback_failed` 并继续
展示维护页，不能把部分回滚当成成功。

#### 6.7 部署后验证

使用 cutover 输出的 backup 路径设置 `BACKUP`，然后检查：

```bash
readlink -f /opt/potato-hermes-lite/current
readlink -f /usr/local/bin/hermes
/opt/potato-hermes-lite/current/venv/bin/pip check
/opt/potato-hermes-lite/current/venv/bin/python3 -I -c \
  'import agent.codex_runtime, pathlib, potato_hermes_lite, tui_gateway.entry; \
   print(potato_hermes_lite.__version__); \
   print(pathlib.Path(agent.codex_runtime.__file__).resolve()); \
   print(pathlib.Path(tui_gateway.entry.__file__).resolve())'

systemctl is-active potato-interface.service
! systemctl is-active --quiet potato-maintenance.service
sudo /usr/local/sbin/potato-maintenancectl status
test ! -L /usr/local/libexec/potato-agent-privileged-helper
test "$(stat -c '%U:%G:%a' /usr/local/libexec/potato-agent-privileged-helper)" = root:root:755
cmp -s \
  /srv/potato_agent/packaging/libexec/potato-agent-privileged-helper \
  /usr/local/libexec/potato-agent-privileged-helper
test -z "$(find /srv/potato_agent -xdev -type d -name '__pycache__' -print -quit)"
test -z "$(find /srv/potato_agent -xdev -type f \
  \( -name '*.pyc' -o -name '*.pyo' \) -print -quit)"
systemctl show potato-interface.service --property=Environment --value |
  tr ' ' '\n' |
  grep -Fx INTERFACE_PRIVILEGED_HELPER=/usr/local/libexec/potato-agent-privileged-helper
INTERFACE_EFFECTIVE_ENV=$(systemctl show potato-interface.service --property=Environment --value)
INTERFACE_EFFECTIVE_EXEC=$(systemctl show potato-interface.service --property=ExecStart --value)
printf '%s\n' "$INTERFACE_EFFECTIVE_ENV" | tr ' ' '\n' |
  grep -Fxq INTERFACE_ARCHIVE_RETENTION_DAYS=99999
printf '%s\n' "$INTERFACE_EFFECTIVE_ENV" | tr ' ' '\n' |
  grep -Fxq INTERFACE_ARCHIVE_STORAGE_RETENTION_DAYS=30
INTERFACE_EFFECTIVE_BIND=$(printf '%s\n' "$INTERFACE_EFFECTIVE_ENV" | tr ' ' '\n' |
  sed -n 's/^INTERFACE_BIND_HOST=//p')
test -n "$INTERFACE_EFFECTIVE_BIND"
curl -fsS "http://$INTERFACE_EFFECTIVE_BIND:3000/health"
case "$INTERFACE_EFFECTIVE_EXEC" in
  *'-m interface.serve --port 3000'*) ;;
  *) false ;;
esac
if printf '%s\n' "$INTERFACE_EFFECTIVE_ENV" | tr ' ' '\n' |
   grep -Fxq INTERFACE_ALLOW_INSECURE_HTTP=true; then
  printf '%s\n' "$INTERFACE_EFFECTIVE_ENV" | tr ' ' '\n' |
    grep -Fxq INTERFACE_SESSION_COOKIE_SECURE=false
  test "$INTERFACE_EFFECTIVE_BIND" != 127.0.0.1
  test "$INTERFACE_EFFECTIVE_BIND" != 0.0.0.0
else
  printf '%s\n' "$INTERFACE_EFFECTIVE_ENV" | tr ' ' '\n' |
    grep -Fxq INTERFACE_ALLOW_INSECURE_HTTP=false
  printf '%s\n' "$INTERFACE_EFFECTIVE_ENV" | tr ' ' '\n' |
    grep -Fxq INTERFACE_SESSION_COOKIE_SECURE=true
  test "$INTERFACE_EFFECTIVE_BIND" = 127.0.0.1
fi
HERMES_UNIT=hermes-REPLACE_WITH_MAPPED_USER.service
systemctl status "$HERMES_UNIT" --no-pager

pgrep -af '[s]lash_worker|/opt/[h]ermes-agent|[h]ermes-agent-src'
test -f "$BACKUP/code-before.complete"
cat "$BACKUP/code-sync.log"
cat "$BACKUP/result.txt"
cat "$BACKUP/state-compare.json"
```

`pgrep` 应无输出，`result.txt` 应为 `complete`，状态比较应为
`{"added": [], "changed": [], "removed": []}`。同时确认 Interface 进程中的
`INTERFACE_TUI_GATEWAY_PYTHON` 和切换前 active 的用户服务 cmdline 都来自新 release。

最后从 Web Interface 完成 create/resume、模型切换、图片、skills、interrupt、approval 和 browser 实测。
不要在同一个 mapped Linux 用户下并行手工启动 `tui_gateway.entry`；它具有单实例语义，可能替换正在运行的
gateway guard。任何带 `--delete` 的命令都不得指向 mapping、Interface data、`HERMES_HOME` 或用户 workdir。

#### 6.8 浏览器 CDP 可选配置

immutable release 安装到 `/opt/potato-hermes-lite/releases/<release-id>`，并由
`/opt/potato-hermes-lite/current` 指向当前版本。若另有 root 管理的本地 Chromium CDP supervisor，可提供
已经解析好的 loopback WebSocket；Interface 不会通过 HTTP 探测 `/json/version`：

```yaml
hermes:
  runtime_profile_path: /opt/potato-hermes-lite/current/config/runtime-profile.yaml
  browser_cdp_url: ws://127.0.0.1:9222/devtools/browser/<browser-id>
```

`browser_cdp_url` 只接受无认证、无 query、使用字面量 loopback IP 的 `ws://`/`wss://` DevTools endpoint；
`localhost` 不会触发 DNS 或 hosts 解析。未配置时保持为空，因此 `browser_cdp` 和 `browser_dialog` 不会通过
真实 availability check。它们与 `vision_analyze` 等工具允许按运行环境动态隐藏；27 项始终只是逻辑上限。

新的每用户 Hermes service 默认使用 `/opt/potato-hermes-lite/current/venv/bin/hermes`。现有生产 mapping 保留
`/usr/local/bin/hermes` 兼容入口，但该 symlink 必须解析到 Lite current venv，不能再指向 legacy venv。

Hermes 0.16.0 的 gateway 重启流程默认会等待 `agent.restart_drain_timeout=180` 秒完成 drain。Potato Agent
生成的每用户 systemd unit 默认写入 `TimeoutStopSec=210`，也就是 `restart_drain_timeout + 30` 秒。若在全局
`hermes.config_overrides.agent.restart_drain_timeout` 或用户
`config_overrides.agent.restart_drain_timeout` 中覆盖该值，生成的 unit 会按覆盖值加 30 秒计算；只有显式设置
`hermes.service.timeout_stop_sec` 时才使用手写值。

### 7. 部署空间转录组查看器数据

空间转录组查看器代码随 `potato-agent` 仓库部署，运行数据不放入 Git checkout。默认数据根目录是
`/srv/spatial_data/current`；该路径通常是指向某个 release 目录的软链接。

数据目录需要包含 `datasets.json`，以及其中 `dataRoot` 指向的数据目录。例如当前数据集布局：

```text
/srv/spatial_data/
  current -> releases/2026-06-16
  releases/
    2026-06-16/
      colors.txt
      datasets.json
      data/
        expression.sqlite
        genes.json
        replicates.json
        clusters.json
        contours/
      datasets/
        s1_stem/
          expression.sqlite
          replicates.json
          clusters.json
          colors.txt
          contours/
```

从已有 `web_viewer` 数据目录部署当前数据：

```bash
release=/srv/spatial_data/releases/2026-06-16
mkdir -p "$release"

rsync -a --delete \
  /path/to/web_viewer/datasets.json \
  /path/to/colors.txt \
  /path/to/web_viewer/data \
  /path/to/web_viewer/datasets \
  "$release/"

chown -R root:potato-interface /srv/spatial_data
find /srv/spatial_data -type d -exec chmod 0750 {} +
find /srv/spatial_data -type f -exec chmod 0640 {} +
ln -sfn "$release" /srv/spatial_data/current
chown -h root:potato-interface /srv/spatial_data/current
```

如果不使用默认路径，在 `potato-interface.service` 中设置：

```ini
Environment=SPATIAL_VIEWER_DATA_ROOT=/srv/spatial_data/current
```

查看器入口是 `/spatial`，API 前缀是 `/api/spatial/`。这些接口不要求网页登录态，但文件系统权限仍
只允许 `potato-interface` 读取数据目录；不要把 `expression.sqlite` 放到可被普通用户直接读取的目录。

### 8. 部署 WGCNA 共表达网络查看器数据

WGCNA 共表达网络查看器代码随 `potato-agent` 仓库部署，页面入口是 `/wgcna`，API 前缀是
`/api/wgcna/`。Lite 首页导航栏中的 `WGCNA Network` 会打开这个页面。页面支持搜索基因、按网络
显示 TOM 共表达边，并通过 `Export network data` 导出当前图的 nodes/edges TSV，方便在本地
Cytoscape 中复现基本网络结构和样式映射。

WGCNA 原始结果目录通常是只读目录，例如当前数据源：

```text
/mnt/data/potato_agent/work/WGCNA/03-network
```

导出脚本不要写回原始结果目录。默认导出位置是：

```bash
$HOME/tmp/wgcna_coexpression_export
```

正式部署时，导出的 TSV 快照放在 `/srv/wgcna_data`，并用 `current` 指向当前 release。当前线上
约定布局是：

```text
/srv/wgcna_data/
  current -> releases/20260713_205139
  releases/
    20260713_205139/
      tables/
        networks.tsv
        genes.tsv
        modules.tsv
        network_genes.tsv
        network_gene_kme.tsv
        coexpression_edges_top.tsv.gz
        module_overlaps.tsv
        shared_coexpression_edges.tsv
      logs/
```

运行时 API 不直接查询 TSV，而是查询 PostgreSQL。推荐数据库和 peer auth role：

```text
database: potato_wgcna
role:     potato-interface
url:      postgresql:///potato_wgcna?host=/var/run/postgresql
```

新机器上可以这样创建数据库。后续如果用 SQL 手写授权语句，role 名 `potato-interface` 需要双引号：

```bash
sudo -u postgres createuser --no-superuser --no-createdb --no-createrole potato-interface 2>/dev/null || true
sudo -u postgres createdb -O potato-interface potato_wgcna 2>/dev/null || true
```

从 WGCNA 原始结果导出 TSV：

```bash
cd /srv/potato_agent
export WGCNA_EXPORT_DIR="$HOME/tmp/wgcna_coexpression_export"

/opt/interface-env/bin/python wgcna_export/scripts/export_network_metadata.py
/opt/interface-env/bin/python wgcna_export/scripts/export_gene_module_tables.py
/opt/interface-env/bin/python wgcna_export/scripts/compute_module_overlaps.py
Rscript wgcna_export/scripts/export_tom_top_edges.R \
  --base-dir /mnt/data/potato_agent/work/WGCNA/03-network \
  --output-dir "$WGCNA_EXPORT_DIR" \
  --networks leaf,stem,root,reproductive,tuberization \
  --top-n 100
/opt/interface-env/bin/python wgcna_export/scripts/compute_shared_edges.py
/opt/interface-env/bin/python wgcna_export/scripts/validate_exports.py
```

发布导出快照：

```bash
release=/srv/wgcna_data/releases/$(date +%Y%m%d)
mkdir -p "$release"
rsync -a --delete "$HOME/tmp/wgcna_coexpression_export/" "$release/"

chown -R root:potato-interface /srv/wgcna_data
find /srv/wgcna_data -type d -exec chmod 0750 {} +
find /srv/wgcna_data -type f -exec chmod 0640 {} +
ln -sfn "$release" /srv/wgcna_data/current
chown -h root:potato-interface /srv/wgcna_data/current
```

把当前快照加载到 PostgreSQL：

```bash
sudo -u potato-interface env \
  WGCNA_EXPORT_DIR=/srv/wgcna_data/current \
  WGCNA_DATABASE_URL='postgresql:///potato_wgcna?host=/var/run/postgresql' \
  /opt/interface-env/bin/python /srv/potato_agent/wgcna_export/scripts/load_to_postgresql.py --truncate
```

生产服务通过 systemd drop-in 设置数据库 URL，避免把运行时配置写进源码目录：

```bash
install -d -o root -g root -m 0755 /etc/systemd/system/potato-interface.service.d
cat >/etc/systemd/system/potato-interface.service.d/40-wgcna.conf <<'EOF'
[Service]
Environment=WGCNA_DATABASE_URL=postgresql:///potato_wgcna?host=/var/run/postgresql
EOF
chown root:root /etc/systemd/system/potato-interface.service.d/40-wgcna.conf
chmod 0644 /etc/systemd/system/potato-interface.service.d/40-wgcna.conf
systemctl daemon-reload
systemctl restart potato-interface.service
```

当前 20260713_205139 release 已验证的主表规模：

```text
networks: 5
genes: 18895
modules: 85
network_genes: 60000
network_gene_kme: 1020000
coexpression_edges_top: 6000000
module_overlaps: 504
shared_coexpression_edges: 551763
```

### 9. 部署 Bulk RNA-Seq 表达查看器数据

Bulk RNA-Seq 表达查看器代码随 `potato-agent` 仓库部署，页面入口是 `/bulk-rnaseq`，API 前缀是
`/api/bulk-rnaseq/`。Lite 首页导航栏中的 `Bulk RNA-Seq` 会打开这个页面。用户输入一个或多个
DMv8.2 gene ID 后，页面会从只读 SQLite 查询 TPM、`log2(TPM + 1)` 或 row z-score，并在浏览器端
渲染热图。

原始整理结果目录通常是只读目录，例如当前数据源：

```text
/mnt/data/public_data/Expression_atlas/DMv8.2
```

该目录需要包含：

```text
sample_tissue_list.tsv
transcript_tpm_matrix_merged.tsv
```

构建脚本不会写回原始结果目录。运行数据库默认位置是：

```text
/srv/bulk_rnaseq/current/bulk_rnaseq.sqlite
```

从原始 TSV 构建 SQLite：

```bash
cd /srv/potato_agent
tmp_db=$HOME/tmp/bulk_rnaseq.sqlite
mkdir -p "$(dirname "$tmp_db")"

/opt/interface-env/bin/python -m interface.build_bulk_rnaseq_db \
  --source-root /mnt/data/public_data/Expression_atlas/DMv8.2 \
  --output-db "$tmp_db"
```

构建脚本默认排除非马铃薯材料 `PG0003`、`PG0009`、`PG0019`。当前源数据中 `PG0009` 和
`PG0019` 各 9 个 run，`PG0003` 不存在；构建后的线上库样本数应为 259。排除名单和排除数量会
写入 SQLite 的 `metadata` 表。

发布 SQLite 时先安装到临时文件，再原子替换，避免服务读到半成品数据库：

```bash
install -d -o root -g potato-interface -m 0750 /srv/bulk_rnaseq/current
install -o root -g potato-interface -m 0640 "$tmp_db" \
  /srv/bulk_rnaseq/current/.bulk_rnaseq.sqlite.new
mv /srv/bulk_rnaseq/current/.bulk_rnaseq.sqlite.new \
  /srv/bulk_rnaseq/current/bulk_rnaseq.sqlite

chown -R root:potato-interface /srv/bulk_rnaseq
find /srv/bulk_rnaseq -type d -exec chmod 0750 {} +
find /srv/bulk_rnaseq -type f -exec chmod 0640 {} +
```

生产服务默认读取 `/srv/bulk_rnaseq/current/bulk_rnaseq.sqlite`。如果需要使用其它路径，在
`potato-interface.service` 中设置：

```ini
Environment=BULK_RNASEQ_DB_PATH=/srv/bulk_rnaseq/current/bulk_rnaseq.sqlite
```

当前过滤后数据库已验证的规模：

```text
genes: 37658
samples: 259
sample_name groups: 45
sample_tissue groups: 194
tissue groups: 15
excluded samples: 18
```

### 10. 部署 Gene Catalog 数据

Gene Catalog 代码随仓库部署，公开页面是 `/genes`，详情 deep link 是 `/genes/<gene-id>`，API 前缀是
`/api/v1/genes/`。运行时默认只读打开
`/srv/gene_catalog/current/gene_catalog.sqlite`；数据库不是源码，也不能放进 Git checkout 或
`/srv/potato_agent`。

在受控构建目录中从经过审查的源数据生成完整数据库。builder 内置的个人开发路径不是生产配置；生产构建必须
显式传入全部来源及 release 标识：

```bash
GENE_BUILD_ROOT=/var/tmp/potato-gene-catalog-$(date -u +%Y%m%dT%H%M%SZ)
GENE_BUILD_DB=$GENE_BUILD_ROOT/gene_catalog.sqlite
test ! -e "$GENE_BUILD_ROOT"
install -d -o root -g root -m 0700 "$GENE_BUILD_ROOT"

python3.12 /srv/potato_agent/interface/build_gene_catalog_db.py \
  --genes-db /path/to/reviewed/genes.db \
  --genes-json /path/to/reviewed/genes.json \
  --paper-metadata /path/to/reviewed/paper_metadata.txt \
  --transcript-map /path/to/reviewed/gene_to_transcript.txt \
  --gff /path/to/reviewed/DMv82.gff3 \
  --genome-fasta /path/to/reviewed/DMv82.fa \
  --genome-fai /path/to/reviewed/DMv82.fa.fai \
  --cds-fasta /path/to/reviewed/cds.fa \
  --protein-fasta /path/to/reviewed/pep.fa \
  --annotations /path/to/reviewed/DMv82_annotations.txt \
  --similarity-hits /path/to/reviewed/uniprot_blastp.txt \
  --predictions /path/to/reviewed/predictions.jsonl \
  --prediction-readme /path/to/reviewed/prediction-release-README.md \
  --primary-run-report /path/to/reviewed/primary-run-report.json \
  --retry-185-run-report /path/to/reviewed/retry-185-run-report.json \
  --retry-1-run-report /path/to/reviewed/retry-1-run-report.json \
  --catalog-version REPLACE_WITH_REVIEWED_CATALOG_VERSION \
  --prediction-release REPLACE_WITH_REVIEWED_PREDICTION_RELEASE \
  --output-db "$GENE_BUILD_DB"
```

builder 会先写同目录临时文件并在成功后替换输出，但发布前仍须在所有连接关闭后执行完整性、schema 和 metadata
检查，并确认没有 SQLite sidecar：

```bash
python3.12 - "$GENE_BUILD_DB" <<'PY'
import json
import pathlib
import sqlite3
import sys

path = pathlib.Path(sys.argv[1]).resolve()
required = {
    "catalog_metadata", "catalog_sources", "genes", "gene_identifiers",
    "transcripts", "gene_descriptions", "papers", "paper_local_ids",
    "gene_paper_refs", "gene_annotations", "protein_similarity_hits",
    "transcript_sequences",
}
with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
    if conn.execute("pragma integrity_check").fetchone()[0] != "ok":
        raise SystemExit("gene catalog integrity_check failed")
    tables = {row[0] for row in conn.execute(
        "select name from sqlite_master where type='table'"
    )}
    if not required <= tables:
        raise SystemExit(f"gene catalog tables missing: {sorted(required - tables)}")
    row = conn.execute(
        "select schema_version,catalog_version,counts_json "
        "from catalog_metadata where singleton=1"
    ).fetchone()
    if row is None or row[0] != 1 or not json.loads(row[2]):
        raise SystemExit("gene catalog metadata is invalid")
    print(f"schema={row[0]} catalog={row[1]} counts={row[2]}")
PY
test ! -e "$GENE_BUILD_DB-wal"
test ! -e "$GENE_BUILD_DB-shm"
test ! -e "$GENE_BUILD_DB-journal"
```

把已关闭、已验证的单文件数据库发布到新的 immutable 版本目录，再原子替换 `current` symlink。不要覆盖活动
SQLite，也不要删除旧版本，直到新版本完成 HTTP 验收和回滚观察：

```bash
GENE_RELEASE=$(date -u +%Y%m%dT%H%M%SZ)
GENE_ROOT=/srv/gene_catalog
GENE_RELEASE_DIR=$GENE_ROOT/releases/$GENE_RELEASE
GENE_NEXT_LINK=$GENE_ROOT/.current-$GENE_RELEASE
test ! -e "$GENE_RELEASE_DIR"
test ! -e "$GENE_NEXT_LINK"
install -d -o root -g potato-interface -m 0750 \
  "$GENE_ROOT" "$GENE_ROOT/releases" "$GENE_RELEASE_DIR"
install -o root -g potato-interface -m 0640 \
  "$GENE_BUILD_DB" "$GENE_RELEASE_DIR/gene_catalog.sqlite"
ln -s "releases/$GENE_RELEASE" "$GENE_NEXT_LINK"
mv -T "$GENE_NEXT_LINK" "$GENE_ROOT/current"

test "$(stat -c '%U:%G:%a' "$GENE_RELEASE_DIR")" = root:potato-interface:750
test "$(stat -c '%U:%G:%a' "$GENE_RELEASE_DIR/gene_catalog.sqlite")" = \
  root:potato-interface:640
sudo -u potato-interface test -r "$GENE_ROOT/current/gene_catalog.sqlite"
```

默认路径无需环境变量。若站点必须使用其它路径，在 Interface site drop-in 中显式设置
`GENE_CATALOG_DB_PATH`，并保持相同 owner/mode 和只读发布规则：

```ini
Environment=GENE_CATALOG_DB_PATH=/srv/gene_catalog/current/gene_catalog.sqlite
```

### 11. 安装 interface 运行时

`interface/requirements.txt` 只用于维护直接依赖输入，不能用于生产安装。先在 CPython 3.12/Linux x86_64
构建机上按全量 hash lock 准备一个新的 wheelhouse：

```bash
INTERFACE_SOURCE=/srv/potato_agent/interface
INTERFACE_LOCK=$INTERFACE_SOURCE/requirements-py312-linux-x86_64.lock
INTERFACE_WHEELHOUSE_MANIFEST=$INTERFACE_SOURCE/wheelhouse-py312-linux-x86_64.json
INTERFACE_WHEELHOUSE=/var/tmp/potato-interface-wheelhouse-py312-linux-x86_64
INTERFACE_DOWNLOAD_ENV=/var/tmp/potato-interface-download-env-py312

test ! -e "$INTERFACE_WHEELHOUSE"
test ! -e "$INTERFACE_DOWNLOAD_ENV"
install -d -o root -g root -m 0755 "$INTERFACE_WHEELHOUSE"
python3.12 -m venv "$INTERFACE_DOWNLOAD_ENV"
"$INTERFACE_DOWNLOAD_ENV/bin/python" -m pip download \
  --require-hashes --no-deps --only-binary=:all: \
  --dest "$INTERFACE_WHEELHOUSE" -r "$INTERFACE_LOCK"
```

在创建生产 venv 前，核对 interpreter target、lock SHA256 及 wheelhouse 的精确文件名、大小和 SHA256。这个
检查同时拒绝额外文件和 symlink：

```bash
"$INTERFACE_DOWNLOAD_ENV/bin/python" - \
  "$INTERFACE_LOCK" "$INTERFACE_WHEELHOUSE_MANIFEST" "$INTERFACE_WHEELHOUSE" <<'PY'
import hashlib
import json
import pathlib
import platform
import sys

lock_path, manifest_path, wheelhouse = map(pathlib.Path, sys.argv[1:])
target = {
    "implementation": "cp",
    "machine": "x86_64",
    "platform": "linux",
    "python_version": "3.12",
}
if (
    sys.implementation.name != "cpython"
    or sys.version_info[:2] != (3, 12)
    or sys.platform != "linux"
    or platform.machine() != "x86_64"
):
    raise SystemExit("Interface wheelhouse requires CPython 3.12 on Linux x86_64")

manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
if manifest.get("schema_version") != 1 or manifest.get("target") != target:
    raise SystemExit("Interface wheelhouse manifest target is invalid")
if manifest.get("lock") != {"path": lock_path.name, "sha256": digest(lock_path)}:
    raise SystemExit("Interface lock differs from wheelhouse manifest")

items = manifest.get("files")
if not isinstance(items, list):
    raise SystemExit("Interface wheelhouse file inventory is invalid")
expected = {str(item.get("filename") or "") for item in items}
if "" in expected or len(expected) != len(items):
    raise SystemExit("Interface wheelhouse file inventory has invalid names")
actual = {path.name for path in wheelhouse.iterdir()}
if actual != expected:
    raise SystemExit(
        f"Interface wheelhouse inventory differs: "
        f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
    )
for item in items:
    filename = str(item["filename"])
    if pathlib.Path(filename).name != filename:
        raise SystemExit(f"unsafe wheel filename: {filename}")
    path = wheelhouse / filename
    if path.is_symlink() or not path.is_file():
        raise SystemExit(f"wheel is not a regular file: {filename}")
    if path.stat().st_size != item.get("size") or digest(path) != item.get("sha256"):
        raise SystemExit(f"wheel size or SHA256 mismatch: {filename}")
print(f"verified {len(items)} Interface wheels for CPython 3.12/Linux x86_64")
PY

chown -R root:root "$INTERFACE_WHEELHOUSE"
chmod -R go-w "$INTERFACE_WHEELHOUSE"
```

全新主机随后只从已验证 wheelhouse 离线安装；使用 `python -m pip`，不要依赖可能保留旧 staging shebang 的
`pip` console script：

```bash
test ! -e /opt/interface-env
python3.12 -m venv /opt/interface-env
/opt/interface-env/bin/python -m pip install \
  --require-hashes --no-index --only-binary=:all: \
  --find-links "$INTERFACE_WHEELHOUSE" -r "$INTERFACE_LOCK"
/opt/interface-env/bin/python -m pip check
/opt/interface-env/bin/python -c \
  'import platform,sys; assert sys.implementation.name == "cpython"; assert sys.version_info[:2] == (3, 12); assert sys.platform == "linux"; assert platform.machine() == "x86_64"'
```

已有 `/opt/interface-env` 时不要在原目录 overlay 或只重跑 `venv`，也不要在临时路径创建 venv 后整体移动到
`/opt/interface-env`：console script 的 shebang 会保留创建时绝对路径。应在维护窗口记录两个主服务的原 active
状态，依次停止 Interface 和 model proxy，把旧目录改名保留为 root-only rollback，然后直接在最终路径
`/opt/interface-env` 按上述 lock/wheelhouse 步骤重建、运行 `python -m pip check`，再依次恢复原本 active 的
model proxy 和 Interface 并完成健康验收；失败时恢复旧目录和原 active 服务。如果同一维护窗口还要执行 Lite
cutover，应先让 cutover 在原服务状态下完成切换和健康验收，再单独重建 venv，不能让 cutover 把人为停服状态
记录为原始状态。生产流程任何位置都不得改回 `pip install -r interface/requirements.txt` 或在线
`pip install --upgrade`。

### 12. 配置本地模型代理

上游模型网关需要兼容 OpenAI API。真实上游 API key 只写入 root-owned
`/var/lib/potato-agent/config/model_proxy.yaml`；每个用户的 Hermes 配置只会包含
`http://127.0.0.1:8765/v1` 和独立随机生成的 `pmp_...` token。旧的可预测 token 不再兼容。

```bash
export POTATO_AGENT_MAPPING_PATH=/var/lib/potato-agent/config/users_mapping.yaml
export POTATO_MODEL_PROXY_CONFIG_PATH=/var/lib/potato-agent/config/model_proxy.yaml
```

先创建代理专用身份和私有状态目录，再交互式配置。脚本用无回显输入读取 key，不会把 key 放进 argv：

```bash
groupadd --system potato-model-proxy 2>/dev/null || true
useradd --system --gid potato-model-proxy --groups potato-interface \
  --home-dir /nonexistent --shell /usr/sbin/nologin \
  potato-model-proxy 2>/dev/null || true
usermod -a -G potato-interface potato-model-proxy
install -d -o potato-model-proxy -g potato-model-proxy -m 0700 \
  /var/lib/potato-agent/model-proxy

/opt/interface-env/bin/python /srv/potato_agent/configure_model_proxy.py
```

自动化时只能使用 root-readable 私有文件或已打开的 FD，例如 `--api-key-file /run/private-key-file` 或
`--api-key-fd 3`；不要把真实 key 写在命令行、shell history、环境变量、mapping 或用户 home 中。可选
fallback 使用对应的 `--fallback-api-key-file` 或 `--fallback-api-key-fd`。配置完成后验证：

```bash
test "$(stat -c '%U:%G:%a' /var/lib/potato-agent/config/model_proxy.yaml)" = \
  'root:potato-model-proxy:640'
```

安装并启动本地 proxy service：

```bash
install -D -m 0644 /srv/potato_agent/packaging/systemd/potato-model-proxy.service \
  /etc/systemd/system/potato-model-proxy.service
chown root:potato-model-proxy /var/lib/potato-agent/config/model_proxy.yaml
chmod 0640 /var/lib/potato-agent/config/model_proxy.yaml
systemctl daemon-reload
systemctl enable --now potato-model-proxy.service
```

已有 mapped 用户必须生成随机 token 并同步更新其私有 Hermes 配置：

```bash
/opt/interface-env/bin/python /srv/potato_agent/configure_model_proxy.py \
  --apply-to-users
```

`--apply-to-users` 会先打印摘要，并要求手动输入 `APPLY`，然后才会重写已有用户的 Hermes
配置并重启当前正在运行的 Hermes service。升级旧部署后还必须先运行
`cleanup_hermes_user_keys.py --dry-run` 检查历史 key，再去掉 `--dry-run` 执行清理。该清理只把用户配置改为
本地 proxy，不删除 root-owned `model_proxy.yaml` 中仍在使用的真实上游 key。

### 13. 安装 privileged helper

```bash
mkdir -p /usr/local/libexec
install -o root -g root -m 0755 \
  /srv/potato_agent/packaging/libexec/potato-agent-privileged-helper \
  /usr/local/libexec/potato-agent-privileged-helper

cat >/etc/sudoers.d/potato-agent-interface <<'EOF'
potato-interface ALL=(root) NOPASSWD: /usr/local/libexec/potato-agent-privileged-helper *
EOF

chown root:root /etc/sudoers.d/potato-agent-interface
chmod 0440 /etc/sudoers.d/potato-agent-interface
visudo -cf /etc/sudoers.d/potato-agent-interface
```

helper 只暴露 `interface.privileged_helper` 中实现的固定命令集，并以 `python -B` 启动，避免 root helper
在只读部署树中生成 `__pycache__`。发布的
`potato-interface.service` 固定设置
`INTERFACE_PRIVILEGED_HELPER=/usr/local/libexec/potato-agent-privileged-helper`；部署后不得删除或改成直接执行
`python -m interface.privileged_helper`，否则不会匹配上述 sudoers 白名单。

### 14. 安装 systemd service

默认生产 profile 使用 `Secure` Cookie，必须先配置 HTTPS 反向代理，将外部 HTTPS origin 转发到
`127.0.0.1:3000`，并用实际域名验证：

```bash
PUBLIC_ORIGIN=https://agent.example.com
curl -fsS "$PUBLIC_ORIGIN/health" >/dev/null
```

创建 root-only credential。session secret 应长期保持稳定；全新部署只生成一次。Resend key 使用无回显输入，
不会进入 unit、argv 或导出的环境变量：

```bash
install -d -o root -g root -m 0700 /etc/potato-agent/credentials
SESSION_CREDENTIAL=/etc/potato-agent/credentials/interface-session-secret
test ! -e "$SESSION_CREDENTIAL"
python3 - "$SESSION_CREDENTIAL" <<'PY'
import os
import secrets
import sys

path = sys.argv[1]
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
try:
    os.write(fd, secrets.token_urlsafe(48).encode("ascii"))
finally:
    os.close(fd)
PY

RESEND_CREDENTIAL=/etc/potato-agent/credentials/resend-api-key
test ! -e "$RESEND_CREDENTIAL"
IFS= read -r -s -p 'Resend API key: ' RESEND_KEY
printf '\n'
(umask 077; printf '%s' "$RESEND_KEY" >"$RESEND_CREDENTIAL")
unset RESEND_KEY
chown root:root "$SESSION_CREDENTIAL" "$RESEND_CREDENTIAL"
chmod 0600 "$SESSION_CREDENTIAL" "$RESEND_CREDENTIAL"
```

安装经过审计的 unit；非敏感的站点配置放在单独 drop-in：

```bash
install -D -m 0644 /srv/potato_agent/packaging/systemd/potato-interface.service \
  /etc/systemd/system/potato-interface.service
install -d -o root -g root -m 0755 \
  /etc/systemd/system/potato-interface.service.d
cat >/etc/systemd/system/potato-interface.service.d/30-site.conf <<'EOF'
[Service]
Environment=INTERFACE_FILE_BROWSER_MODE=home_and_public_data
Environment=INTERFACE_RUNTIME_IDLE_TIMEOUT_SECONDS=1800
Environment=POTATO_AGENT_MAPPING_PATH=/var/lib/potato-agent/config/users_mapping.yaml
Environment=INTERFACE_AUTH_DB=/var/lib/potato-agent/data/interface.db
Environment=INTERFACE_ARCHIVE_DB=/var/lib/potato-agent/data/archive.db
Environment=INTERFACE_TUI_GATEWAY_PYTHON=/opt/potato-hermes-lite/current/venv/bin/python3
Environment=SPATIAL_VIEWER_DATA_ROOT=/srv/spatial_data/current
Environment=WGCNA_DATABASE_URL=postgresql:///potato_wgcna?host=/var/run/postgresql
Environment=BULK_RNASEQ_DB_PATH=/srv/bulk_rnaseq/current/bulk_rnaseq.sqlite
Environment=GENE_CATALOG_DB_PATH=/srv/gene_catalog/current/gene_catalog.sqlite
Environment="INTERFACE_MAIL_FROM=Potato Agent <noreply@mail.example.com>"
Environment=INTERFACE_MAIL_REPLY_TO=support@example.com
EOF
chown root:root /etc/systemd/system/potato-interface.service.d/30-site.conf
chmod 0644 /etc/systemd/system/potato-interface.service.d/30-site.conf
systemctl daemon-reload
```

仅当无域名测试服务器已经由 owner 明确接受明文传输风险时，增加下面的高优先级站点 drop-in，并把占位值
替换为该服务器实际对外的 LAN IPv4。地址只存在于 root 管理的站点配置，不写入源码或 packaged unit；使用
明确地址也避免 Interface 同时监听 VPN、ZeroTier 或其它接口：

```bash
cat >/etc/systemd/system/potato-interface.service.d/90-site-insecure-http.conf <<'EOF'
[Service]
Environment=INTERFACE_ALLOW_INSECURE_HTTP=true
Environment=INTERFACE_BIND_HOST=REPLACE_WITH_SERVER_LAN_IPV4
Environment=INTERFACE_SESSION_COOKIE_SECURE=false
ExecStart=
ExecStart=/opt/interface-env/bin/python -m interface.serve --port 3000
EOF
chown root:root /etc/systemd/system/potato-interface.service.d/90-site-insecure-http.conf
chmod 0644 /etc/systemd/system/potato-interface.service.d/90-site-insecure-http.conf
systemctl daemon-reload
```

`ExecStart` 重置只用于覆盖旧部署可能遗留的硬编码 listener；实际地址始终由站点级
`INTERFACE_BIND_HOST` 提供。后续 cutover 会保留并验证这组高优先级配置。

HTTP profile 不得通过把 `INTERFACE_ENVIRONMENT` 改成 `development` 实现；服务必须继续加载 production 的
secret、credential、保留策略和权限检查。由于密码、session Cookie、聊天正文和上传内容都会以明文经过网络，
只能在可信隔离网络使用。部署后必须确认仅实际 LAN IPv4 监听 `3000/tcp`，VPN、ZeroTier 或其它接口不能
访问；站点已有主机防火墙时，还应按实际 LAN interface 和可信来源网段进一步限制来源。

站点已启用 UFW 时，先把两个占位值替换为现场值，再添加限定规则并复核默认入站策略；不要执行不带
interface/source 限制的 `ufw allow 3000/tcp`：

```bash
LAN_INTERFACE=REPLACE_WITH_LAN_INTERFACE
TRUSTED_CIDR=REPLACE_WITH_TRUSTED_CIDR
ufw allow in on "$LAN_INTERFACE" from "$TRUSTED_CIDR" \
  to any port 3000 proto tcp
ufw status verbose
ufw status numbered
ufw show listening
```

正式域名和 HTTPS 入口就绪后，删除 `90-site-insecure-http.conf`，重新加载 systemd 并重启 Interface，即可
恢复 packaged unit 的 loopback + Secure 默认 profile。

注册流程通过 Resend HTTPS API 发送邮箱验证码。`INTERFACE_MAIL_FROM` 必须使用已验证域名下的地址；Resend
credential 或发件地址无效时，发送接口会失败。不要把 credential 的内容复制到 drop-in。

会话归档调度仍每天 03:00 运行，但默认只处理超过 `99999` 天未活跃的会话，即默认
不开启常规自动会话归档。该阈值由 `INTERFACE_ARCHIVE_RETENTION_DAYS` 调整；未经 owner 明确要求，
不得将其降低。归档正文默认只保留 30 天，由 `INTERFACE_ARCHIVE_STORAGE_RETENTION_DAYS`
调整。生产 unit 明确固定为 `99999` 和 `30`，两个值都必须至少为 `1`，且含义不同。

启用服务：

```bash
systemctl daemon-reload
systemctl enable --now potato-interface.service
systemctl status potato-interface.service
```

#### Daily Updates（需单独部署）

Daily Updates 不是只需同步一个 SQLite 的静态数据集。同步源码、重建 Interface venv 或执行 Lite cutover
都不会在新主机上自动创建采集账号、安装定时任务或配置模型代理凭据；只复制数据库只能让前端读取已有内容，
不能产生后续的每日更新。

当前统一数据库及生产目标路径均为：

```text
/srv/daily_updates/data/daily_updates.sqlite
```

首次在生产主机启用时还必须完成以下站点设置：

- 创建 `potato-daily-updates` 系统用户、同名数据组和 `/srv/daily_updates` 权限结构；数据库使用
  `potato-daily-updates:potato-daily-updates 0640`。
- 创建 root-only 的 `daily-updates-model-proxy-token` systemd credential，并让 collector 与 model proxy
  加载同一个 credential；真实上游模型 key 仍只保留在 `model_proxy.yaml`。
- 配置 NCBI 要求的 `DAILY_UPDATES_PUBMED_EMAIL`；NCBI API key 可选，但只能通过独立 systemd
  credential 提供。
- 安装 `potato-daily-updates.service`、`potato-daily-updates.timer`、Interface 数据组 drop-in 和 model proxy
  credential drop-in；确认主机可访问 PubMed，collector 可访问本地模型代理。
- 依次重启 model proxy 和 Interface，手动成功运行一次 collector，再启用每天 `08:00 Asia/Shanghai`
  执行且支持宕机补跑的 timer。

完整命令、权限验收和回滚步骤见
[`interface/DAILY_UPDATES.md`](interface/DAILY_UPDATES.md)。从本服务器向另一台生产服务器同步已经生成的统一
SQLite 时，应先按该文档生成一致性快照，再传输快照；不要在线直接复制正在写入的数据库或其 journal。
目标端应先停止 collector，将快照校验后原子发布到上述路径，再恢复服务。同步统一数据库时跳过旧 Knowledge
Hub 三库迁移步骤。

首次对外开放前还必须执行“一次性安全升级”中的“宿主 `/proc` 隔离（部署时必做）”，完成临时兼容性测试、
`/etc/fstab` 持久化、受控重启和两个普通 mapped 用户的跨 UID 复测；空主机部署也不能跳过。

访问地址：

```text
https://agent.example.com/lite
http://<server-address>:3000/lite  # 仅限显式 HTTP profile
```

显式 HTTP profile 上线后，必须使用新的无痕浏览器 profile 从实际 HTTP origin 验收：完成登录后刷新页面仍保持
认证；DevTools 的 Application/Cookies 中 `potato_interface_token` 应为 `Secure=false`、`HttpOnly=true`、
`SameSite=Lax`、`Path=/`；Console 的 `document.cookie` 不得包含该名称；退出登录并刷新后必须回到未认证状态。
不要复制、截图或输出 Cookie 值及完整 `Set-Cookie` header。还要分别从允许的 LAN 客户端和非授权接口/来源
测试可达与不可达，不能只做服务器本机 curl。

### 15. 创建用户

创建系统托管的 Linux 用户和网页账号：

```bash
/opt/interface-env/bin/python /srv/potato_agent/provision_interface_user.py \
  alice alice@example.com
```

把已有 Linux 用户绑定为网页账号：

```bash
/opt/interface-env/bin/python /srv/potato_agent/bind_existing_linux_user.py \
  alice alice@example.com --linux-user alice
```

两个脚本默认通过 `getpass` 无回显读取并再次确认网页登录密码。非交互开通只能使用 root-readable 私有普通
文件的 `--password-file`，或从受控 pipe 读取一次的 `--password-stdin`；不要把密码放在位置参数、argv、
环境变量或 shell history 中。用户本地 Hermes API key 和 model proxy token 均由脚本随机生成，不接受命令行
传入的用户级 API key；per-user API key 使用独立的 256-bit 随机值。

创建或绑定用户会写入映射、创建网页登录记录、安装该用户的 Hermes runtime 文件，并创建该用户的
systemd unit。安装 runtime 文件时会在用户 home 下创建 `public_data` 软链接，指向
`/mnt/data/public_data`；共享数据目录的读写权限由该目录自身权限控制，开通流程不会修改它。
Hermes service 默认保持 disabled，用户进入 workspace 时再按需启动。

## 升级已有部署

自动 Lite cutover 只支持状态边界已经稳定的生产环境。开始前必须同时满足：

- mapping 位于 `/var/lib/potato-agent/config/users_mapping.yaml`，Interface 数据库位于
  `/var/lib/potato-agent/data/`，且 systemd 实际使用这些路径；
- 每个 mapped 用户的 `HERMES_HOME` 和 workdir 都在源码树外；
- mapping 至少包含一个用户，全部 mapped unit 已存在，`/usr/local/bin/hermes` 是 symlink；
- 已完成独立、可恢复的数据备份，而不是只依赖 cutover 的状态指纹。

如果 mapping、任一 Interface DB 或其 WAL/SHM 仍在 `/srv/potato_agent`，或者新旧位置同时存在状态，这属于
单独的旧状态迁移，不得与 Lite release 切换合并执行。本 README 不提供危险的文件复制捷径：迁移必须在
维护窗口记录原 active 服务、确认所有写入方已停止，使用 SQLite backup API 先生成并校验完整私有 staging，
再把 mapping 和全部数据库作为一个受控事务发布；任何失败都必须撤销新路径/drop-in 并恢复原 active 集合。
完成迁移、Web 验收和独立回滚验证后，才能继续下面的 Lite cutover。

不要对已有生产重复“全新部署”中的 `rsync --delete`，也不要直接把 dirty checkout 覆盖到
`/srv/potato_agent`。

### 1. 一次性安全升级

从旧的明文 secret、可预测 proxy token 或共享 proxy 身份升级时，必须先按 6.1 至 6.4、6.6 小节准备好
inactive release 和全新的 root-owned `CODE_SOURCE`，但暂时不要运行 cutover。以下步骤在同一个维护窗口内
完成。确认当前配置的 public origin 健康后立即让入口进入维护模式，并保持到随机 token 迁移和全部验收结束；
本机 loopback 健康检查仍需可用。默认生产入口必须是 HTTPS；只有已按“安装 systemd service”小节配置并由
owner 批准的隔离测试服务器，才可在维护窗口继续使用显式 HTTP profile。

首次从不认识 `INTERFACE_ALLOW_INSECURE_HTTP` 的旧版本升级时，不得在 cutover 前创建
`90-site-insecure-http.conf`。先保持旧的 loopback + Secure profile 完成代码切换并验证新 Interface 能启动，
再安装 HTTP drop-in 和重启；这样切换失败时不会把旧代码恢复到它无法识别的 Cookie 配置。已经运行支持该
开关的版本后，后续 cutover 会保留并验证现有 HTTP drop-in。

先确认 public origin、独立备份和 mapping 边界。正式生产使用 HTTPS 示例；已经启用显式 HTTP profile 的
版本将其替换为 `http://<server-address>:3000`。首次引入该 profile 时，cutover 前只检查
`http://127.0.0.1:3000/health`，安装 HTTP drop-in 后再检查实际 public origin：

```bash
PUBLIC_ORIGIN=https://agent.example.com
curl -fsS "$PUBLIC_ORIGIN/health" >/dev/null

CODE_SOURCE=/root/potato-agent-code-source
MAPPING=/var/lib/potato-agent/config/users_mapping.yaml
AUTH_DB=/var/lib/potato-agent/data/interface.db
test -f "$CODE_SOURCE/interface/app.py"
test -f "$MAPPING"
test -f "$AUTH_DB"

PYTHONPATH="$CODE_SOURCE" \
POTATO_AGENT_MAPPING_PATH="$MAPPING" \
/opt/interface-env/bin/python - "$MAPPING" <<'PY'
import pathlib
import sys

import yaml

from interface.mapping import (
    build_targets_from_config,
    ensure_model_proxy_tokens,
    ensure_unique_user_api_keys,
)

path = pathlib.Path(sys.argv[1])
config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
expected = len(config.get("users") or [])
rotated_tokens = ensure_model_proxy_tokens(config)
rotated_api_keys = ensure_unique_user_api_keys(config)
targets = build_targets_from_config(config, resolve_env=False)
if not targets or len(targets) != expected:
    raise SystemExit("mapping contains an invalid or unresolved user entry")
print(
    f"validated mapped users: {len(targets)}; "
    f"credentials requiring migration: {rotated_tokens + rotated_api_keys}"
)
PY

/opt/interface-env/bin/python - "$AUTH_DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True) as conn:
    conflicts = conn.execute(
        "select mapping_username, count(*) from users "
        "where trim(mapping_username) <> '' group by mapping_username having count(*) > 1"
    ).fetchall()
if conflicts:
    raise SystemExit(f"duplicate auth mapping_username rows: {len(conflicts)}")
print("auth mapping usernames are unique")
PY
```

该预检只在内存副本中临时生成缺失/重复 proxy token 和旧共享/占位 per-user API key，不写回 mapping；因此
允许第二阶段可迁移的 legacy credential，但仍拒绝多个用户共享 username、非空 email、Linux 用户、home、
`HERMES_HOME`、workdir、`state.db`、systemd service、`(host, port)` endpoint、已是独立值的 per-user API key
或 model proxy token。任何资源冲突都必须先人工核对并修正，不能让脚本猜测聊天记录应归属哪个账号。
下面的私有状态检查在 cutover 前运行一次，并在随机 token 迁移后再运行一次；它不读取或输出
`processes.json` 内容，只验证 owner/type 并把已有文件收紧为 `0600`：

```bash
PYTHONPATH="$CODE_SOURCE" \
/opt/interface-env/bin/python - "$MAPPING" <<'PY'
import os
import pathlib
import pwd
import stat
import sys

import yaml

from interface.mapping import (
    build_targets_from_config,
    ensure_model_proxy_tokens,
    ensure_unique_user_api_keys,
)

checked = 0
fixed = 0
mapping_path = pathlib.Path(sys.argv[1])
config = yaml.safe_load(mapping_path.read_text(encoding="utf-8")) or {}
ensure_model_proxy_tokens(config)
ensure_unique_user_api_keys(config)
for target in build_targets_from_config(config, resolve_env=False):
    path = target.hermes_home / "processes.json"
    try:
        fd = os.open(
            path,
            os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
    except FileNotFoundError:
        continue
    try:
        info = os.fstat(fd)
        account = pwd.getpwnam(target.linux_user)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != account.pw_uid:
            raise SystemExit("unsafe processes.json type or owner")
        checked += 1
        if stat.S_IMODE(info.st_mode) != 0o600:
            os.fchmod(fd, 0o600)
            fixed += 1
    finally:
        os.close(fd)
print(f"checked processes.json files: {checked}; modes repaired: {fixed}")
PY
```

在旧 Interface 进程仍运行时，无回显地把当前 Resend key 写入 systemd credential。该步骤保留原 key，
不会轮换它，也不会把值打印到终端。若 credential 已在此前成功创建，不要覆盖；先确认这是同一次迁移留下的
文件再继续：

旧部署把 Resend key 放在 systemd `Environment=` 中，而现网 `systemctl show ... Environment` 对本机普通用户
可读，因此该 key 已经处于可获取的暴露面。按当前决定保留原 key 只能停止后续暴露，不能证明历史上无人复制；
若 Resend 出现滥发、异常调用或其它可疑活动，应立即在 Resend 控制台轮换，再无回显更新 credential。

```bash
install -d -o root -g root -m 0700 /etc/potato-agent/credentials
RESEND_CREDENTIAL=/etc/potato-agent/credentials/resend-api-key
test ! -e "$RESEND_CREDENTIAL"
INTERFACE_PID=$(systemctl show potato-interface.service --property=MainPID --value)
test "$INTERFACE_PID" -gt 0
python3 - "$INTERFACE_PID" "$RESEND_CREDENTIAL" <<'PY'
import os
import pathlib
import sys

pid, destination = sys.argv[1:]
entries = pathlib.Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
prefix = b"INTERFACE_RESEND_API_KEY="
values = [entry[len(prefix):] for entry in entries if entry.startswith(prefix)]
if len(values) != 1 or not values[0]:
    raise SystemExit("running Interface process does not contain exactly one Resend key")
fd = os.open(
    destination,
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
    0o600,
)
try:
    os.write(fd, values[0])
finally:
    os.close(fd)
PY
chown root:root "$RESEND_CREDENTIAL"
chmod 0600 "$RESEND_CREDENTIAL"
```

若旧服务已经不在运行，必须从现有 root-only secret source 直接写入上述 credential，不能通过终端回显、
命令参数或临时 world-readable 文件中转。随后生成一个新的 session secret；这次轮换会在新版 Interface
启动后注销所有现有浏览器会话，用户需要重新登录：

```bash
SESSION_CREDENTIAL=/etc/potato-agent/credentials/interface-session-secret
test ! -e "$SESSION_CREDENTIAL"
python3 - "$SESSION_CREDENTIAL" <<'PY'
import os
import secrets
import sys

fd = os.open(
    sys.argv[1],
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
    0o600,
)
try:
    os.write(fd, secrets.token_urlsafe(48).encode("ascii"))
finally:
    os.close(fd)
PY
chown root:root "$SESSION_CREDENTIAL"
chmod 0600 "$SESSION_CREDENTIAL"
test "$(stat -c '%U:%G:%a' /etc/potato-agent/credentials)" = 'root:root:700'
test "$(stat -c '%U:%G:%a' "$SESSION_CREDENTIAL")" = 'root:root:600'
test "$(stat -c '%U:%G:%a' "$RESEND_CREDENTIAL")" = 'root:root:600'
```

这里的 `0600` 是 `/etc/potato-agent/credentials` 中持久化源文件的边界。systemd 会在服务的私有、只读
`$CREDENTIALS_DIRECTORY` 中以 `0550` 目录和 `0440` 文件呈现 `LoadCredential=` 内容；新版 loader 会验证
该目录无 symlink、由 root 或服务用户拥有、group 不可写且 other 无权限，并仅在这个边界内接受 credential
文件的 group-read 位。普通 `INTERFACE_*_FILE` 路径仍必须没有任何 group/other 权限。

创建独立 proxy 身份和状态目录，并只调整现有 `model_proxy.yaml` 的读取边界。此时不要运行
`configure_model_proxy.py`，mapping 和用户配置必须继续使用旧 token，直到 cutover 已成功完成：

```bash
groupadd --system potato-model-proxy 2>/dev/null || true
useradd --system --gid potato-model-proxy --groups potato-interface \
  --home-dir /nonexistent --shell /usr/sbin/nologin \
  potato-model-proxy 2>/dev/null || true
usermod -a -G potato-interface potato-model-proxy
install -d -o potato-model-proxy -g potato-model-proxy -m 0700 \
  /var/lib/potato-agent/model-proxy
chown root:potato-model-proxy /var/lib/potato-agent/config/model_proxy.yaml
chmod 0640 /var/lib/potato-agent/config/model_proxy.yaml
test "$(stat -c '%U:%G:%a' /var/lib/potato-agent/config/model_proxy.yaml)" = \
  'root:potato-model-proxy:640'
test "$(stat -c '%U:%G:%a' /var/lib/potato-agent/model-proxy)" = \
  'potato-model-proxy:potato-model-proxy:700'
```

不要提前覆盖或 reload 两个主 unit；cutover 会在停服后备份旧主 unit，再从 `CODE_SOURCE` 安装新版本，失败时
自动恢复。先检查历史 Interface drop-in：如果使用 `EnvironmentFile=`，把仍需保留的非敏感值改写到普通
`Environment=` drop-in；direct session/Resend assignment 会由 cutover 在 root-only 备份后删除。下面只打印
相关文件名，不打印 secret 值：

```bash
grep -RIlE \
  '^[[:space:]]*(EnvironmentFile[[:space:]]*=|Environment[[:space:]]*=[[:space:]]*"?INTERFACE_(SESSION_SECRET|RESEND_API_KEY)=)' \
  /etc/systemd/system/potato-interface.service.d 2>/dev/null || true
```

为 cutover 后的第二阶段另建 root-only 状态备份。它保存 live mapping、proxy 配置以及每个 mapped 用户的
`config.yaml`/`.env`，但不读取或复制聊天数据库。保留输出的 `SECURITY_BACKUP` 路径：

```bash
SECURITY_BACKUP=/var/backups/potato-agent/security-migration/$(date -u +%Y%m%dT%H%M%SZ)
install -d -o root -g root -m 0700 "$SECURITY_BACKUP/users"
install -o root -g root -m 0600 "$MAPPING" "$SECURITY_BACKUP/users_mapping.yaml"
install -o root -g root -m 0600 \
  /var/lib/potato-agent/config/model_proxy.yaml \
  "$SECURITY_BACKUP/model_proxy.yaml"

PYTHONPATH="$CODE_SOURCE" \
/opt/interface-env/bin/python - "$MAPPING" "$SECURITY_BACKUP" <<'PY'
import json
import os
import pathlib
import sys

from interface.mapping import MappingStore
from interface.user_private_files import read_user_private_text

mapping_path = pathlib.Path(sys.argv[1])
backup = pathlib.Path(sys.argv[2])
manifest = []
targets = MappingStore(mapping_path).load_targets()
for index, target_user in enumerate(targets):
    destination = backup / "users" / f"{index:04d}"
    destination.mkdir(mode=0o700)
    sources = {}
    for name in ("config.yaml", ".env"):
        source = target_user.hermes_home / name
        value = read_user_private_text(target_user, source)
        if value is None:
            continue
        target_path = destination / name
        fd = os.open(
            target_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        try:
            os.write(fd, value.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        sources[name] = str(source)
    manifest.append({"index": index, "files": sources})
manifest_path = backup / "user-files.json"
manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
os.chown(manifest_path, 0, 0)
os.chmod(manifest_path, 0o600)
print(f"backed up mapped runtime configs: {len(manifest)}")
PY
```

在第一次启动新 Interface 前确认 `archive.db` 已包含在独立备份中。新版启动时会立即清理归档时间超过 30 天的
正文和旧运行记录，之后每天继续清理；这是有意的数据最小化，不能从 live DB 撤销。

现在按 6.6 小节运行 cutover。脚本会记录原 active 集合后统一停止 Interface、proxy 和 Hermes，使用
`migrate_model_proxy_usage.py` 从 `interface.db` 幂等迁移 usage/quota 到专用 `usage.db`，再采集状态指纹、
切换代码、备份并安装两个主 unit，清理旧 secret、`EnvironmentFile=` 和归档保留期 drop-in 后启动服务。
新版主 unit 会强制使用 99999 天在线会话归档阈值和 30 天归档正文保留，旧 drop-in 中的归档阈值
覆盖不会延续。迁移只复制
usage/quota 表，不复制认证数据或聊天记录。不要在 cutover 前手工运行迁移，否则无法得到维护窗口停写后的
完整快照。

cutover 报错时会自动恢复旧 code、两个主 unit、Interface drop-in、symlink 和原 active 服务；因为此时还没有
随机化 token，旧模型链路也能恢复。这是自动回滚边界。cutover 成功后，新 proxy 会拒绝旧可预测 token，模型
请求暂时 fail closed；保持公网维护模式，立即执行第二阶段迁移。

旧上游模型 key 曾写入每个普通用户的 `~/.hermes/config.yaml`，必须视为已暴露。先在上游供应商创建全新的
primary key，以及每个 fallback/model option 各自的新 key。下面用 root-only 临时文件注入 primary key，真实
值不会进入 argv 或导出的环境变量：

```bash
PRIMARY_MODEL_KEY_FILE=$(mktemp /run/potato-primary-model-key.XXXXXX)
chmod 0600 "$PRIMARY_MODEL_KEY_FILE"
trap 'rm -f "$PRIMARY_MODEL_KEY_FILE"' EXIT
IFS= read -r -s -p 'New primary model API key: ' PRIMARY_MODEL_KEY
printf '\n'
printf '%s' "$PRIMARY_MODEL_KEY" >"$PRIMARY_MODEL_KEY_FILE"
unset PRIMARY_MODEL_KEY

PYTHONPATH=/srv/potato_agent \
/opt/interface-env/bin/python /srv/potato_agent/configure_model_proxy.py \
  --mapping "$MAPPING" \
  --proxy-config /var/lib/potato-agent/config/model_proxy.yaml \
  --api-key-file "$PRIMARY_MODEL_KEY_FILE" \
  --apply-to-users

rm -f "$PRIMARY_MODEL_KEY_FILE"
trap - EXIT

PYTHONPATH=/srv/potato_agent \
/opt/interface-env/bin/python /srv/potato_agent/cleanup_hermes_user_keys.py \
  --mapping "$MAPPING" --dry-run
PYTHONPATH=/srv/potato_agent \
/opt/interface-env/bin/python /srv/potato_agent/cleanup_hermes_user_keys.py \
  --mapping "$MAPPING"

test "$(stat -c '%U:%G:%a' /var/lib/potato-agent/model-proxy/usage.db)" = \
  'potato-model-proxy:potato-model-proxy:600'
test "$(stat -c '%U:%G:%a' /var/lib/potato-agent/config/model_proxy.yaml)" = \
  'root:potato-model-proxy:640'
test ! -e /srv/potato_agent/configure_hermes_model.py
test ! -e /srv/potato_agent/interface/test_configure_hermes_model.py
systemctl enable potato-interface.service potato-model-proxy.service
systemctl is-active --quiet potato-model-proxy.service
systemctl is-active --quiet potato-interface.service
```

`cleanup_hermes_user_keys.py --dry-run` 中 `config_api_key=True` 不能单独判定泄露：新版合法的随机 proxy token
本身就位于 `model.api_key`。必须再运行下面的结构化验收，要求该字段恰好等于用户 mapping 中的
`model_proxy_token`，其它 profile-managed `api_key` 占位只能为空，并要求 `.env` 完全不含
`OPENAI_API_KEY`。探针通过用户私有文件 reader 读取，只报告用户名和路径，不打印配置值或 key：

```bash
PYTHONPATH=/srv/potato_agent \
/opt/interface-env/bin/python - "$MAPPING" <<'PY'
import pathlib
import sys

import yaml

from interface.mapping import MappingStore
from interface.user_private_files import read_user_private_text


def api_key_values(value, path=(), ancestors=frozenset()):
    if isinstance(value, (dict, list)):
        identity = id(value)
        if identity in ancestors:
            yield (*path, "<recursive>"), "<recursive>"
            return
        ancestors = ancestors | {identity}
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = (*path, str(key))
            if str(key) == "api_key":
                yield child_path, child
            yield from api_key_values(child, child_path, ancestors)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from api_key_values(child, (*path, str(index)), ancestors)


mapping_path = pathlib.Path(sys.argv[1]).resolve()
violations = []
targets = MappingStore(mapping_path).load_targets()
for target in targets:
    config_path = target.hermes_home / "config.yaml"
    env_path = target.hermes_home / ".env"
    config_text = read_user_private_text(target, config_path)
    env_text = read_user_private_text(target, env_path)
    try:
        config = yaml.safe_load(config_text or "") or {}
    except Exception:
        violations.append(f"{target.username}: invalid {config_path}")
        continue
    model = config.get("model") if isinstance(config, dict) else None
    if not isinstance(model, dict) or str(model.get("api_key") or "").strip() != target.model_proxy_token:
        violations.append(f"{target.username}: wrong proxy token in {config_path}")
    unexpected = [
        path
        for path, value in api_key_values(config)
        if path != ("model", "api_key") and str(value or "").strip()
    ]
    if unexpected:
        violations.append(f"{target.username}: extra nonempty api_key in {config_path}")
    if "OPENAI_API_KEY" in (env_text or ""):
        violations.append(f"{target.username}: legacy key name in {env_path}")
if violations:
    raise SystemExit("user model credential validation failed:\n" + "\n".join(violations))
print(f"validated user model credentials: {len(targets)}")
PY
```

存在 fallback 时必须在同一次调用中增加 `--fallback-api-key-file` 指向它的新私有 key 文件；每个额外 model
option 也必须通过 `--option 'id=...,model=...,base_url=...,api_key_file=/run/private-file'` 提供新 key。先审查
`model_proxy.yaml.models`，确保没有任何 route 默默保留旧 key。`--apply-to-users` 是首次随机 token 迁移的
必需步骤。

完成一个经过 proxy 的真实模型请求并确认所有 route 可用后，立即在上游供应商撤销旧 primary、fallback 和
option key。临时复用旧 key 只能用于受控过渡，不算完成本次安全修复。新 key 自动化输入只能使用
root-readable 文件或已打开 FD。

进入第二阶段后，cutover 不再提供自动回滚：若命令中断，
应优先修正原因并继续 roll forward；若必须回退，必须同时使用 `SECURITY_BACKUP` 恢复 mapping、
`model_proxy.yaml` 和全部用户配置，并使用 cutover backup 恢复旧 code、两个主 unit、drop-in 与 symlink。只恢复
其中一组会让新旧 token 不匹配，不能启动对外服务。

cutover 的代码/unit 自动回滚也不能撤销新版 Interface 首次启动时已经执行的 30 天归档清理，因此独立的
`archive.db` 备份是回退前提。`SECURITY_BACKUP` 含真实模型 key 和用户 runtime 凭据，必须始终保持
`root:root 0700/0600`，不得同步到代码仓库；过了明确的回滚保留期后按站点 secret 备份策略处置。
cutover backup 中的旧主 unit/drop-in 也可能包含仍有效的 Resend key，必须按同一 secret 备份等级保护和清理。

旧版本可能在共享 `/tmp` 留下命令脚本、结果、会话目录，以及顶层的 snapshot shell/cwd 文件。新版私有
`TMPDIR` 不会自动清理这些历史文件。完成 cutover 后先只列出 metadata，不读取或输出内容：

```bash
find /tmp -xdev -mindepth 1 -maxdepth 1 -type d \
  \( -name 'hermes-runtime-*' -o -name 'hermes-background-*' -o \
     -name 'hermes-results' -o -name 'hermes-command-*' -o \
     -name 'hermes_rpc' \) \
  -printf '%M %u:%g %TY-%Tm-%TdT%TH:%TM:%TS %p\n'

find /tmp -xdev -mindepth 1 -maxdepth 1 -type f \
  \( -name 'hermes-snap-*.sh' -o -name 'hermes-cwd-*.txt' \) \
  -printf '%M %u:%g %s %TY-%Tm-%TdT%TH:%TM:%TS %p\n'
```

目录逐个用 `lsof +D <reviewed-directory>` 检查；顶层文件逐个用 `lsof -- <reviewed-file>` 检查。对每个已经
人工核对的路径先执行下面的边界检查；若仍被使用，顶层文件至少立即收紧为 `0600`，目录和目录下的普通文件
分别收紧为 `0700`/`0600`，待不再使用后再按完整路径删除。若 `lsof` 已确认无进程使用，可以直接删除。
不要 `cat` 文件，也不要把未审查的递归通配符交给删除命令：

```bash
REVIEWED_TMP_FILE=/tmp/hermes-snap-REPLACE_WITH_REVIEWED_NAME.sh
case "$REVIEWED_TMP_FILE" in
  /tmp/hermes-snap-*.sh|/tmp/hermes-cwd-*.txt) ;;
  *) echo 'refusing unexpected tmp path' >&2; exit 2 ;;
esac
test -f "$REVIEWED_TMP_FILE"
test ! -L "$REVIEWED_TMP_FILE"
lsof -- "$REVIEWED_TMP_FILE"
chmod 0600 -- "$REVIEWED_TMP_FILE"
# 仅当上一条 lsof 已确认无进程使用时：
rm -f -- "$REVIEWED_TMP_FILE"
```

目录必须使用另一个变量逐个处理，不能把文件模板中的 `0600` 直接套在目录上。下面的 special-entry 检查必须
没有输出；出现 symlink、socket、设备或其它非普通项时先人工核对，不能继续递归改权限或删除。`rm -rf` 只允许
使用已经通过 `case`、类型、symlink、special-entry 和 `lsof` 检查的这个精确路径：

```bash
REVIEWED_TMP_DIR=/tmp/hermes-results
case "$REVIEWED_TMP_DIR" in
  /tmp/hermes-runtime-*|/tmp/hermes-background-*|/tmp/hermes-results|\
  /tmp/hermes-command-*|/tmp/hermes_rpc) ;;
  *) echo 'refusing unexpected tmp directory' >&2; exit 2 ;;
esac
test -d "$REVIEWED_TMP_DIR"
test ! -L "$REVIEWED_TMP_DIR"
if find "$REVIEWED_TMP_DIR" -xdev -mindepth 1 \
  ! -type d ! -type f -print -quit | grep -q .; then
  find "$REVIEWED_TMP_DIR" -xdev -mindepth 1 \
    ! -type d ! -type f -printf 'special-entry %y %p\n'
  echo 'refusing tmp directory with special entries' >&2
  exit 2
fi
lsof +D "$REVIEWED_TMP_DIR"
find "$REVIEWED_TMP_DIR" -xdev -type d -exec chmod 0700 -- {} +
find "$REVIEWED_TMP_DIR" -xdev -type f -exec chmod 0600 -- {} +
# 仅当 special-entry 没有输出且 lsof 已确认无进程使用时：
rm -rf -- "$REVIEWED_TMP_DIR"
```

处理后重新运行两个顶层 `find`；已保留的文件必须显示 `-rw-------`，已保留目录必须显示 `drwx------`，其下
普通文件也必须为 `0600`。用另一个普通 mapped 用户分别执行 `test ! -r <reviewed-file>` 和
`test ! -x <reviewed-directory>`；发现任何 `0644`、`0755` 或跨用户可读/可遍历项都不能结束维护模式。

#### 宿主 `/proc` 隔离（部署时必做）

普通登录用户在默认 procfs 上可以读取其它 UID 的部分进程 metadata 和 argv。每用户 systemd unit 中的
`ProtectProc=invisible`/`ProcSubset=pid` 只改变该 unit 自己看到的 mount namespace，不能保护普通用户从宿主
shell 访问 `/proc`。新部署和本次安全升级都必须在维护窗口验证并持久化宿主 `hidepid=2`；先备份并记录现状：

```bash
PROC_BACKUP=/var/backups/potato-agent/proc-hardening/$(date -u +%Y%m%dT%H%M%SZ)
install -d -o root -g root -m 0700 "$PROC_BACKUP"
cp -a /etc/fstab "$PROC_BACKUP/fstab"
findmnt -no SOURCE,FSTYPE,OPTIONS /proc >"$PROC_BACKUP/proc-before.txt"
test "$(findmnt -no FSTYPE /proc)" = proc
```

先盘点监控、EDR、备份和容器服务是否确实需要查看其它 UID 的进程。默认不设例外；确有兼容性要求时，创建
专用 system group `potato-proc-inspectors`，在 mount option 中使用它的数字 `gid`，并且只把经过审查的系统
服务账号加入该组。`potato-interface`、`potato-model-proxy`、所有 mapped Hermes 用户和普通登录用户都不能
加入；服务账号加组后必须重启对应服务才能获得新 supplementary group。

先做临时 remount 和兼容性验收，尚不要编辑 `/etc/fstab`。无例外时使用第一组 options；只有确需例外时才用
第二组：

```bash
PROC_MOUNT_OPTIONS=nosuid,nodev,noexec,hidepid=2

# 仅在已审查的系统服务确需跨 UID 查看进程时使用：
# groupadd --system potato-proc-inspectors 2>/dev/null || true
# PROC_INSPECT_GID=$(getent group potato-proc-inspectors | cut -d: -f3)
# test -n "$PROC_INSPECT_GID"
# PROC_MOUNT_OPTIONS=nosuid,nodev,noexec,hidepid=2,gid=$PROC_INSPECT_GID

mount -o "remount,rw,$PROC_MOUNT_OPTIONS" /proc
findmnt -no OPTIONS /proc | tr ',' '\n' | grep -Eq '^hidepid=(2|invisible)$'
systemctl is-active --quiet potato-model-proxy.service
systemctl is-active --quiet potato-interface.service
INTERFACE_HEALTH_HOST=$(systemctl show potato-interface.service --property=Environment --value |
  tr ' ' '\n' | sed -n 's/^INTERFACE_BIND_HOST=//p')
test -n "$INTERFACE_HEALTH_HOST"
curl -fsS "http://$INTERFACE_HEALTH_HOST:3000/health" >/dev/null
```

部分 util-linux 版本会把数值 `hidepid=2` 规范化显示为等价的 `hidepid=invisible`；上面的验收同时接受两种
显示形式，`/etc/fstab` 仍使用可移植的数值形式 `hidepid=2`。

同时检查站点实际使用的监控/容器服务和一次真实模型请求。任何服务因 `/proc` 可见性失败时，先判断是否真的
需要跨 UID 权限；只有必要的专用服务账号可以进入例外组，不能通过把普通用户或两个 Potato 服务身份加组来
绕过隔离。

临时验收通过后，人工审查 `/etc/fstab` 中现有 `/proc` 条目：已有条目就保留其安全选项并加入
`hidepid=2`，没有条目才新增。无例外时目标行如下；需要例外时追加数字 `gid=<PROC_INSPECT_GID>`。文件中只能
有一个有效 `/proc` 条目：

```fstab
proc /proc proc nosuid,nodev,noexec,hidepid=2 0 0
# 仅在必要时使用这一形式，并把 987 替换为已审查 group 的实际数字 GID：
# proc /proc proc nosuid,nodev,noexec,hidepid=2,gid=987 0 0
```

保存后运行 `findmnt --verify --verbose`，并在受控重启后再次确认 `/proc` options 和上述服务。若临时或重启
验收失败，保持公网维护模式，先用 `$PROC_BACKUP/fstab` 恢复 `/etc/fstab`，再按记录的原 options remount；
标准原配置可这样即时撤销，随后复测受影响服务：

```bash
cp -a "$PROC_BACKUP/fstab" /etc/fstab
mount -o remount,rw,nosuid,nodev,noexec,hidepid=0 /proc
findmnt -no SOURCE,FSTYPE,OPTIONS /proc
```

最后选择两个不同的普通 mapped Linux 用户，并让被测目标 service 保持运行。下面的检查不得读取或打印文件
内容；`hidepid=2` 下其它 UID 的进程目录应不可见，而用户自己的 `/proc/self` 仍可读：

```bash
ORDINARY_USER=hmx_alice
OTHER_USER_SERVICE=hermes-bob.service
test "$ORDINARY_USER" != jiayuxin
OTHER_USER_PID=$(systemctl show "$OTHER_USER_SERVICE" --property=MainPID --value)
INTERFACE_PID=$(systemctl show potato-interface.service --property=MainPID --value)
test "$OTHER_USER_PID" -gt 1
test "$INTERFACE_PID" -gt 1
sudo -u "$ORDINARY_USER" test ! -e "/proc/$OTHER_USER_PID"
sudo -u "$ORDINARY_USER" test ! -r "/proc/$OTHER_USER_PID/cmdline"
sudo -u "$ORDINARY_USER" test ! -r "/proc/$OTHER_USER_PID/environ"
sudo -u "$ORDINARY_USER" test ! -r "/proc/$INTERFACE_PID/environ"
sudo -u "$ORDINARY_USER" sh -c 'test -r /proc/self/cmdline'
```

新版每用户 unit 设置私有 `TMPDIR` 和 `UMask=0077`，并在每次启动前以目标用户身份创建/收紧 `0700` 临时目录，
不再把这些敏感运行时文件写入共享 `/tmp`。最后执行
6.7 小节和“验证”章节中的普通用户权限复测，并从当前配置的 public origin 重新登录；正式生产必须使用
HTTPS，旧 Cookie 应全部失效。

### 2. 构建、安装并切换 Lite release

按以下顺序执行，不能跳过中间的 inactive 状态：

1. 按 6.1 至 6.3 小节准备独立 build venv、clean browser assets，并完成 Lite 测试、隔离 verifier 和
   两次确定性构建。
2. 按 6.4 小节准备完整离线 wheelhouse，并用 `install_lite_release.sh` 安装一个新的 inactive release ID。
3. 按 6.6 小节生成全新的 root-owned `CODE_SOURCE`，人工核对 mapped user 数，完成独立数据备份后运行
   `cutover_lite_production.sh`。
4. 按 6.7 小节检查 symlink、模块 origin、服务进程、`pip check`、健康状态、cutover 结果和状态指纹差异。

cutover 脚本会自行记录 active 服务、停止写入方、切换并只恢复原来 active 的服务，不要提前手工停止服务；
需要重建 `/opt/interface-env` 时，按第 11 节在 cutover 成功并完成健康验收后单独执行。
已有 mapped 用户不需要重新创建，也不应为了 runtime 升级运行 `provision-user`；该命令会改写用户配置、
skills 和 unit，范围大于 release 切换。

### 3. 仅刷新每用户 unit 模板

如果 release 和用户 runtime 文件都不变，只需要刷新 unit 模板，先只读检查渲染差异，再显式应用。下面的
数量必须在每次操作时从受保护 mapping 动态读取并人工核对，README 不保存固定值：

```bash
MAPPING=/var/lib/potato-agent/config/users_mapping.yaml
EXPECTED_USER_COUNT=$(
  /opt/interface-env/bin/python -c \
    'import sys,yaml; print(len((yaml.safe_load(open(sys.argv[1])) or {}).get("users") or []))' \
    "$MAPPING"
)
printf 'reviewed mapped users: %s\n' "$EXPECTED_USER_COUNT"

PYTHONPATH=/srv/potato_agent \
POTATO_AGENT_MAPPING_PATH="$MAPPING" \
/opt/interface-env/bin/python /srv/potato_agent/refresh_hermes_systemd_units.py \
  --all --expect-count "$EXPECTED_USER_COUNT" --require-existing-set

PYTHONPATH=/srv/potato_agent \
POTATO_AGENT_MAPPING_PATH="$MAPPING" \
/opt/interface-env/bin/python /srv/potato_agent/refresh_hermes_systemd_units.py \
  --apply --all --expect-count "$EXPECTED_USER_COUNT" --require-existing-set
```

只读检查发现 drift 时返回 `1`，无 drift 返回 `0`；校验或安全边界失败返回 `2`。`--apply` 会验证全部候选、
备份旧 unit、执行原子逐文件替换并 reload systemd，但不会重启服务，也不会读写用户 home、Hermes config、
session、数据库或 skills。应用后只在维护窗口重启需要加载新 unit 且原本在线的服务。

Hermes 0.16.0 gateway 默认使用 180 秒 drain；生成的 unit 应为 `TimeoutStopSec=210`。如果 mapping 中覆盖
`restart_drain_timeout`，则应为覆盖值加 30 秒。

## 日常运维

### 查看日志

```bash
journalctl -u potato-interface.service -f
journalctl -u hermes-alice.service -f
```

### 服务控制

```bash
systemctl restart potato-interface.service
systemctl status potato-interface.service
systemctl status hermes-alice.service
```

### 空闲超时

`INTERFACE_RUNTIME_IDLE_TIMEOUT_SECONDS` 控制用户 runtime 空闲多久后被停止，并撤销网页登录态。
生产值是 `1800` 秒，也就是 30 分钟。测试时可临时改成 `300` 秒，然后重启
`potato-interface.service`。

会话轮询接口本身不会刷新活动时间；用户在 workspace 中触发的聊天、文件等操作才会刷新活动时间。

### 用户管理脚本

```bash
/opt/interface-env/bin/python /srv/potato_agent/provision_interface_user.py USER EMAIL
/opt/interface-env/bin/python /srv/potato_agent/bind_existing_linux_user.py USER EMAIL --linux-user LINUX_USER
/opt/interface-env/bin/python /srv/potato_agent/deprovision_interface_user.py USER
/opt/interface-env/bin/python /srv/potato_agent/unbind_existing_linux_user.py USER
```

创建和绑定命令默认无回显提示密码；自动化使用 `--password-file` 或 `--password-stdin`，不能传递密码位置参数。
只有在确认要删除系统托管 Linux 用户的 home 目录时，才给
`deprovision_interface_user.py` 加 `--delete-home`。

### 修改模型配置

```bash
export POTATO_AGENT_MAPPING_PATH=/var/lib/potato-agent/config/users_mapping.yaml
/opt/interface-env/bin/python /srv/potato_agent/configure_model_proxy.py
```

如果需要立即写入已有用户 runtime，追加 `--apply-to-users`。真实上游 key 只能通过交互式无回显提示、
`--api-key-file` 或 `--api-key-fd` 输入。

## 验证

Interface 代码变更后运行 interface 测试：

```bash
cd /srv/potato_agent
/opt/interface-env/bin/python -m pytest interface/test_*.py
```

Hermes Lite 源码、profile、manifest 或构建脚本变更后，还必须使用 6.1 小节的 `BUILD_PYTHON` 运行
Lite 单测、packaging 测试、mock gateway E2E 和隔离 verifier：

```bash
PYTHONDONTWRITEBYTECODE=1 "$BUILD_PYTHON" -B -m pytest \
  -q -p no:cacheprovider -c /dev/null \
  hermes-lite/tests hermes-lite/tests_packaging
PYTHONDONTWRITEBYTECODE=1 "$BUILD_PYTHON" -B -m pytest \
  -q -p no:cacheprovider -c /dev/null \
  hermes-lite/tests_e2e/test_mock_provider_e2e.py
PYTHONDONTWRITEBYTECODE=1 "$BUILD_PYTHON" -B \
  hermes-lite/scripts/verify_lite.py --python "$BUILD_PYTHON"
```

两个 pytest 目录应在同一调用中收集；测试支持模块不依赖裸 `conftest` 导入。release 构建仍需按 6.3 小节
完成 dry run 和两次 wheel SHA 比较。生产切换后还需完整执行 6.7 小节，不以单元测试代替 runtime origin、
服务进程和受保护状态检查。

检查网页服务：

```bash
INTERFACE_HEALTH_HOST=$(systemctl show potato-interface.service --property=Environment --value |
  tr ' ' '\n' | sed -n 's/^INTERFACE_BIND_HOST=//p')
test -n "$INTERFACE_HEALTH_HOST"
INTERFACE_HTTP_ORIGIN="http://$INTERFACE_HEALTH_HOST:3000"
curl -fsS "$INTERFACE_HTTP_ORIGIN/health" | python3 -m json.tool >/dev/null
curl -fsS "$INTERFACE_HTTP_ORIGIN/lite" >/dev/null
curl -fsS "$INTERFACE_HTTP_ORIGIN/spatial" >/dev/null
curl -fsS "$INTERFACE_HTTP_ORIGIN/api/spatial/datasets" | python3 -m json.tool >/dev/null
curl -fsS "$INTERFACE_HTTP_ORIGIN/wgcna" >/dev/null
curl -fsS "$INTERFACE_HTTP_ORIGIN/api/wgcna/status" | python3 -m json.tool >/dev/null
curl -fsS "$INTERFACE_HTTP_ORIGIN/bulk-rnaseq" >/dev/null
curl -fsS "$INTERFACE_HTTP_ORIGIN/api/bulk-rnaseq/status" | python3 -m json.tool >/dev/null
curl -fsS "$INTERFACE_HTTP_ORIGIN/genes" >/dev/null
curl -fsS "$INTERFACE_HTTP_ORIGIN/api/v1/gene-catalog" | python3 -m json.tool >/dev/null
curl -fsS --get --data-urlencode 'q=DM8' \
  "$INTERFACE_HTTP_ORIGIN/api/v1/genes/search" | python3 -m json.tool >/dev/null
systemctl is-active potato-interface.service
PUBLIC_ORIGIN=https://agent.example.com
# 显式 HTTP profile 使用：http://<server-address>:3000
curl -fsS "$PUBLIC_ORIGIN/health" >/dev/null
```

检查空间转录组、WGCNA、Bulk RNA-Seq 和 Gene Catalog 数据对 interface 服务可读：

```bash
sudo -u potato-interface test -r /srv/spatial_data/current/datasets.json
sudo -u potato-interface test -r /srv/spatial_data/current/data/expression.sqlite
sudo -u potato-interface test -r /srv/spatial_data/current/datasets/s1_stem/expression.sqlite
sudo -u potato-interface test -r /srv/wgcna_data/current/tables/network_genes.tsv
sudo -u potato-interface test -r /srv/wgcna_data/current/tables/coexpression_edges_top.tsv.gz
sudo -u potato-interface test -r /srv/bulk_rnaseq/current/bulk_rnaseq.sqlite
sudo -u potato-interface test -r /srv/gene_catalog/current/gene_catalog.sqlite
```

检查普通 Hermes 用户不能读取源码、Interface/proxy 状态、credentials 或另一个用户的聊天数据库。测试账号
必须是实际 mapped 普通用户，不能使用管理员账号 `jiayuxin`；把两个占位值替换为两个不同用户的实际路径：

```bash
ORDINARY_USER=hmx_alice
OTHER_USER_STATE_DB=/home/hmx_bob/.hermes/state.db
test "$ORDINARY_USER" != jiayuxin
sudo -u "$ORDINARY_USER" test ! -r /srv/potato_agent/interface/app.py
sudo -u "$ORDINARY_USER" test ! -r /var/lib/potato-agent/config/users_mapping.yaml
sudo -u "$ORDINARY_USER" test ! -r /var/lib/potato-agent/config/model_proxy.yaml
sudo -u "$ORDINARY_USER" test ! -r /var/lib/potato-agent/data/interface.db
sudo -u "$ORDINARY_USER" test ! -r /var/lib/potato-agent/data/archive.db
sudo -u "$ORDINARY_USER" test ! -r /var/lib/potato-agent/model-proxy/usage.db
sudo -u "$ORDINARY_USER" test ! -r /etc/potato-agent/credentials/interface-session-secret
sudo -u "$ORDINARY_USER" test ! -r /etc/potato-agent/credentials/resend-api-key
sudo -u "$ORDINARY_USER" test ! -r "$OTHER_USER_STATE_DB"
sudo -u "$ORDINARY_USER" test ! -r /srv/spatial_data/current/data/expression.sqlite
sudo -u "$ORDINARY_USER" test ! -r /srv/wgcna_data/current/tables/coexpression_edges_top.tsv.gz
sudo -u "$ORDINARY_USER" test ! -r /srv/bulk_rnaseq/current/bulk_rnaseq.sqlite
sudo -u "$ORDINARY_USER" test ! -r /srv/gene_catalog/current/gene_catalog.sqlite
sudo -u potato-interface test ! -r /var/lib/potato-agent/config/model_proxy.yaml
sudo -u potato-model-proxy test ! -r /var/lib/potato-agent/data/interface.db
```

从两个普通 Web 账号分别创建一条可识别的测试会话，再确认双方的会话列表、resume、归档状态和 title 修改接口
都看不到或不能修改对方记录。Linux 文件权限检查不能替代这项应用层验收。

最后确认三个真实 secret 均未出现在任何进程 argv。下面的探针只在内存中比较并最多报告 PID，不打印 secret：

```bash
PYTHONPATH=/srv/potato_agent /opt/interface-env/bin/python - <<'PY'
import pathlib
import yaml

from interface.mapping import MappingStore
from interface.user_private_files import read_user_private_text

proxy_path = pathlib.Path("/var/lib/potato-agent/config/model_proxy.yaml")
proxy = yaml.safe_load(proxy_path.read_text(encoding="utf-8")) or {}
secrets = {
    str(model.get("api_key") or "").encode()
    for model in proxy.get("models") or []
    if isinstance(model, dict)
}
for path in (
    pathlib.Path("/etc/potato-agent/credentials/interface-session-secret"),
    pathlib.Path("/etc/potato-agent/credentials/resend-api-key"),
):
    secrets.add(path.read_bytes().strip())
secrets.discard(b"")

mapping_path = pathlib.Path("/var/lib/potato-agent/config/users_mapping.yaml")
candidate_values = [(mapping_path, mapping_path.read_bytes())]
for target in MappingStore(mapping_path).load_targets():
    for candidate in (
        target.hermes_home / "config.yaml",
        target.hermes_home / ".env",
    ):
        value = read_user_private_text(target, candidate)
        if value is not None:
            candidate_values.append((candidate, value.encode("utf-8")))
system_candidates = [
    pathlib.Path("/etc/systemd/system/potato-interface.service")
]
system_candidates.extend(
    pathlib.Path("/etc/systemd/system/potato-interface.service.d").glob("*.conf")
)
leaked_paths = []
for candidate in system_candidates:
    try:
        value = candidate.read_bytes()
    except FileNotFoundError:
        continue
    candidate_values.append((candidate, value))
for candidate, value in candidate_values:
    if any(secret in value for secret in secrets):
        leaked_paths.append(str(candidate))
if leaked_paths:
    raise SystemExit("configured secret found outside its owner: " + ", ".join(leaked_paths))

leaked_pids = []
for cmdline in pathlib.Path("/proc").glob("[0-9]*/cmdline"):
    try:
        value = cmdline.read_bytes()
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        continue
    if any(secret in value for secret in secrets):
        leaked_pids.append(cmdline.parent.name)
if leaked_pids:
    raise SystemExit("secret found in process argv for PID(s): " + ", ".join(leaked_pids))
print("no configured secret found in process argv")
PY
```

检查生成的 Hermes unit hardening：

```bash
systemctl cat hermes-alice.service
```

unit 中应包含：

- `User=<mapped-linux-user>`
- `PrivateTmp=yes`
- `NoNewPrivileges=yes`
- `InaccessiblePaths=-/srv/potato_agent`
- `InaccessiblePaths=-/var/lib/potato-agent`
- `InaccessiblePaths=-/etc/potato-agent`
- `InaccessiblePaths=-/opt/interface-env`

## 运行时状态文件

不要提交或同步这些文件到仓库：

- `/var/lib/potato-agent/config/users_mapping.yaml`
- `/var/lib/potato-agent/config/model_proxy.yaml`
- `/var/lib/potato-agent/data/interface.db`
- `/var/lib/potato-agent/data/archive.db`
- `/var/lib/potato-agent/model-proxy/usage.db`
- 上述 SQLite 对应的 `-wal`、`-shm` 和 `-journal` sidecar
- `/etc/potato-agent/credentials/interface-session-secret`
- `/etc/potato-agent/credentials/resend-api-key`
- `/etc/potato-agent/credentials/daily-updates-model-proxy-token`
- `/etc/potato-agent/credentials/daily-updates-pubmed-api-key`（可选）
- `/srv/daily_updates/data/daily_updates.sqlite` 及其 journal sidecar
- `/srv/daily_updates/legacy/` 中的只读迁移快照

仓库内的 `users_mapping.yaml` 和 `interface/data/*.db` 只属于旧部署位置。当前安全部署应把它们
放在 `/var/lib/potato-agent` 下。
