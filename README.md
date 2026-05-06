# Potato Agent

`potato_agent` 是一个面向多用户场景的 Hermes 网页入口仓库。它提供统一的网页入口，但每个网页用户都会绑定到独立的 Linux 用户和独立的 Hermes systemd 服务，因此聊天状态、文件访问、工作目录和执行权限都是按用户隔离的。

## Quick Start

这是一套最短可执行的部署流程。

约定：

- 仓库部署在 `/srv/potato_agent`
- 下面所有命令都在仓库根目录执行
- 默认以 `root` 身份执行，便于创建 Linux 用户、写入 `/etc/systemd/system`，并调用 `systemctl`
- 目标机器已经具备：Linux、systemd、Python 3、Python `venv` 模块、`rsync`
- 上游模型网关是 OpenAI-compatible 接口

如果你还没有把仓库放到目标目录，可以先执行：

```bash
mkdir -p /srv/potato_agent
rsync -a ./ /srv/potato_agent/
cd /srv/potato_agent
```

### 1. 安装 Hermes 运行时

```bash
mkdir -p /opt/hermes-agent-src
rsync -a --delete ./hermes-agent/ /opt/hermes-agent-src/

python3 -m venv /opt/hermes-agent-venv
/opt/hermes-agent-venv/bin/pip install -e "/opt/hermes-agent-src[all]"
ln -sf /opt/hermes-agent-venv/bin/hermes /usr/local/bin/hermes

/usr/local/bin/hermes --help
```

这一步完成后，后面的每用户 Hermes service 会默认使用 `/usr/local/bin/hermes`。

### 2. 创建 interface 运行环境

```bash
python3 -m venv /opt/interface-env
/opt/interface-env/bin/pip install -r ./interface/requirements.txt
```

后面的模型配置脚本和用户管理脚本，也统一使用这个虚拟环境来执行。

### 3. 配置上游模型并生成 `users_mapping.yaml`

交互式方式：

```bash
/opt/interface-env/bin/python ./configure_hermes_model.py
```

脚本会提示你输入：

- 上游模型 `base_url`
- 默认模型名称
- 上游 `API_KEY`

如果当前没有 `users_mapping.yaml`，脚本会自动创建一个空的 `users: []` 文件。
如果当前已经有 `users_mapping.yaml`，脚本只会更新模型配置，不会改动已有用户信息。历史的
`hermes.fallback_model` list 会在写回时迁移为 Hermes 标准的
`hermes.fallback_providers`。

非交互式示例：

```bash
/opt/interface-env/bin/python ./configure_hermes_model.py \
  --base-url https://your-upstream-model-gateway.example/v1 \
  --model gpt-5.4 \
  --api-key 'sk-...'
```

配置 fallback provider：

```bash
/opt/interface-env/bin/python ./configure_hermes_model.py \
  --base-url https://your-upstream-model-gateway.example/v1 \
  --model gpt-5.4 \
  --api-key 'sk-...' \
  --fallback-base-url https://your-fallback-gateway.example/v1 \
  --fallback-model gpt-5.4-mini \
  --fallback-api-key 'sk-fallback-...'
```

如果你后面更换上游模型，并希望把新配置立即下发到当前已存在的用户 Hermes 实例：

```bash
/opt/interface-env/bin/python ./configure_hermes_model.py \
  --base-url https://your-upstream-model-gateway.example/v1 \
  --model gpt-5.4 \
  --api-key 'sk-...' \
  --apply-to-users
```

这个参数会：

- 遍历 `users_mapping.yaml` 中的现有用户
- 重写每个用户的 `~/.hermes/config.yaml` 和 `~/.hermes/.env`
- 重启对应 Hermes systemd 服务

执行前脚本会显示变更摘要、列出受影响用户，并要求你手动输入 `APPLY` 做二次确认。

### 4. 创建第一个可登录用户

如果服务器上还没有对应 Linux 用户：

```bash
/opt/interface-env/bin/python ./provision_interface_user.py alice alice@example.com webpass123
```

如果服务器上已经有要复用的 Linux 用户：

```bash
/opt/interface-env/bin/python ./bind_existing_linux_user.py \
  alice \
  alice@example.com \
  webpass123 \
  --linux-user alice
```

这一步会直接完成：

- 更新 `users_mapping.yaml`
- 创建或绑定 Linux 用户
- 安装该用户自己的 Hermes service（默认保持 disabled，用户首次登录工作台时再自动启动）
- 创建网页登录账号

