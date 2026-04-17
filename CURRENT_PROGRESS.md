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

当前项目主线内容：

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

- 旧目录名 `hermes_webUI_SOP` 已废弃，项目已统一改名为 `potato_agent`
- 项目文档中的调用路径已经统一为相对路径
- `hermes-agent/` 和 `open-webui/` 是源码工作区，不等于线上运行目录

## 当前已完成能力

### 1. 统一 mapping 驱动

- `./generate_multiuser_bundle.py`

- 从一份 `users_mapping.yaml` 生成 Hermes 和 Open WebUI 的部署产物，包括：

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

- `./provision_openwebui_hermes_user.py`

- 输入：`username`、`email`、`password`
- 自动完成：

- 创建或更新 Open WebUI 用户
- 更新 `users_mapping.yaml`
- 创建 Linux 用户
- 写入 Hermes `.env` 与 `config.yaml`
- 安装并启动 per-user systemd 服务
- 更新 Open WebUI connection 配置
- 导入该用户专属 wrapper model
- 重启 Open WebUI 并验证登录

### 3. 一键删除用户并解绑 Hermes

- `./deprovision_openwebui_hermes_user.py`

- 输入：`username`、`password`
- 默认自动完成：

- 删除 Open WebUI wrapper model 与 access grants
- 删除 Open WebUI connection 配置
- 删除 Open WebUI 用户账号
- 删除聊天/目录/频道成员等用户残留数据
- 停止并移除 per-user Hermes systemd 服务
- 删除 Linux 用户
- 从 `users_mapping.yaml` 中移除该用户
- 重启 Open WebUI 并验证该用户不能再登录

- 附加选项：`--delete-home`、`--keep-openwebui-user`

### 4. Hermes / Open WebUI 命名策略已收口

当前约定：

- Hermes API server 基础模型名统一为 `Hermes`
- 每个 Open WebUI connection 必须使用唯一 `prefix_id`
- 每个 Open WebUI private wrapper model 使用唯一 `model_id`
- 每个 wrapper 的显示名统一为 `Hermes`

这是为了避免 Open WebUI 将多个 OpenAI-compatible 连接返回的基础模型按同名 `id` 合并冲突。

### 5. Lite 轻量前端已落地

- 已实现一套不依赖 npm 构建的 Lite 前端。
- 入口：`/lite`
- 代码位置：

- `open-webui/backend/open_webui/static/lite/index.html`
- `open-webui/backend/open_webui/static/lite/styles.css`
- `open-webui/backend/open_webui/static/lite/app.js`
- `open-webui/backend/open_webui/main.py`

- 当前支持：登录、聊天、聊天切换、右侧文件树、文件下载
- 已完成的前端体验优化包括：

- 页面高度固定为浏览器窗口高度
- 聊天区、左侧聊天列表、右侧文件树都已具备各自独立滚动
- 文件树长文件名不再换行，改为横向滚动
- 文件树隐藏以 `.` 开头的隐藏文件和隐藏目录
- 输入框支持 `Enter` 发送，`Ctrl/Cmd + Enter` 或 `Shift + Enter` 换行
- 消息正文已支持 Markdown 渲染
- 用户消息和助手消息都已支持“复制原始内容”按钮
- 已开始接入 Hermes 流式过程信息展示

### 6. Lite 附件上传已切换为 Hermes 原生处理

- 当前附件链路不再依赖 Open WebUI 解析 PDF / 图片，而是：

- 前端上传文件到本地存储
- Lite 通过专用接口获取真实文件路径
- 聊天请求将“附件名 + 本地路径”提示交给 Hermes
- Hermes 自行用工具处理 PDF、图片和 Office 文档

- 上传文件落到 `/tmp`
- 文件名格式：`<uuid>_<年月日时分秒>_<filename>`
- 支持点击 `+` 上传
- 支持在聊天输入框直接粘贴剪贴板文件/图片上传

- PDF 可正常解析
- 图片可正常解析
- 附件统一按同一条链路提交给 Hermes

### 7. Lite 文件树已经从 terminal server 依赖切换为专用后端接口

已经确认 Hermes 本身不提供 Open WebUI 原生 terminal server 所需接口，因此当前实现已切换为 Lite 专用文件接口。

- `GET /api/lite/files/tree`
- `GET /api/lite/files/download`

- 优先使用 `home_dir`
- 没有 `home_dir` 时退回 `workdir`

Lite 文件树当前默认从 `/home/<linux_user>` 开始，而不是只从 `/home/<linux_user>/work` 开始。

### 8. Lite 聊天消息已支持 Markdown 渲染

- 标题
- 列表
- 粗体 / 斜体
- 行内代码
- 代码块
- 引用块
- 表格
- 链接

- 实现方式：后端仍返回原始 Markdown，前端用 `marked` 渲染并做基础 HTML 清理

### 9. Hermes 流式过程信息已开始接入 Lite 聊天页

