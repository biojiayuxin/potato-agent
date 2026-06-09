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
- Hermes 源码安装目录：`/opt/hermes-agent-src`。
- Hermes Python 环境：`/opt/hermes-agent-venv`。
- interface Python 环境：`/opt/interface-env`。

如果确实不能使用 `/var/lib/potato-agent`，可以用 `POTATO_AGENT_STATE_DIR` 指向其它状态根目录；
同时要确保 systemd unit、权限和迁移命令中的路径保持一致。

interface 进程不应该以 root 运行。需要 root 的动作由 privileged helper 完成，包括创建用户、
安装或启停每用户 Hermes service、读取每用户 Hermes session 数据库、按目标 Linux 用户权限
处理文件浏览和下载上传。

## 安全边界

当前安全部署依赖三层边界：

1. `/srv/potato_agent` 只允许 `root` 和 `potato-interface` 组读取，普通 Hermes 用户不能读源码。
2. `/var/lib/potato-agent/data` 由 `potato-interface` 独占，普通 Hermes 用户不能读
   interface 用户数据库和归档数据库。
3. 每用户 Hermes service 以各自 Linux 用户运行，并在 systemd unit 中隐藏
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
```

共享服务器或公网部署应使用 `INTERFACE_FILE_BROWSER_MODE=home_only`。只有在可信内网机器上，
并且你确实希望用户可以浏览 Linux 账号本身有权限读取的任意目录时，才使用 `user_readable`。

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
- 系统默认 Python 可直接导入 GO/KEGG 分析依赖
- Hermes Python 依赖需要的编译工具

Debian 或 Ubuntu 可先安装基础包：

```bash
apt-get update
apt-get install -y python3 python3-venv python3-pip git curl rsync sudo build-essential
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

```bash
mkdir -p /srv/potato_agent
rsync -a --delete \
  --exclude '.git/' \
  --exclude '__pycache__/' \
  --exclude 'interface/data/' \
  --exclude 'users_mapping.yaml' \
  ./ /srv/potato_agent/
cd /srv/potato_agent
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
`python3`。新部署不能只把这些包安装到 `/opt/interface-env` 或 `/opt/hermes-agent-venv`，必须保证
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

### 6. 安装 Hermes 运行时

```bash
mkdir -p /opt/hermes-agent-src
rsync -a --delete /srv/potato_agent/hermes-agent/ /opt/hermes-agent-src/

python3 -m venv /opt/hermes-agent-venv
/opt/hermes-agent-venv/bin/pip install --upgrade pip
/opt/hermes-agent-venv/bin/pip install -e "/opt/hermes-agent-src[all]"