### 5. 先前台确认 interface 能正常启动

这一段只建议用于一次性验收。正式部署时，推荐切到 systemd unit 方式启动 `interface`。

```bash
/opt/interface-env/bin/python -m uvicorn interface.app:app --host 0.0.0.0 --port 3000
```

访问：

```text
http://<host>:3000/lite
```

此时应该已经可以用上一步创建的账号登录。

### 6. 再改成 systemd 常驻服务

当前架构下，`interface` 建议由 root 的 systemd 服务启动。原因是：

- 需要读取各用户 `700` 权限的 home、`work` 和 `.hermes/state.db`
- 注册或自动开通用户时，需要创建 Linux 用户并管理 systemd 服务

先生成一个固定的 `INTERFACE_SESSION_SECRET`：

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

把下面内容写到 `/etc/systemd/system/potato-interface.service`：

```ini
[Unit]
Description=Potato Agent Interface
After=network.target

[Service]
Type=simple
WorkingDirectory=/srv/potato_agent
Environment=INTERFACE_SESSION_SECRET=replace-with-a-long-random-string
Environment=INTERFACE_FILE_BROWSER_MODE=user_readable
ExecStart=/opt/interface-env/bin/python -m uvicorn interface.app:app --host 0.0.0.0 --port 3000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

注意：

- `WorkingDirectory` 必须改成你实际部署这个仓库的目录
- `INTERFACE_SESSION_SECRET` 必须固定；如果不固定，`interface` 每次重启都会让现有登录态失效
- `INTERFACE_TUI_GATEWAY_PYTHON` 默认是 `/opt/hermes-agent-venv/bin/python3`；如果你的 Hermes 虚拟环境装在别处，需要把这个环境变量改成对应的 Python 路径
- `INTERFACE_RUNTIME_IDLE_TIMEOUT_SECONDS` 默认是 1800；测试时可以临时调小，例如 300 表示 5 分钟
- 聊天主链路通过 `tui_gateway`，不再依赖浏览器侧 `api_server` 回退
- 正式运行推荐使用 systemd unit；前台 `uvicorn` 更适合一次性验证和排障
- `INTERFACE_FILE_BROWSER_MODE` 控制 Files 面板里是否允许用户输入目录并打开：
  - `home_only`：默认值。用户只能浏览 `~/`，不显示目录输入框。适合公有云/共享服务器。
  - `user_readable`：显示目录输入框，允许用户打开任意当前 Linux 用户有读取权限的目录。适合 HPC/内网机器。
- 修改 `INTERFACE_FILE_BROWSER_MODE` 后，需要重启 `interface`

然后启用服务：

```bash
systemctl daemon-reload
systemctl enable --now potato-interface.service
systemctl status potato-interface.service
```

### 7. 本地部署状态文件不要同步到 Git

下面这些文件属于本机部署状态，不应该同步到 Git：

- `users_mapping.yaml`
- `interface/data/interface.db`
- `interface/data/archive.db`

其中 `users_mapping.yaml` 里可能直接包含上游模型 `API_KEY`。

## 根目录 Python 脚本

- `configure_hermes_model.py`
  创建或更新 `users_mapping.yaml` 中的 Hermes 模型配置。支持 `--fallback-base-url`、`--fallback-model`、`--fallback-api-key` 配置 `hermes.fallback_providers`；可选 `--apply-to-users`，把新配置下发到已存在用户并重启对应 Hermes 服务。

- `provision_interface_user.py`
  创建一个系统托管的新用户。会创建 Linux 用户、写入 `users_mapping.yaml`、初始化 `~/.hermes` 和 `~/work`、安装并启动 Hermes service、创建网页登录账号。

- `deprovision_interface_user.py`
  删除一个系统托管用户。默认会移除网页账号、映射关系和 Hermes service；附加 `--delete-home` 时还会删除对应 Linux 用户 home。

- `bind_existing_linux_user.py`
  把服务器上已经存在的 Linux 用户绑定到网页系统。适合复用已有 home 目录、已有文件和已有 Linux 身份。

- `unbind_existing_linux_user.py`
  安全解绑一个已绑定的现有 Linux 用户。只移除网页绑定关系、映射关系和 Hermes service，不删除 Linux 用户本身，也不删除 home、`.hermes`、`work`。

更详细的开发背景和当前实现说明，可以看：`CURRENT_PROGRESS.md`。
