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
- `rsync`
- `sudo`
- `systemctl`
- `micromamba`，用于生信技能按用户隔离安装工具环境
- Hermes Python 依赖需要的编译工具

Debian 或 Ubuntu 可先安装基础包：

```bash
apt-get update
apt-get install -y python3 python3-venv python3-pip git rsync sudo build-essential
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

### 4. 安装 Hermes 运行时

```bash
mkdir -p /opt/hermes-agent-src
rsync -a --delete /srv/potato_agent/hermes-agent/ /opt/hermes-agent-src/

python3 -m venv /opt/hermes-agent-venv
/opt/hermes-agent-venv/bin/pip install --upgrade pip
/opt/hermes-agent-venv/bin/pip install -e "/opt/hermes-agent-src[all]"

ln -sf /opt/hermes-agent-venv/bin/hermes /usr/local/bin/hermes
/usr/local/bin/hermes --help >/dev/null
```

每用户 Hermes service 默认使用 `/usr/local/bin/hermes`。

### 5. 安装 interface 运行时

```bash
python3 -m venv /opt/interface-env
/opt/interface-env/bin/pip install --upgrade pip
/opt/interface-env/bin/pip install -r /srv/potato_agent/interface/requirements.txt
```

### 6. 配置本地模型代理

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

### 7. 安装 privileged helper

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

### 8. 安装 systemd service

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
ExecStart=/opt/interface-env/bin/python -m uvicorn interface.app:app --host 0.0.0.0 --port 3000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

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

### 9. 创建用户

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
systemd unit。Hermes service 默认保持 disabled，用户进入 workspace 时再按需启动。

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
- 安装 Hermes 运行时
- 安装 interface 运行时
- 安装 privileged helper
- 安装 `potato-interface.service`

然后重启 interface：

```bash
systemctl daemon-reload
systemctl restart potato-interface.service
```

已有映射用户不需要重新创建。如果每用户 systemd unit 模板发生变化，可以对每个 mapped username
重新安装 runtime 文件和 unit：

```bash
/usr/local/libexec/potato-agent-privileged-helper provision-user --username alice
```

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