- Hermes 在流式返回中会发出 `event: hermes.tool.progress`
- 某些 provider / 场景下还可能返回 `delta.reasoning_content`、`delta.tool_calls`

- 推理过程
- 工具调用
- 执行进度

其中当前最稳定的来源是 `event: hermes.tool.progress`。

### 10. Open WebUI 第一批精简已完成

- 已完成一轮“只删确认无用代码”的后端精简，主链验证通过。已删除或停用：

- 不必要 startup 初始化
- 大量当前 Lite 产品不需要的 router 注册
- tasks API endpoint
- Anthropic Messages 兼容入口：
  - `/api/message`
  - `/api/v1/messages`
- `/api/chat/completed`
- `/api/chat/actions/{action_id}`

- 当前仍保留聊天主链真实依赖的模块，例如 `utils/middleware.py`、`utils/chat.py`、`socket.main` 等

### 11. Open WebUI 部署脚本已补齐

为适配“开发环境 + 部署环境”分离，当前已新增：

- `./deploy_openwebui_from_workspace.sh`
- `./deploy_lite_to_installed_openwebui.sh`

- 用于把仓库里的 Open WebUI 工作区源码部署到已安装环境；当前机器的源码路径在 `/root/potato_agent/open-webui/...`，在线运行代码在 `/opt/open-webui-venv/...`

### 12. Hermes 源码工作区与运行时已同步升级到 `0.9.0`

- 当前已完成 Hermes 版本收口，避免“工作区旧版、线上新版”的错位。当前状态：

- 工作区源码目录：`/root/potato_agent/hermes-agent`
- 在线部署源码目录：`/opt/hermes-agent-src`
- 在线运行环境：`/opt/hermes-agent-venv`
- 在线命令入口：`/usr/local/bin/hermes`
- 当前 Hermes 版本：`v0.9.0 (2026.4.13)`

- 升级过程：先备份，再同步工作区与部署源码，最后刷新 `/opt/hermes-agent-venv` 并重启 per-user Hermes 服务

## 当前已验证状态

### 1. Hermes 运行时可用

- Hermes 工作区源码：`/root/potato_agent/hermes-agent`
- Hermes 在线部署源码：`/opt/hermes-agent-src`
- Hermes 可执行入口：`/usr/local/bin/hermes`
- Hermes Python 环境：`/opt/hermes-agent-venv`
- Hermes 当前在线版本：`v0.9.0 (2026.4.13)`

### 2. 当前关键服务状态正常

- `open-webui.service`
- `hermes-user-test.service`
- `hermes-user1.service`

### 3. `user_test` 真实用户链路已跑通

- Open WebUI 用户存在：`user_test@example.com`
- Open WebUI 用户 id：`342f9bf2-7cda-4408-8124-bff02a4f6ed7`
- Open WebUI wrapper model 存在：`hermes-user-test`
- wrapper 绑定基础模型：`hermes-user-test.Hermes`
- wrapper 显示名：`Hermes`
- 该模型只授予对应用户读取权限

- email: `user_test@example.com`
- password: `jia123456`

### 4. Lite 前端已在 3000 服务上可用

- `http://<host>:3000/lite`

- 登录成功
- 聊天页面可进入
- 聊天历史可显示和切换
- Lite 文件树接口已返回 `/home/hmx_user_test` 下的目录内容
- Lite 文件树已隐藏隐藏文件
- Lite 消息已支持 Markdown 渲染
- Lite 消息气泡下方已支持复制原始内容
- Lite 已开始显示 Hermes 流式工具进度
- Lite 回复等待动画逻辑已修复，刷新历史聊天不会残留
- Lite 聊天区已去除横向滚动，长行自动换行
- Lite 三栏布局已支持桌面端拖拽调整宽度
- 聊天列表已新增删除按钮，可删除当前聊天及服务器保存记录
- 删除按钮与复制按钮已改为图标按钮，并支持 hover 显示
- 主题切换支持 `light` / `dark` / `system`，并持久化保存
- 输入框默认单行显示，自动增高到 8 行，发送中可切换为停止按钮
- 已支持附件上传、粘贴上传，并统一交给 Hermes 处理
- 上传文件当前落在 `/tmp`
- 用户发送消息后，输入区附件会立即清空，不再等 AI 回复结束
- Lite 附件上传已增加单文件 `20 MB` 大小限制，超限会直接拒绝上传
- 聊天区错误提示已挪到输入框上方，并会在 `10s` 后自动消失
- 附件超限提示已统一为英文固定文案：`Upload file too large (> 20 MB).`
- 上传附件按钮已从 `+` 改为 PNG 图标
- 当前 `user_test` 的 Hermes 配置已显式写入 `agent.reasoning_effort: high`
- 当前 `hermes-user-test.service` 已按 `high` 默认推理深度重启成功
- Hermes `0.9.0` 升级后，`8643` / `8644` 的 `/v1/models`、非流式聊天、流式聊天、`event: hermes.tool.progress` 均已验证正常

