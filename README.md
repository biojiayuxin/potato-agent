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
- 新 Hermes 源码和构建入口：仓库内的 `hermes-lite/`。
- 当前 immutable 运行时：`/opt/potato-hermes-lite/current`；精确版本和 wheel hash 见下方
  “Hermes Lite 运行时”章节。
- `/opt/hermes-agent-src` 和 `/opt/hermes-agent-venv` 已不参与在线进程，仅在实际用户验收和观察期完成前
  保留作 legacy 回滚来源。
- interface Python 环境：`/opt/interface-env`。

如果确实不能使用 `/var/lib/potato-agent`，可以用 `POTATO_AGENT_STATE_DIR` 指向其它状态根目录；
同时要确保 systemd unit、权限和迁移命令中的路径保持一致。

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
release:      /opt/potato-hermes-lite/releases/20260718T082412Z-0.16.0-potato.lite.4-2ce0fef7
current:      /opt/potato-hermes-lite/current
version:      0.16.0+potato.lite.4
wheel SHA256: 2ce0fef7ea82b95c6b63e8baea2e365a3ceea4171c2c8645b90272ecdb975481
```

11 个 mapped unit 和 Interface gateway Python 均已切到 Lite；初步实际用户验收已通过，目前仍处于观察和
legacy 回滚保留期。完整工具清单、迁移证据和删除门禁见
[`HERMES_SLIMMING_PLAN.md`](HERMES_SLIMMING_PLAN.md)。

## 安全边界

当前安全部署依赖六层边界：

1. `/srv/potato_agent` 只允许 `root` 和 `potato-interface` 组读取，普通 Hermes 用户不能读源码。
2. `/var/lib/potato-agent/data` 由 `potato-interface` 独占，普通 Hermes 用户不能读
   interface 用户数据库和归档数据库。
3. `/srv/spatial_data` 由 `root:potato-interface` 只读维护，空间转录组页面可公开访问，但底层
   SQLite 和轮廓数据不暴露给普通 Linux 用户直接读取。
4. `/srv/wgcna_data` 由 `root:potato-interface` 只读维护，WGCNA 页面可公开访问，但底层导出
   TSV 和 PostgreSQL 写入权限不开放给普通 Linux 用户。
5. `/srv/bulk_rnaseq` 由 `root:potato-interface` 只读维护，Bulk RNA-Seq 页面可公开访问，但底层
   SQLite 不暴露给普通 Linux 用户直接读取。
6. 每用户 Hermes service 以各自 Linux 用户运行，并在 systemd unit 中隐藏
   `/srv/potato_agent`、`/var/lib/potato-agent`、`/etc/potato-agent` 和
   `/opt/interface-env`。

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

chown -R root:potato-interface /srv/spatial_data 2>/dev/null || true
find /srv/spatial_data -type d -exec chmod 0750 {} + 2>/dev/null || true
find /srv/spatial_data -type f -exec chmod 0640 {} + 2>/dev/null || true

chown -R root:potato-interface /srv/wgcna_data 2>/dev/null || true
find /srv/wgcna_data -type d -exec chmod 0750 {} + 2>/dev/null || true
find /srv/wgcna_data -type f -exec chmod 0640 {} + 2>/dev/null || true

chown -R root:potato-interface /srv/bulk_rnaseq 2>/dev/null || true
find /srv/bulk_rnaseq -type d -exec chmod 0750 {} + 2>/dev/null || true
find /srv/bulk_rnaseq -type f -exec chmod 0640 {} + 2>/dev/null || true
```

共享服务器或公网部署如果需要在 Files 面板访问共享数据，应使用
`INTERFACE_FILE_BROWSER_MODE=home_and_public_data`；不需要共享数据时使用 `home_only`。只有在可信
内网机器上，并且你确实希望用户可以浏览 Linux 账号本身有权限读取的任意目录时，才使用
`user_readable`。

## 前置条件

