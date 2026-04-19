# Potato Agent

`potato_agent` 是一个面向多用户场景的 Hermes 网页入口项目。

它的核心思路是：

- 网页端由 `interface/` 提供
- 每个用户绑定一个独立 Linux 用户
- 每个 Linux 用户运行一个独立 Hermes systemd 服务
- 聊天、文件访问、工作目录权限都由对应 Linux 用户身份隔离

这份 README 只回答三个问题：

1. 这个项目是什么
2. 如何部署
3. 如何手动管理用户

更详细的开发进展、架构演进和历史说明，请看：`CURRENT_PROGRESS.md`

## 项目结构

- `interface/`
  轻量后端 + Lite 前端
- `hermes-agent/`
  Hermes 源码工作区
- `users_mapping.yaml`
  用户到 Linux/Hermes 实例的映射事实源
- `provision_interface_user.py`
  创建一个“由系统托管”的新用户
- `deprovision_interface_user.py`
  删除一个“由系统托管”的用户
- `bind_existing_linux_user.py`
  把服务器上已存在的 Linux 用户绑定进网页系统
- `unbind_existing_linux_user.py`
  安全解绑一个已绑定的现有 Linux 用户

## 工作方式

每个用户都有自己独立的运行单元：

- Linux 用户
- home 目录
- `work` 目录
- Hermes 配置目录
- Hermes systemd 服务
- Hermes API 端口

这样网页端虽然是统一入口，但底层执行、文件访问和状态存储都是按 Linux 用户隔离的。

## 部署

### 1. 前置条件

部署机器至少需要：

- Linux
- systemd
- root 权限
- Python 3

推荐准备共享上游模型密钥：

```bash
export POTATO_AGENT_SHARED_API_KEY='sk-...'
```

### 2. 部署 Hermes 运行时

本项目仓库里的 `hermes-agent/` 就是当前这套系统兼容的 Hermes 源码工作区。

推荐部署方式是把它安装到独立目录和独立虚拟环境中：

```bash
mkdir -p /opt/hermes-agent-src
rsync -a --delete ./hermes-agent/ /opt/hermes-agent-src/

python3 -m venv /opt/hermes-agent-venv
/opt/hermes-agent-venv/bin/pip install -e "/opt/hermes-agent-src[all]"
ln -sf /opt/hermes-agent-venv/bin/hermes /usr/local/bin/hermes
```

验证：

```bash
/usr/local/bin/hermes --help
```

说明：

- `interface` 默认就是按 `/usr/local/bin/hermes` 来安装每用户 Hermes service
- 后续如果你更新了仓库中的 `hermes-agent/`，需要重新同步到 `/opt/hermes-agent-src/`，再重启对应 Hermes 服务

### 3. 配置 Hermes 模型

Hermes 能正常运行，至少需要这些模型相关配置：

- `provider`
- `base_url`
- `default`
- `api_key`

当前项目默认从 `users_mapping.yaml` 的 `hermes.model` 段读取这些配置。

最关键的配置示例：

```yaml
hermes:
  executable: /usr/local/bin/hermes
  api_server_host: 127.0.0.1
  api_server_model_name: Hermes
  model:
    default: gpt-5.4
    provider: custom
    base_url: https://your-upstream-model-gateway.example/v1
    api_key: ${POTATO_AGENT_SHARED_API_KEY}
  extra_env:
    OPENAI_API_KEY: ${POTATO_AGENT_SHARED_API_KEY}
```

其中：

- `default`
  Hermes 默认使用的模型名，例如 `gpt-5.4`
- `provider`
  模型提供方类型；当前项目常用 `custom`
- `base_url`
  上游 OpenAI-compatible 模型网关地址
- `api_key`
  上游模型访问密钥；当前项目通常通过环境变量 `POTATO_AGENT_SHARED_API_KEY` 注入

如果没有正确设置 `base_url`、`provider`、`default`，即使系统服务能启动，Hermes 也无法正常完成聊天请求。

### 4. 创建 interface 运行环境

```bash
python3 -m venv /opt/interface-env
/opt/interface-env/bin/pip install -r ./interface/requirements.txt
```

### 5. 准备映射文件

首次部署建议先从模板复制：

```bash
cp ./users_mapping.example.yaml ./users_mapping.yaml
```

至少确认这些基础配置合理：

- `start_port`
- `hermes.executable`
- `hermes.model.base_url`
- `hermes.model.api_key`

### 6. 启动网页服务

```bash
/opt/interface-env/bin/python -m uvicorn interface.app:app --host 0.0.0.0 --port 3000
```

启动后访问：

```text
http://<host>:3000/lite
```

## 手动管理用户

### 1. 创建一个新用户

适用于：

- 服务器上还没有这个 Linux 用户
- 希望系统自动创建 Linux 用户并开通 Hermes

```bash
/opt/interface-env/bin/python ./provision_interface_user.py <username> <email> <password>
```

这个脚本会：

- 更新 `users_mapping.yaml`
- 创建 Linux 用户
- 创建 `~/.hermes` 和 `~/work`
- 安装并启动该用户的 Hermes service
- 创建网页登录账号

### 2. 删除一个系统托管用户

```bash
/opt/interface-env/bin/python ./deprovision_interface_user.py <username>
```

如果还想删除该 Linux 用户的 home 目录：

```bash
/opt/interface-env/bin/python ./deprovision_interface_user.py <username> --delete-home
```

### 3. 绑定一个已存在的 Linux 用户

适用于：

- 服务器上已经有某个 Linux 用户
- 不想新建 Linux 用户
- 希望直接为这个已有用户开通 Hermes 和网页登录能力

```bash
/opt/interface-env/bin/python ./bind_existing_linux_user.py \
  alice \
  alice@example.com \
  webpass123 \
  --linux-user alice
```

这个脚本会：

- 创建网页登录账号
- 在 `users_mapping.yaml` 中增加映射
- 直接复用现有 Linux 用户的 home 目录
- 默认使用：
  - `~/.hermes`
  - `~/work`
- 为这个已有 Linux 用户安装并启动 Hermes service

### 4. 安全解绑一个已存在的 Linux 用户

适用于：

- 只想移除网页绑定关系
- 不想删除服务器上原本存在的 Linux 用户和文件

```bash
/opt/interface-env/bin/python ./unbind_existing_linux_user.py alice
```

这个脚本会删除：

- `users_mapping.yaml` 中的映射
- 网页账号
- interface 展示态聊天记录
- 对应 Hermes service

这个脚本不会删除：

- Linux 用户本身
- home 目录
- `.hermes`
- `work` 目录

## 常用文件

- `users_mapping.yaml`
  用户和 Linux/Hermes 绑定关系
- `interface/data/interface.db`
  网页用户、展示态聊天记录、注册任务等
- `interface/data/archive.db`
  归档的旧会话和归档运行记录

## 说明

- 登录支持“用户名或邮箱”
- 旧会话归档由 `interface` 后台定时任务处理
- 详细开发背景、接口调整、当前验证状态，请查看：`CURRENT_PROGRESS.md`
