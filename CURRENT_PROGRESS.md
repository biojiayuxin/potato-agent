# 当前进展

更新时间：当前会话
项目目录：`/root/potato_agent`

## 当前目标

当前项目目标保持不变：

- Open WebUI 作为统一前端入口
- Hermes 作为后端执行层
- 每个 Open WebUI 用户绑定一个独立 Linux 用户
- 每个 Linux 用户运行一个独立 Hermes systemd 服务
- 用户前端最终只看到一个模型名 `Hermes`
- 后台通过唯一 wrapper `model_id`、唯一 `prefix_id` 和 Linux 用户隔离实现多租户隔离

## 当前目录状态

当前项目根目录主线内容：

- `README.md`
- `CURRENT_PROGRESS.md`
- `Hermes_OpenWebUI_multiuser_SOP.md`
- `end_to_end_multiuser_integration.md`
- `users_mapping.example.yaml`
- `users_mapping.yaml`
- `generate_multiuser_bundle.py`
- `provision_openwebui_hermes_user.py`
- `deprovision_openwebui_hermes_user.py`
- `deploy_openwebui_from_workspace.sh`
- `deploy_lite_to_installed_openwebui.sh`
- `LITE_FRONTEND.md`
- `hermes-agent/`
- `open-webui/`

说明：

- 旧目录名 `hermes_webUI_SOP` 已废弃，项目已统一改名为 `potato_agent`
- 项目文档中的调用路径已经统一为相对路径
- `hermes-agent/` 和 `open-webui/` 是源码工作区，不等于线上运行目录

## 当前已完成能力

### 1. 统一 mapping 驱动

入口：

- `./generate_multiuser_bundle.py`

作用：

- 从一份 `users_mapping.yaml` 同时生成 Hermes 侧与 Open WebUI 侧部署产物

当前可生成：

- 每用户 `.env`
- 每用户 `config.yaml`
- 每用户 systemd unit
- `apply_host.sh`
- Open WebUI `connections.json`
- Open WebUI `wrapper_mapping.yaml`
- Open WebUI `wrappers.import.json`
- `summary.json`
- `checklist.md`

### 2. 一键创建用户并绑定 Hermes

入口：

- `./provision_openwebui_hermes_user.py`

输入：

- `username`
- `email`
- `password`

自动完成：

- 创建或更新 Open WebUI 用户
- 更新 `users_mapping.yaml`
- 创建 Linux 用户
- 写入 Hermes `.env` 与 `config.yaml`
- 安装并启动 per-user systemd 服务
- 更新 Open WebUI connection 配置
- 导入该用户专属 wrapper model
- 重启 Open WebUI 并验证登录

### 3. 一键删除用户并解绑 Hermes

入口：

- `./deprovision_openwebui_hermes_user.py`

输入：

- `username`
- `password`

默认自动完成：

- 删除 Open WebUI wrapper model 与 access grants
- 删除 Open WebUI connection 配置
- 删除 Open WebUI 用户账号
- 删除聊天/目录/频道成员等用户残留数据
- 停止并移除 per-user Hermes systemd 服务
- 删除 Linux 用户
- 从 `users_mapping.yaml` 中移除该用户
- 重启 Open WebUI 并验证该用户不能再登录

附加选项：

- `--delete-home`
- `--keep-openwebui-user`

### 4. Hermes / Open WebUI 命名策略已收口

当前约定：

- Hermes API server 基础模型名统一为 `Hermes`
- 每个 Open WebUI connection 必须使用唯一 `prefix_id`
- 每个 Open WebUI private wrapper model 使用唯一 `model_id`
- 每个 wrapper 的显示名统一为 `Hermes`

这是为了避免 Open WebUI 将多个 OpenAI-compatible 连接返回的基础模型按同名 `id` 合并冲突。

### 5. Lite 轻量前端已落地

当前已经实现一套不依赖 npm 构建的 Lite 前端。

入口：

- `/lite`

代码位置：

- `open-webui/backend/open_webui/static/lite/index.html`
- `open-webui/backend/open_webui/static/lite/styles.css`
- `open-webui/backend/open_webui/static/lite/app.js`
- `open-webui/backend/open_webui/main.py`

当前支持：

- 登录
- 聊天
- 聊天切换
- 右侧文件树
- 文件下载

### 6. Lite 文件树已经从 terminal server 依赖切换为专用后端接口

已经确认 Hermes 本身不提供 Open WebUI 原生 terminal server 所需接口，因此当前实现已切换为 Lite 专用文件接口。

后端接口：

- `GET /api/lite/files/tree`
- `GET /api/lite/files/download`

当前目录边界策略：

- 优先使用 `home_dir`
- 没有 `home_dir` 时退回 `workdir`

这意味着 Lite 文件树当前默认从：

- `/home/<linux_user>`

