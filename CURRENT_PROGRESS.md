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

当前已完成的 Lite 前端体验优化包括：

- 页面高度固定为浏览器窗口高度
- 聊天区、左侧聊天列表、右侧文件树都已具备各自独立滚动
- 文件树长文件名不再换行，改为横向滚动
- 文件树隐藏以 `.` 开头的隐藏文件和隐藏目录
- 输入框改为：
  - `Enter` 发送
  - `Ctrl + Enter` / `Cmd + Enter` / `Shift + Enter` 换行
- 消息正文已支持 Markdown 渲染
- 用户消息和助手消息都已支持“复制原始内容”按钮
- 已开始接入 Hermes 流式过程信息展示

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

### 7. Lite 聊天消息已支持 Markdown 渲染

当前 Lite 聊天页已经不再只是纯文本加换行，而是支持基础 Markdown 渲染。

已支持的展示包括：

- 标题
- 列表
- 粗体 / 斜体
- 行内代码
- 代码块
- 引用块
- 表格
- 链接

当前实现策略：

- 后端仍返回原始 Markdown
- 前端本地用 `marked` 做渲染
- 再做一层基础 HTML 清理

### 8. Hermes 流式过程信息已开始接入 Lite 聊天页

当前已经确认：

- Hermes 在流式返回中会发出 `event: hermes.tool.progress`
- 某些 provider / 场景下还可能返回：
  - `delta.reasoning_content`
  - `delta.tool_calls`

Lite 前端当前已经开始解析并尝试展示：

- 推理过程
- 工具调用
- 执行进度

其中当前最稳定、已确认存在的数据来源是：

- `event: hermes.tool.progress`

### 9. Open WebUI 第一批精简已完成

当前已经完成一轮“只删确认无用代码”的后端精简，且上线后验证主链仍然正常。

已删除或停用的内容包括：

- 不必要 startup 初始化
- 大量当前 Lite 产品不需要的 router 注册
- tasks API endpoint
- Anthropic Messages 兼容入口：
  - `/api/message`
  - `/api/v1/messages`
- `/api/chat/completed`
- `/api/chat/actions/{action_id}`

说明：

- 这轮精简是先对照代码运行链确认后再删除
- 当前仍保留聊天主链真实依赖的模块，例如 `utils/middleware.py`、`utils/chat.py`、`socket.main` 等

### 10. Open WebUI 部署脚本已补齐

为了适配“开发环境 + 部署环境”分离，当前已经新增两个部署脚本：

- `./deploy_openwebui_from_workspace.sh`
- `./deploy_lite_to_installed_openwebui.sh`

作用：

- 把仓库里的 Open WebUI 工作区源码部署到已安装的 Open WebUI 路径
- 适合当前这台机器的运行方式：
  - 源码在 `/root/potato_agent/open-webui/...`
  - 在线运行代码在 `/opt/open-webui-venv/...`

### 11. Hermes 源码工作区与运行时已同步升级到 `0.9.0`

当前已经完成 Hermes 版本收口，避免出现“项目工作区还是旧代码、线上运行已经是新版本”的错位。

当前状态：

- 工作区源码目录：`/root/potato_agent/hermes-agent`
- 在线部署源码目录：`/opt/hermes-agent-src`
- 在线运行环境：`/opt/hermes-agent-venv`
- 在线命令入口：`/usr/local/bin/hermes`
- 当前 Hermes 版本：`v0.9.0 (2026.4.13)`

本次升级时已完成：

- 先备份升级前工作区、部署源码和运行时 venv
- 再将 `/root/hermes-agent.260413` 的新源码同步到项目工作区
- 再从项目工作区同步到 `/opt/hermes-agent-src`
- 最后刷新 `/opt/hermes-agent-venv` 的 editable 安装并重启 per-user Hermes 服务

## 当前已验证状态

### 1. Hermes 运行时可用

当前机器上已确认：

- Hermes 工作区源码：`/root/potato_agent/hermes-agent`
- Hermes 在线部署源码：`/opt/hermes-agent-src`
- Hermes 可执行入口：`/usr/local/bin/hermes`
- Hermes Python 环境：`/opt/hermes-agent-venv`
- Hermes 当前在线版本：`v0.9.0 (2026.4.13)`

### 2. 当前关键服务状态正常

当前已确认正常运行：

- `open-webui.service`
- `hermes-user-test.service`
- `hermes-user1.service`

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
- Lite 文件树已隐藏隐藏文件
- Lite 消息已支持 Markdown 渲染
- Lite 消息气泡下方已支持复制原始内容
- Lite 已开始显示 Hermes 流式工具进度
- Lite 回复气泡的等待动画逻辑已修复：
  - 不会再错误显示在用户消息下方
  - 模型输出完成后会自动消失
  - 刷新历史聊天后不会残留思考动画