ln -sf /opt/hermes-agent-venv/bin/hermes /usr/local/bin/hermes
/usr/local/bin/hermes --version
```

每用户 Hermes service 默认使用 `/usr/local/bin/hermes`。

Hermes 0.16.0 的 gateway 重启流程默认会等待 `agent.restart_drain_timeout=180` 秒完成
drain。Potato Agent 生成的每用户 systemd unit 默认写入 `TimeoutStopSec=210`，也就是
`restart_drain_timeout + 30` 秒。若在全局 `hermes.config_overrides.agent.restart_drain_timeout`
或用户 `config_overrides.agent.restart_drain_timeout` 中覆盖该值，生成的 unit 会按覆盖值加
30 秒计算；只有显式设置 `hermes.service.timeout_stop_sec` 时才使用手写值。

### 7. 安装 interface 运行时

```bash
python3 -m venv /opt/interface-env
/opt/interface-env/bin/pip install --upgrade pip
/opt/interface-env/bin/pip install -r /srv/potato_agent/interface/requirements.txt
```

### 8. 配置本地模型代理

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

### 9. 安装 privileged helper

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

### 10. 安装 systemd service

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
Environment=INTERFACE_FILE_BROWSER_MODE=home_only
Environment=INTERFACE_RUNTIME_IDLE_TIMEOUT_SECONDS=1800
Environment=POTATO_AGENT_MAPPING_PATH=/var/lib/potato-agent/config/users_mapping.yaml
Environment=INTERFACE_AUTH_DB=/var/lib/potato-agent/data/interface.db
Environment=INTERFACE_ARCHIVE_DB=/var/lib/potato-agent/data/archive.db
Environment=INTERFACE_PRIVILEGED_HELPER=/usr/local/libexec/potato-agent-privileged-helper
Environment=INTERFACE_TUI_GATEWAY_PYTHON=/opt/hermes-agent-venv/bin/python3
Environment=INTERFACE_RESEND_API_KEY=replace-with-resend-api-key
Environment="INTERFACE_MAIL_FROM=Potato Agent <noreply@mail.example.com>"
ExecStart=/opt/interface-env/bin/python -m uvicorn interface.app:app --host 0.0.0.0 --port 3000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

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

### 11. 创建用户

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

## 从旧部署升级

旧部署如果把 `users_mapping.yaml` 或 `interface/data/*.db` 放在仓库目录下，按下面流程迁移。

### 1. 停止服务

```bash
systemctl stop potato-interface.service 2>/dev/null || true
systemctl list-units 'hermes-*.service' --no-legend --plain \
  | awk '{print $1}' \
  | xargs -r systemctl stop
```

### 2. 备份旧状态

```bash
backup_dir=/root/potato-agent-backup-$(date +%Y%m%d-%H%M%S)
mkdir -p "$backup_dir"

cp -a /srv/potato_agent/users_mapping.yaml "$backup_dir/" 2>/dev/null || true
cp -a /srv/potato_agent/interface/data "$backup_dir/interface-data" 2>/dev/null || true
cp -a /etc/systemd/system/potato-interface.service "$backup_dir/" 2>/dev/null || true
```

### 3. 把状态文件迁移到 `/var/lib/potato-agent`

```bash
mkdir -p /var/lib/potato-agent/config /var/lib/potato-agent/data

if [ -f /srv/potato_agent/users_mapping.yaml ]; then
  cp -a /srv/potato_agent/users_mapping.yaml /var/lib/potato-agent/config/users_mapping.yaml
fi

if [ ! -s /var/lib/potato-agent/config/users_mapping.yaml ]; then
  printf 'users: []\n' >/var/lib/potato-agent/config/users_mapping.yaml
fi

if [ -f /srv/potato_agent/interface/data/interface.db ]; then
  cp -a /srv/potato_agent/interface/data/interface.db /var/lib/potato-agent/data/interface.db
fi

if [ -f /srv/potato_agent/interface/data/archive.db ]; then
  cp -a /srv/potato_agent/interface/data/archive.db /var/lib/potato-agent/data/archive.db
fi

chown root:potato-interface /var/lib/potato-agent /var/lib/potato-agent/config
chown root:potato-interface /var/lib/potato-agent/config/users_mapping.yaml
chown potato-interface:potato-interface /var/lib/potato-agent/data
chown potato-interface:potato-interface /var/lib/potato-agent/data/*.db 2>/dev/null || true

chmod 0750 /var/lib/potato-agent /var/lib/potato-agent/config
chmod 0700 /var/lib/potato-agent/data
chmod 0640 /var/lib/potato-agent/config/users_mapping.yaml
chmod 0600 /var/lib/potato-agent/data/*.db 2>/dev/null || true
```

确认新部署正常后，删除仓库目录下的旧状态文件，避免以后同步代码时再次暴露。

### 4. 重新安装运行时和服务

重复全新部署中的这些步骤：

- 同步代码到 `/srv/potato_agent`
- 安装 micromamba
- 安装系统 Python GO/KEGG 分析依赖
- 安装 sgRNA Design 共享依赖
- 安装 Hermes 运行时
- 安装 interface 运行时
- 安装 privileged helper
- 安装 `potato-interface.service`

然后重载 systemd 并重启 interface：

```bash
systemctl daemon-reload
systemctl restart potato-interface.service
```

如果 Hermes 运行时或每用户 systemd unit 模板发生变化，正在运行的 Hermes gateway 也需要在合适
窗口重启：

```bash
systemctl restart hermes-alice.service
```

已有映射用户不需要重新创建。如果允许重写该用户的 Hermes runtime 文件和 unit，可以对每个 mapped
username 执行：

```bash
/usr/local/libexec/potato-agent-privileged-helper provision-user --username alice
```

`provision-user` 会按当前 mapping 重写该用户的 `.hermes/config.yaml`、`.hermes/.env`、技能文件和
systemd unit；如果只需要刷新 unit 模板，不要用它。只重写每用户 systemd unit 时，用仓库代码生成
unit，然后再重启需要继续在线的 Hermes service：

```bash
PYTHONPATH=/srv/potato_agent \
POTATO_AGENT_MAPPING_PATH=/var/lib/potato-agent/config/users_mapping.yaml \
/opt/interface-env/bin/python - <<'PY'
from pathlib import Path

from interface.hermes_service import build_systemd_unit
from interface.mapping import DEFAULT_MAPPING_PATH, MappingStore, load_mapping

config = load_mapping(DEFAULT_MAPPING_PATH, resolve_env=True)
for target in MappingStore(DEFAULT_MAPPING_PATH).load_targets():
    unit_path = Path("/etc/systemd/system") / target.systemd_service
    unit_path.write_text(build_systemd_unit(config, target), encoding="utf-8")
    unit_path.chmod(0o644)
    print(unit_path)
PY
systemctl daemon-reload
systemctl restart hermes-alice.service
```

升级到 Hermes 0.16.0 时需要刷新每用户 unit，确保 gateway unit 至少有
`TimeoutStopSec=restart_drain_timeout + 30`；默认部署应显示为 `TimeoutStopSec=210`。

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

代码变更后运行 interface 测试：

```bash
cd /srv/potato_agent
/opt/interface-env/bin/python -m pytest interface/test_*.py
```

检查网页服务：

```bash
curl -fsS http://127.0.0.1:3000/lite >/dev/null
systemctl is-active potato-interface.service
```

检查普通 Hermes 用户不能读取源码和 interface 状态。把 `hmx_user_test` 换成实际 mapped Linux
用户：

```bash
sudo -u hmx_user_test test ! -r /srv/potato_agent/interface/app.py
sudo -u hmx_user_test test ! -r /var/lib/potato-agent/config/users_mapping.yaml
sudo -u hmx_user_test test ! -r /var/lib/potato-agent/data/interface.db
sudo -u hmx_user_test test ! -r /var/lib/potato-agent/data/archive.db
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