开始，而不是只从：

- `/home/<linux_user>/work`

开始。

### 7. Open WebUI 部署脚本已补齐

为了适配“开发环境 + 部署环境”分离，当前已经新增两个部署脚本：

- `./deploy_openwebui_from_workspace.sh`
- `./deploy_lite_to_installed_openwebui.sh`

作用：

- 把仓库里的 Open WebUI 工作区源码部署到已安装的 Open WebUI 路径
- 适合当前这台机器的运行方式：
  - 源码在 `/root/potato_agent/open-webui/...`
  - 在线运行代码在 `/opt/open-webui-venv/...`

## 当前已验证状态

### 1. Hermes 运行时可用

当前机器上已确认：

- Hermes 可执行入口：`/usr/local/bin/hermes`
- Hermes Python 环境：`/opt/hermes-agent-venv`

### 2. 当前关键服务状态正常

当前已确认正常运行：

- `open-webui.service`
- `hermes-user-test.service`

### 3. `user_test` 真实用户链路已跑通

当前已确认：

- Open WebUI 用户存在：`user_test@example.com`
- Open WebUI 用户 id：`342f9bf2-7cda-4408-8124-bff02a4f6ed7`
- Open WebUI wrapper model 存在：`hermes-user-test`
- wrapper 绑定基础模型：`hermes-user-test.Hermes`
- wrapper 显示名：`Hermes`
- 该模型只授予对应用户读取权限

当前可用测试登录信息：

- email: `user_test@example.com`
- password: `jia123456`

### 4. Lite 前端已在 3000 服务上可用

当前可访问：

- `http://<host>:3000/lite`

已验证：

- 登录成功
- 聊天页面可进入
- 聊天历史可显示和切换
- Lite 文件树接口已返回 `/home/hmx_user_test` 下的目录内容

### 5. 一键新增和删除流程都做过真实验证

已验证新增：

- `user_test`
- `auto_test`

已验证删除：

- `auto_test`

删除验证确认以下内容都能被自动移除：

- Open WebUI 用户
- wrapper model
- access grants
- per-user Hermes service
- Linux 用户
- `users_mapping.yaml` 中对应条目

## 当前使用入口

### 新增用户

```bash
python3 ./provision_openwebui_hermes_user.py <username> <email> <password>
```

### 删除用户

```bash
python3 ./deprovision_openwebui_hermes_user.py <username> <password>
```

### 生成完整部署 bundle

```bash
export POTATO_AGENT_SHARED_API_KEY='sk-...'

python3 ./generate_multiuser_bundle.py ./users_mapping.yaml --output-dir ./generated_bundle
```

### 将工作区 Open WebUI 后端部署到已安装环境

```bash
sudo ./deploy_openwebui_from_workspace.sh
```

### 只部署 Lite 相关改动

```bash
sudo ./deploy_lite_to_installed_openwebui.sh
```

补充说明：

- `users_mapping.yaml` 支持 `${ENV_NAME}` 形式的环境变量占位符
- 当前共享 API key 推荐写成 `${POTATO_AGENT_SHARED_API_KEY}`
- 生成 bundle 和执行 provision/deprovision 脚本前，需要先在当前 shell 导出该变量

## 当前边界与已知说明

### 1. 强隔离依赖 Linux 用户层

真正的隔离边界来自：

- Linux 用户
- `HOME`
- `systemd User=`
- 文件系统权限

不是只靠 Open WebUI 授权，也不是只靠目录树展示逻辑。

### 2. 当前仓库源码不等于线上运行代码

当前机器上：

- 开发源码目录：`/root/potato_agent/open-webui`
- 在线运行目录：`/opt/open-webui-venv/lib64/python3.11/site-packages/open_webui`

因此改完源码后，必须额外部署一次，线上才会生效。

### 3. 当前目录不是 git 工作树主仓库

不要依赖 git 状态判断当前改动是否已经在线上生效。

## 下一步开发重点

下一步重点不再是继续补外围运维脚本，也不是继续围绕 Open WebUI 现有前端做小修补。

下一阶段重点已经明确为：

1. 魔改 Open WebUI 后端
2. 删除当前项目不需要的后端功能
3. 精简 Open WebUI 后端代码体积
4. 收缩启动路径和依赖链，降低部署复杂度
5. 保留当前项目真正需要的最小能力集：
   - 登录
   - 聊天
   - 聊天切换
   - Hermes OpenAI-compatible 转发
   - Lite 文件树与文件下载

具体方向：

1. 识别并裁剪当前项目不需要的 Open WebUI 模块
2. 减少与当前目标无关的 routers、models、utils、前端静态资源
3. 让 Lite 前端逐步成为主界面，而不是 Open WebUI 原前端的附属页面
4. 最终形成一个更轻量、可维护、可重新部署的定制化后端分支