### 5. 一键新增和删除流程都做过真实验证

- 已验证新增：`user_test`、`auto_test`
- 已验证删除：`auto_test`
- 删除后会自动清理：Open WebUI 用户、wrapper model、access grants、per-user Hermes service、Linux 用户、`users_mapping.yaml` 条目

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

- `users_mapping.yaml` 支持 `${ENV_NAME}` 形式的环境变量占位符
- 当前共享 API key 推荐写成 `${POTATO_AGENT_SHARED_API_KEY}`
- 生成 bundle 和执行 provision/deprovision 脚本前，需要先在当前 shell 导出该变量

## 当前边界与已知说明

### 1. 强隔离依赖 Linux 用户层

- Linux 用户
- `HOME`
- `systemd User=`
- 文件系统权限

不是只靠 Open WebUI 授权，也不是只靠目录树展示逻辑。

### 2. 当前仓库源码不等于线上运行代码

- 开发源码目录：`/root/potato_agent/open-webui`
- 在线运行目录：`/opt/open-webui-venv/lib64/python3.11/site-packages/open_webui`

因此改完源码后，必须额外部署一次，线上才会生效。

### 3. 当前目录不是 git 工作树主仓库

不要依赖 git 状态判断当前改动是否已经在线上生效。

## 当前更新进展

### 1. 聊天页面显示优化已完成

- Markdown 渲染后的段落、列表、代码块显示已做过一轮收口
- 模型尚未开始输出时，回复气泡会显示等待动画
- 模型开始真实输出后，等待动画会自动消失
- 刷新历史聊天后不会残留错误的思考动画
- 聊天消息区域已去除横向滚动
- 过长单行内容会自动换行
- 横向滚动只保留在文件树窗口

### 2. Hermes 默认推理深度已统一为 `high`

- 已确认 Hermes 使用 `agent.reasoning_effort` 作为配置入口
- 生成链默认值已统一调整为 `high`
- `users_mapping.yaml` 与 `users_mapping.example.yaml` 已显式写入该默认
- 当前部署中的用户 Hermes 配置已按 `high` 运行
- 当前在线 per-user Hermes 服务已按该默认值重启验证通过

### 3. Lite 页面布局与聊天管理问题已完成

- 三栏布局支持桌面端拖拽调整宽度
- 聊天列表已增加删除按钮
- 删除按钮已改为图标按钮
- hover 聊天列表项时会显示删除按钮
- 可直接从 Lite 前端删除当前聊天
- 删除操作会同步删除 Open WebUI 后端保存的对应聊天记录

### 4. 消息输入框与发送/停止交互已完成

- 输入框默认缩减为单行高度
- 输入框高度会随内容自动增长
- 最大增长到 8 行
- 超过 8 行后在输入框内部出现滚动条
- 发送按钮已改为图标按钮并放到输入框右侧
- 发送后会切换为停止按钮
- 模型响应完成后会恢复为发送按钮
- 用户可以主动停止当前响应

### 5. Hermes 版本升级已完成并完成联动验证

- 已完成 Hermes 源码工作区、部署源码和运行时三处同步升级。当前状态：

- 项目工作区源码：`/root/potato_agent/hermes-agent`
- 在线部署源码：`/opt/hermes-agent-src`
- 在线运行环境：`/opt/hermes-agent-venv`
- 在线命令入口：`/usr/local/bin/hermes`
- 当前在线版本：`Hermes Agent v0.9.0 (2026.4.13)`

- `hermes-user-test.service` 重启成功并保持运行
- `hermes-user1.service` 重启成功并保持运行
- `GET /v1/models` 在 `8643` / `8644` 端口均正常
- 非流式 `chat/completions` 正常
- 流式 `chat/completions` 正常
- `event: hermes.tool.progress` 仍然正常返回

- 当前网页访问到的 Hermes 继续走 per-user systemd 服务，而不是 `/root` 下手动启动的 root Hermes
- 这次升级已经避免“项目工作区还是旧版、线上运行已经新版”的源码错位问题
- 如需回滚，可使用备份目录：`/root/hermes-upgrade-backup-20260414-230811`

### 6. Lite 附件与提示体验优化已完成

- 用户消息发送后，输入区附件会立即清空，不再等 AI 回复完成后才消失
- Lite 附件上传已增加单文件 `20 MB` 限制
- 超过限制时会直接拒绝上传，并显示固定英文提示：`Upload file too large (> 20 MB).`
- 聊天区错误提示已改到输入框上方，避免把 `+` / 附件按钮和输入框挤偏
- 聊天区错误提示会在 `10s` 后自动消失
- 上传附件按钮已从文本 `+` 改为 PNG 图标
- `deploy_lite_to_installed_openwebui.sh` 已补充同步 `static/lite/icons/attachment.png`，后续重部署会自动带上该资源