以下命令默认以 root 执行，目标机器需要 Linux 和 systemd。

需要安装：

- Python 3 和 `venv`
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
apt-get install -y python3 python3-venv python3-pip git curl rsync sudo build-essential postgresql postgresql-client
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
  --exclude '.pytest_cache/' \
  --exclude '.ruff_cache/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '*.pyo' \
  --exclude '*.egg-info/' \
  --exclude 'build/' \
  --exclude 'dist/' \
  --exclude 'node_modules/' \
  --exclude '*.db' \
  --exclude '*.sqlite' \
  --exclude '*.sqlite3' \
  --exclude '.env' \
  --exclude 'hermes-agent/' \
  --exclude 'packaging/hermes/' \
  --exclude 'hermes-lite/build/' \
  --exclude 'interface/data/' \
  --exclude 'users_mapping.yaml' \
  ./ /srv/potato_agent/
cd /srv/potato_agent
```

同步结果不得包含 legacy Hermes 或运行时状态：

```bash
test ! -d /srv/potato_agent/hermes-agent
test ! -e /srv/potato_agent/users_mapping.yaml
find /srv/potato_agent -type f \
  \( -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' -o -name '.env' \
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

mkdir -p /var/lib/potato-agent/config /var/lib/potato-agent/data
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
`/opt/potato-hermes-lite/current/venv` 构建下一版。当前服务器在 legacy 观察期内可以临时使用
`/opt/hermes-agent-venv/bin/python3` 作为依赖提供者，但旧源码树绝不能进入 `PYTHONPATH` 或 wheel 输入。
全新机器应建立独立 build venv：

```bash
python3 -m venv /opt/potato-hermes-lite-build-env
BUILD_PYTHON=/opt/potato-hermes-lite-build-env/bin/python3

"$BUILD_PYTHON" -m pip install --upgrade pip
mapfile -t lite_requirements < <(
  "$BUILD_PYTHON" -c \
    'import json; print(*json.load(open("hermes-lite/manifests/direct-dependencies.json"))["requirements"], sep="\n")'
)
"$BUILD_PYTHON" -m pip install \
  setuptools==82.0.1 wheel packaging pytest \
  "${lite_requirements[@]}"
```

在当前过渡服务器上也可以只设置：

```bash
BUILD_PYTHON=/opt/hermes-agent-venv/bin/python3
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

Lite 单测与 packaging 测试有各自的 `conftest.py`，必须分开运行：

```bash
PYTHONDONTWRITEBYTECODE=1 "$BUILD_PYTHON" -B -m pytest \
  -q -p no:cacheprovider -c /dev/null hermes-lite/tests

PYTHONDONTWRITEBYTECODE=1 "$BUILD_PYTHON" -B -m pytest \
  -q -p no:cacheprovider -c /dev/null hermes-lite/tests_packaging

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
install -d -o root -g root -m 0755 "$BUILD_ROOT"

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

installer 全程使用 `--no-index`。wheelhouse 必须针对目标机器的 Python 版本和平台，包含 Lite wheel 的全部
直接及传递依赖：

```bash
WHEELHOUSE=$BUILD_ROOT/wheelhouse
install -d -o root -g root -m 0755 "$WHEELHOUSE"
"$BUILD_PYTHON" -m pip download --only-binary=:all: \
  --dest "$WHEELHOUSE" "$RELEASE_A"/wheel/*.whl
find "$WHEELHOUSE" -maxdepth 1 -type f ! -name '*.whl' -print
```

最后一条命令应无输出。直接依赖已固定，但传递依赖不是长期 lock；应归档并审查本次 wheelhouse，以及安装后
release 内的 `config/installed-distributions.json`。

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
切换前 active 的 Hermes 服务，应安排维护窗口并先完成独立数据备份。状态指纹用于证明切换期间零变化，
不是备份；它不读取用户 workdir，`.hermes/home` 只做 metadata tree 摘要。

不能把 dirty checkout 直接作为 `CODE_SOURCE`。先创建全新的 root-owned staging，排除 legacy 源码、状态、
缓存和生成文件：

```bash
REPO=$PWD
CODE_SOURCE=$BUILD_ROOT/code-source
test ! -e "$CODE_SOURCE"
install -d -o root -g root -m 0755 "$CODE_SOURCE"
rsync -a \
  --exclude '/.git/' \
  --exclude '/.codex-tmp/' \
  --exclude '/.pytest_cache/' \
  --exclude '/.ruff_cache/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '*.pyo' \
  --exclude '*.db' \
  --exclude '*.sqlite' \
  --exclude '*.sqlite3' \
  --exclude '.env' \
  --exclude '/users_mapping.yaml' \
  --exclude '/interface/data/' \
  --exclude '/hermes-agent/' \
  --exclude '/packaging/hermes/' \
  "$REPO/" "$CODE_SOURCE/"

chown -R root:root "$CODE_SOURCE"
chmod -R go-w "$CODE_SOURCE"
test -f "$CODE_SOURCE/interface/app.py"
test ! -e "$CODE_SOURCE/hermes-agent"
test ! -e "$CODE_SOURCE/users_mapping.yaml"
test -z "$(find "$CODE_SOURCE" -type l -print -quit)"
test -z "$(find "$CODE_SOURCE" -type f \
  \( -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' -o \
     -name '.env' -o -name '*.pyc' -o -name '*.pyo' \) -print -quit)"
```

根据当次受保护 mapping 人工核对 mapped user 数。`11` 只是当前生产值，不能机械沿用：

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

- 备份 mapping、全部 mapped unit、旧 `current`/`hermes` target、Interface drop-in 和被覆盖的代码；
- 记录切换前 active 的服务，只停止并最终恢复这些服务，原本 inactive 的 unit 保持 inactive；
- 停止写入方后采集 mapping、Interface data 和 mapped `HERMES_HOME` 指纹；
- overlay 新代码，原子切换 `current` 与 `/usr/local/bin/hermes`，刷新既有 unit 集；
- 再次采集并比较指纹，要求 `added`、`changed`、`removed` 全为空；
- 检查 `/health`、进程使用的新 release、无 `slash_worker`、`pip check` 和 CLI help。

备份写入 root-only 的
`/var/backups/potato-agent/hermes-lite-cutover/<timestamp>-<release-id>`。切换开始后的错误、中断或终止信号会
恢复旧 unit、drop-in、symlink、被覆盖文件和原 active 服务；但 overlay 新增的代码文件不会自动删除，因此
这不是整个代码树的完整原样回滚。成功后的人工回滚也没有独立的一键脚本，必须保留旧 immutable release、
旧源码/venv 和对应 backup，在维护窗口中按 backup 受控执行。

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
curl -fsS http://127.0.0.1:3000/health
HERMES_UNIT=hermes-REPLACE_WITH_MAPPED_USER.service
systemctl status "$HERMES_UNIT" --no-pager

pgrep -af '[s]lash_worker|/opt/[h]ermes-agent|[h]ermes-agent-src'
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

### 10. 安装 interface 运行时

```bash
python3 -m venv /opt/interface-env
/opt/interface-env/bin/pip install --upgrade pip
/opt/interface-env/bin/pip install -r /srv/potato_agent/interface/requirements.txt
```

### 11. 配置本地模型代理

上游模型网关需要兼容 OpenAI API。真实上游 API key 只写入 root-owned
`/var/lib/potato-agent/config/model_proxy.yaml`；每个用户的 Hermes 配置只会包含
`http://127.0.0.1:8765/v1` 和 `{username}-local-token`。

```bash
export POTATO_AGENT_MAPPING_PATH=/var/lib/potato-agent/config/users_mapping.yaml
export POTATO_MODEL_PROXY_CONFIG_PATH=/var/lib/potato-agent/config/model_proxy.yaml
```

交互式配置：

```bash
/opt/interface-env/bin/python /srv/potato_agent/configure_hermes_model.py
```

非交互式示例：

```bash
/opt/interface-env/bin/python /srv/potato_agent/configure_hermes_model.py \
  --base-url https://model-gateway.example/v1 \
  --model gpt-5.4 \
  --api-key 'replace-with-upstream-api-key'
```

可选 fallback provider 会写入 proxy 配置，但不会写入用户目录：

```bash
/opt/interface-env/bin/python /srv/potato_agent/configure_hermes_model.py \
  --base-url https://model-gateway.example/v1 \
  --model gpt-5.4 \
  --api-key 'replace-with-upstream-api-key' \
  --fallback-base-url https://fallback-gateway.example/v1 \
  --fallback-model gpt-5.4-mini \
  --fallback-api-key 'replace-with-fallback-api-key'
```

安装并启动本地 proxy service：

```bash
install -D -m 0644 /srv/potato_agent/packaging/systemd/potato-model-proxy.service \
  /etc/systemd/system/potato-model-proxy.service
chown root:potato-interface /var/lib/potato-agent/config/model_proxy.yaml
chmod 0640 /var/lib/potato-agent/config/model_proxy.yaml
systemctl daemon-reload
systemctl enable --now potato-model-proxy.service
```

如果要把新的 proxy 配置立即下发到已有用户：

```bash
/opt/interface-env/bin/python /srv/potato_agent/configure_hermes_model.py \
  --base-url https://model-gateway.example/v1 \
  --model gpt-5.4 \
  --api-key 'replace-with-upstream-api-key' \
  --apply-to-users
```

`--apply-to-users` 会先打印摘要，并要求手动输入 `APPLY`，然后才会重写已有用户的 Hermes
配置并重启当前正在运行的 Hermes service。升级旧部署后可以运行
`cleanup_hermes_user_keys.py --dry-run` 检查历史 key，再去掉 `--dry-run` 执行清理。

### 12. 安装 privileged helper

```bash
mkdir -p /usr/local/libexec
cat >/usr/local/libexec/potato-agent-privileged-helper <<'EOF'
#!/bin/sh
export PYTHONPATH=/srv/potato_agent${PYTHONPATH:+:$PYTHONPATH}
exec /opt/interface-env/bin/python -m interface.privileged_helper "$@"
EOF

chown root:root /usr/local/libexec/potato-agent-privileged-helper
chmod 0755 /usr/local/libexec/potato-agent-privileged-helper

cat >/etc/sudoers.d/potato-agent-interface <<'EOF'
potato-interface ALL=(root) NOPASSWD: /usr/local/libexec/potato-agent-privileged-helper *
EOF

chown root:root /etc/sudoers.d/potato-agent-interface
chmod 0440 /etc/sudoers.d/potato-agent-interface
visudo -cf /etc/sudoers.d/potato-agent-interface
```

helper 只暴露 `interface.privileged_helper` 中实现的固定命令集。

### 13. 安装 systemd service

生成固定的 session secret：

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

写入 `/etc/systemd/system/potato-interface.service`：

```ini
[Unit]
Description=Potato Agent Interface
After=network.target

[Service]
Type=simple
User=potato-interface
Group=potato-interface
WorkingDirectory=/srv/potato_agent
Environment=INTERFACE_SESSION_SECRET=replace-with-long-random-string
Environment=INTERFACE_FILE_BROWSER_MODE=home_and_public_data
Environment=INTERFACE_RUNTIME_IDLE_TIMEOUT_SECONDS=1800
Environment=POTATO_AGENT_MAPPING_PATH=/var/lib/potato-agent/config/users_mapping.yaml
Environment=INTERFACE_AUTH_DB=/var/lib/potato-agent/data/interface.db
Environment=INTERFACE_ARCHIVE_DB=/var/lib/potato-agent/data/archive.db
Environment=INTERFACE_PRIVILEGED_HELPER=/usr/local/libexec/potato-agent-privileged-helper
Environment=INTERFACE_TUI_GATEWAY_PYTHON=/opt/potato-hermes-lite/current/venv/bin/python3
Environment=SPATIAL_VIEWER_DATA_ROOT=/srv/spatial_data/current
Environment=WGCNA_DATABASE_URL=postgresql:///potato_wgcna?host=/var/run/postgresql
Environment=BULK_RNASEQ_DB_PATH=/srv/bulk_rnaseq/current/bulk_rnaseq.sqlite
Environment=INTERFACE_RESEND_API_KEY=replace-with-resend-api-key
Environment="INTERFACE_MAIL_FROM=Potato Agent <noreply@mail.example.com>"
ExecStart=/opt/interface-env/bin/python -m uvicorn interface.app:app --host 0.0.0.0 --port 3000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

会话归档由 `potato-interface.service` 进程内的后台任务执行，默认每天 03:00 归档超过 7 天未活跃的
interface 会话。需要保留更久或实际关闭归档时，设置 `INTERFACE_ARCHIVE_RETENTION_DAYS`；例如当前
生产部署用 365000 天等效关闭自动归档：

```bash
install -d -o root -g root -m 0755 /etc/systemd/system/potato-interface.service.d
cat >/etc/systemd/system/potato-interface.service.d/30-archive-retention.conf <<'EOF'
[Service]
Environment=INTERFACE_ARCHIVE_RETENTION_DAYS=365000
EOF
chown root:root /etc/systemd/system/potato-interface.service.d/30-archive-retention.conf
chmod 0644 /etc/systemd/system/potato-interface.service.d/30-archive-retention.conf
systemctl daemon-reload
systemctl restart potato-interface.service
```

需要恢复 7 天归档时，把该值改回 `7` 后重载并重启服务。不要设为 `0`；当前实现中 `0` 会让几乎所有
旧会话都满足归档条件。

注册流程会通过 Resend HTTPS API 发送邮箱验证码，不使用 SMTP 端口。部署前需要在 Resend 中完成
发件域名验证，并创建 API key。`INTERFACE_MAIL_FROM` 必须使用已验证域名下的地址；如果验证的是
`mail.example.com`，发件地址应类似 `noreply@mail.example.com`。可选设置
`INTERFACE_MAIL_REPLY_TO`：

```ini
Environment=INTERFACE_RESEND_API_KEY=replace-with-resend-api-key
Environment="INTERFACE_MAIL_FROM=Potato Agent <noreply@mail.example.com>"
Environment=INTERFACE_MAIL_REPLY_TO=support@example.com
```

如果是升级已有部署，也可以用 drop-in 单独写入 Resend 配置，避免改动主 unit：

```bash
install -d -o root -g root -m 0755 /etc/systemd/system/potato-interface.service.d
cat >/etc/systemd/system/potato-interface.service.d/20-resend-env.conf <<'EOF'
[Service]
Environment=INTERFACE_RESEND_API_KEY=replace-with-resend-api-key
Environment="INTERFACE_MAIL_FROM=Potato Agent <noreply@mail.example.com>"
EOF
chown root:root /etc/systemd/system/potato-interface.service.d/20-resend-env.conf
chmod 0640 /etc/systemd/system/potato-interface.service.d/20-resend-env.conf
systemctl daemon-reload
systemctl restart potato-interface.service
```

未配置 Resend key 或发件地址时，注册发送验证码接口会返回 503。新版本启动时会自动创建邮箱验证
所需的 SQLite 表和列，不需要手工迁移数据库。

启用服务：

```bash
systemctl daemon-reload
systemctl enable --now potato-interface.service
systemctl status potato-interface.service
```

访问地址：

```text
http://<server>:3000/lite
```

### 14. 创建用户

创建系统托管的 Linux 用户和网页账号：

```bash
/opt/interface-env/bin/python /srv/potato_agent/provision_interface_user.py \
  alice alice@example.com 'replace-with-login-password'
```

把已有 Linux 用户绑定为网页账号：

```bash
/opt/interface-env/bin/python /srv/potato_agent/bind_existing_linux_user.py \
  alice alice@example.com 'replace-with-login-password' \
  --linux-user alice
```

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

### 1. 构建、安装并切换 Lite release

按以下顺序执行，不能跳过中间的 inactive 状态：

1. 按 6.1 至 6.3 小节准备独立 build venv、clean browser assets，并完成 Lite 测试、隔离 verifier 和
   两次确定性构建。
2. 按 6.4 小节准备完整离线 wheelhouse，并用 `install_lite_release.sh` 安装一个新的 inactive release ID。
3. 按 6.6 小节生成全新的 root-owned `CODE_SOURCE`，人工核对 mapped user 数，完成独立数据备份后运行
   `cutover_lite_production.sh`。
4. 按 6.7 小节检查 symlink、模块 origin、服务进程、`pip check`、健康状态、cutover 结果和状态指纹差异。

cutover 脚本会自行记录 active 服务、停止写入方、切换并只恢复原来 active 的服务，不要提前手工停止服务。
已有 mapped 用户不需要重新创建，也不应为了 runtime 升级运行 `provision-user`；该命令会改写用户配置、
skills 和 unit，范围大于 release 切换。

### 2. 仅刷新每用户 unit 模板

如果 release 和用户 runtime 文件都不变，只需要刷新 unit 模板，先只读检查渲染差异，再显式应用。下面的
数量占位符必须替换为当次从受保护 mapping 读取并人工核对的值：

```bash
EXPECTED_USER_COUNT='REPLACE_WITH_REVIEWED_COUNT'

PYTHONPATH=/srv/potato_agent \
POTATO_AGENT_MAPPING_PATH=/var/lib/potato-agent/config/users_mapping.yaml \
/opt/interface-env/bin/python /srv/potato_agent/refresh_hermes_systemd_units.py \
  --all --expect-count "$EXPECTED_USER_COUNT" --require-existing-set

PYTHONPATH=/srv/potato_agent \
POTATO_AGENT_MAPPING_PATH=/var/lib/potato-agent/config/users_mapping.yaml \
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
/opt/interface-env/bin/python /srv/potato_agent/provision_interface_user.py USER EMAIL PASSWORD
/opt/interface-env/bin/python /srv/potato_agent/bind_existing_linux_user.py USER EMAIL PASSWORD --linux-user LINUX_USER
/opt/interface-env/bin/python /srv/potato_agent/deprovision_interface_user.py USER
/opt/interface-env/bin/python /srv/potato_agent/unbind_existing_linux_user.py USER
```

只有在确认要删除系统托管 Linux 用户的 home 目录时，才给
`deprovision_interface_user.py` 加 `--delete-home`。

### 修改模型配置

```bash
export POTATO_AGENT_MAPPING_PATH=/var/lib/potato-agent/config/users_mapping.yaml
/opt/interface-env/bin/python /srv/potato_agent/configure_hermes_model.py
```

如果需要立即写入已有用户 runtime，追加 `--apply-to-users`。

## 验证

Interface 代码变更后运行 interface 测试：

```bash
cd /srv/potato_agent
/opt/interface-env/bin/python -m pytest interface/test_*.py
```

Hermes Lite 源码、profile、manifest 或构建脚本变更后，还必须使用 6.1 小节的 `BUILD_PYTHON` 分开运行
Lite 单测、packaging 测试、mock gateway E2E 和隔离 verifier：

```bash
PYTHONDONTWRITEBYTECODE=1 "$BUILD_PYTHON" -B -m pytest \
  -q -p no:cacheprovider -c /dev/null hermes-lite/tests
PYTHONDONTWRITEBYTECODE=1 "$BUILD_PYTHON" -B -m pytest \
  -q -p no:cacheprovider -c /dev/null hermes-lite/tests_packaging
PYTHONDONTWRITEBYTECODE=1 "$BUILD_PYTHON" -B -m pytest \
  -q -p no:cacheprovider -c /dev/null \
  hermes-lite/tests_e2e/test_mock_provider_e2e.py
PYTHONDONTWRITEBYTECODE=1 "$BUILD_PYTHON" -B \
  hermes-lite/scripts/verify_lite.py --python "$BUILD_PYTHON"
```

不要把两个 pytest 目录合并成一次调用；它们使用不同的 `conftest.py` 边界。release 构建仍需按 6.3 小节
完成 dry run 和两次 wheel SHA 比较。生产切换后还需完整执行 6.7 小节，不以单元测试代替 runtime origin、
服务进程和受保护状态检查。

检查网页服务：

```bash
curl -fsS http://127.0.0.1:3000/health | python3 -m json.tool >/dev/null
curl -fsS http://127.0.0.1:3000/lite >/dev/null
curl -fsS http://127.0.0.1:3000/spatial >/dev/null
curl -fsS http://127.0.0.1:3000/api/spatial/datasets | python3 -m json.tool >/dev/null
curl -fsS http://127.0.0.1:3000/wgcna >/dev/null
curl -fsS http://127.0.0.1:3000/api/wgcna/status | python3 -m json.tool >/dev/null
curl -fsS http://127.0.0.1:3000/bulk-rnaseq >/dev/null
curl -fsS http://127.0.0.1:3000/api/bulk-rnaseq/status | python3 -m json.tool >/dev/null
systemctl is-active potato-interface.service
```

检查空间转录组、WGCNA 和 Bulk RNA-Seq 数据对 interface 服务可读：

```bash
sudo -u potato-interface test -r /srv/spatial_data/current/datasets.json
sudo -u potato-interface test -r /srv/spatial_data/current/data/expression.sqlite
sudo -u potato-interface test -r /srv/spatial_data/current/datasets/s1_stem/expression.sqlite
sudo -u potato-interface test -r /srv/wgcna_data/current/tables/network_genes.tsv
sudo -u potato-interface test -r /srv/wgcna_data/current/tables/coexpression_edges_top.tsv.gz
sudo -u potato-interface test -r /srv/bulk_rnaseq/current/bulk_rnaseq.sqlite
```

检查普通 Hermes 用户不能读取源码和 interface 状态。把 `hmx_user_test` 换成实际 mapped Linux
用户：

```bash
sudo -u hmx_user_test test ! -r /srv/potato_agent/interface/app.py
sudo -u hmx_user_test test ! -r /var/lib/potato-agent/config/users_mapping.yaml
sudo -u hmx_user_test test ! -r /var/lib/potato-agent/data/interface.db
sudo -u hmx_user_test test ! -r /var/lib/potato-agent/data/archive.db
sudo -u hmx_user_test test ! -r /srv/spatial_data/current/data/expression.sqlite
sudo -u hmx_user_test test ! -r /srv/wgcna_data/current/tables/coexpression_edges_top.tsv.gz
sudo -u hmx_user_test test ! -r /srv/bulk_rnaseq/current/bulk_rnaseq.sqlite
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
- `/var/lib/potato-agent/data/interface.db`
- `/var/lib/potato-agent/data/archive.db`
- `/var/lib/potato-agent/data/interface.db-wal`
- `/var/lib/potato-agent/data/interface.db-shm`
- `/var/lib/potato-agent/data/archive.db-wal`
- `/var/lib/potato-agent/data/archive.db-shm`

仓库内的 `users_mapping.yaml` 和 `interface/data/*.db` 只属于旧部署位置。当前安全部署应把它们
放在 `/var/lib/potato-agent` 下。