- Lite 聊天消息区域已去除横向滚动：
  - 过长单行内容会自动换行
  - 横向滚动只保留在文件树窗口
- Lite 三栏布局已支持桌面端拖拽调整宽度：
  - 聊天列表
  - 聊天消息
  - 文件树
- 聊天列表已新增删除按钮，可删除当前聊天及服务器保存记录
- 删除按钮已改为图标按钮，位置调整到聊天卡片下方
- 消息复制按钮已改为图标按钮：
  - 默认显示 `copy_button.png`
  - 点击后切换为 `copied.png`
  - 3 秒后自动恢复
- 删除按钮和复制按钮已支持 hover 显示逻辑：
  - hover 聊天列表项时显示删除按钮
  - hover 消息气泡时显示复制按钮
- Lite 页面颜色主题切换功能已完成：
  - 支持 `light`
  - 支持 `dark`
  - 支持 `system`
  - 主题选择结果会持久化保存
- Lite 输入框与发送交互已完成：
  - 输入框默认单行显示
  - 输入内容会自动增高
  - 最高增长到 8 行
  - 超过 8 行后在输入框内部滚动
  - 发送按钮已改为图标按钮并放到输入框右侧
  - 发送中会切换为停止按钮
  - 响应完成后会恢复为发送按钮
  - 用户可以主动停止当前模型响应
- 当前 `user_test` 的 Hermes 配置已显式写入 `agent.reasoning_effort: high`
- 当前 `hermes-user-test.service` 已按 `high` 默认推理深度重启成功
- 本次 Hermes `0.9.0` 升级后已再次验证：
  - `http://127.0.0.1:8643/v1/models` 正常
  - `http://127.0.0.1:8644/v1/models` 正常
  - 非流式聊天正常
  - 流式聊天正常
  - `event: hermes.tool.progress` 仍正常返回

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

## 当前更新进展

### 1. 聊天页面显示优化已完成

之前标记为“下一步”的聊天显示优化现在已经落地，当前已完成：

- Markdown 渲染后的段落、列表、代码块显示已做过一轮收口
- 模型尚未开始输出时，回复气泡会显示等待动画
- 模型开始真实输出后，等待动画会自动消失
- 刷新历史聊天后不会残留错误的思考动画
- 聊天消息区域已去除横向滚动
- 过长单行内容会自动换行
- 横向滚动只保留在文件树窗口

### 2. Hermes 默认推理深度已统一为 `high`

这项工作当前已经完成并收口：

- 已确认 Hermes 使用 `agent.reasoning_effort` 作为配置入口
- 生成链默认值已统一调整为 `high`
- `users_mapping.yaml` 与 `users_mapping.example.yaml` 已显式写入该默认
- 当前部署中的用户 Hermes 配置已按 `high` 运行
- 当前在线 per-user Hermes 服务已按该默认值重启验证通过

### 3. Lite 页面布局与聊天管理问题已完成

之前列出的布局和聊天管理问题当前都已处理：

- 三栏布局支持桌面端拖拽调整宽度
- 聊天列表已增加删除按钮
- 删除按钮已改为图标按钮
- hover 聊天列表项时会显示删除按钮
- 可直接从 Lite 前端删除当前聊天
- 删除操作会同步删除 Open WebUI 后端保存的对应聊天记录

### 4. 消息输入框与发送/停止交互已完成

之前列出的输入区交互优化当前已经完成：

- 输入框默认缩减为单行高度
- 输入框高度会随内容自动增长
- 最大增长到 8 行
- 超过 8 行后在输入框内部出现滚动条
- 发送按钮已改为图标按钮并放到输入框右侧
- 发送后会切换为停止按钮
- 模型响应完成后会恢复为发送按钮
- 用户可以主动停止当前响应

### 5. Hermes 版本升级已完成并完成联动验证

本次会话已经完成 Hermes 的源码工作区、部署源码和运行时三处同步升级。

当前状态：

- 项目工作区源码：`/root/potato_agent/hermes-agent`
- 在线部署源码：`/opt/hermes-agent-src`
- 在线运行环境：`/opt/hermes-agent-venv`
- 在线命令入口：`/usr/local/bin/hermes`
- 当前在线版本：`Hermes Agent v0.9.0 (2026.4.13)`

本次升级已经验证：

- `hermes-user-test.service` 重启成功并保持运行
- `hermes-user1.service` 重启成功并保持运行
- `GET /v1/models` 在 `8643` / `8644` 端口均正常
- 非流式 `chat/completions` 正常
- 流式 `chat/completions` 正常
- `event: hermes.tool.progress` 仍然正常返回

说明：

- 当前网页访问到的 Hermes 继续走 per-user systemd 服务，而不是 `/root` 下手动启动的 root Hermes
- 这次升级已经避免“项目工作区还是旧版、线上运行已经新版”的源码错位问题
- 如需回滚，可使用备份目录：`/root/hermes-upgrade-backup-20260414-230811`
