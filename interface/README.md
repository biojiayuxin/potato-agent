# Interface

`interface/` 是新的轻后端 + Lite 前端目录，目标是直接贴 Hermes 能力。

当前实现：

- 认证：使用 `interface` 自己的 SQLite 用户库
- 会话列表/消息：直接读取每个用户自己的 Hermes `state.db`
- 聊天：直接代理对应用户 Hermes 的 `/v1/chat/completions`
- 模型：直接代理对应用户 Hermes 的 `/v1/models`
- 文件树/下载/上传：由 `interface` 自己提供
- 前端：迁移后的 Lite 页面位于 `interface/static/lite/`

## 目录

- `app.py`：FastAPI 入口
- `requirements.txt`：最小依赖
- `static/lite/`：前端页面、样式、脚本、图标

## 依赖的数据源

- `users_mapping.yaml`
- `interface/data/interface.db`
- 每用户 `~/.hermes/state.db`

## 关键环境变量

- `POTATO_AGENT_MAPPING_PATH`
- `INTERFACE_AUTH_DB`
- `INTERFACE_SESSION_SECRET`
- `INTERFACE_SESSION_TTL_SECONDS`
- `INTERFACE_MAX_UPLOAD_BYTES`
- `INTERFACE_UPLOAD_DIR_NAME`

## 用户管理

新增用户：

```bash
/opt/interface-env/bin/python ./provision_interface_user.py <username> <email> <password>
```

删除用户：

```bash
/opt/interface-env/bin/python ./deprovision_interface_user.py <username>
```

这两个脚本会直接维护：

- `users_mapping.yaml`
- `interface/data/interface.db`
- per-user Linux 用户
- per-user Hermes 配置和 systemd 服务

绑定服务器上已存在的 Linux 用户：

```bash
/opt/interface-env/bin/python ./bind_existing_linux_user.py \
  alice \
  alice@example.com \
  webpass123 \
  --linux-user alice
```

这个脚本会：

- 创建 interface 网页登录账号
- 在 `users_mapping.yaml` 中增加映射
- 直接复用现有 Linux 用户的 home 目录
- 默认目录沿用当前规则：
  - `~/.hermes`
  - `~/work`
- 为这个已有 Linux 用户安装并启动 Hermes service

安全解绑已绑定的现有 Linux 用户：

```bash
/opt/interface-env/bin/python ./unbind_existing_linux_user.py alice
```

这个脚本会：

- 删除 interface 网页账号
- 删除 interface 展示态聊天记录
- 删除 `users_mapping.yaml` 中的映射
- 停止并移除对应 Hermes service

但不会删除：

- Linux 用户本身
- home 目录
- `.hermes`
- `work` 目录

## 启动

使用仓库外的独立虚拟环境 `/opt/interface-env` 来启动 `interface`，避免把部署依赖混在开发目录里。

首次创建环境：

```bash
python3 -m venv /opt/interface-env
/opt/interface-env/bin/pip install -r ./interface/requirements.txt
```

```bash
/opt/interface-env/bin/python -m uvicorn interface.app:app --host 0.0.0.0 --port 3001
```

启动后访问：

```text
http://<host>:3001/lite
```

## 当前边界

- Hermes 当前在线只开了 API server，没开 `web_server`，所以会话列表不是走 Hermes HTTP，而是直接读 `state.db`
- 附件上传保存到每用户 `workdir` 下的 `.potato-interface-uploads/`
- `users_mapping.yaml` 里仍保留一些历史 `openwebui_*` 字段；`interface` 运行时不会使用它们
