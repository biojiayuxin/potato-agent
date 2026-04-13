# 当前进展

更新时间：当前会话
项目目录：`/root/potato_agent`

## 当前目标

当前项目目标已经明确并保持不变：

- Open WebUI 作为统一前端
- Hermes 作为后端执行层
- 每个 Open WebUI 用户绑定一个独立 Linux 用户
- 每个 Linux 用户运行一个独立 Hermes systemd 服务
- 用户前端最终只看到一个模型名 `Hermes`
- 后台使用唯一 wrapper `model_id` 做隔离和授权

## 当前目录状态

当前项目根目录已经收敛为以下主线内容：

- `Hermes_OpenWebUI_multiuser_SOP.md`
- `README.md`
- `CURRENT_PROGRESS.md`
- `users_mapping.example.yaml`
- `users_mapping.yaml`
- `generate_multiuser_bundle.py`
- `provision_openwebui_hermes_user.py`
- `deprovision_openwebui_hermes_user.py`
- `end_to_end_multiuser_integration.md`
- `hermes-agent/`
- `open-webui/`
- `opencode.jsonc`

说明：

- 旧目录名 `hermes_webUI_SOP` 已废弃，项目现已改名为 `potato_agent`
- 项目自身文档中的调用路径已经统一改成相对路径
- `hermes-agent/` 和 `open-webui/` 仍然是源码快照，不是 git clone 工作树

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

## 当前已验证状态

### 1. 共享 Hermes 运行时可用

当前机器上已经确认：

- Hermes 共享入口：`/usr/local/bin/hermes`

### 2. 当前关键服务状态正常

当前已确认处于运行状态：

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

### 4. 一键新增和删除流程都做过真实验证

已验证新增：

- `user_test`
- `auto_test`

已验证删除：

- `auto_test`

删除验证确认了以下内容都能被自动移除：

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

补充说明：

- `users_mapping.yaml` 现在支持 `${ENV_NAME}` 形式的环境变量占位符
- 当前共享 API key 推荐写成 `${POTATO_AGENT_SHARED_API_KEY}`
- 生成 bundle 和执行 provision/deprovision 脚本前，需要先在当前 shell 导出该变量

## 当前边界与已知说明

### 1. 强隔离依赖 Linux 用户层

真正的隔离边界来自：

- Linux 用户
- `HOME`
- `systemd User=`
- 文件系统权限

不是只靠 Open WebUI 授权，也不是只靠 `terminal.cwd`。

### 2. `pwd` 默认落点目前不是必须收口项

当前真实测试中，`pwd` 返回到用户 home 目录也是可接受的。

如果后续需要更强的默认目录约束，可以再通过提示词或环境桥接，把工作目录进一步引导到 `/home/<linux_user>/work`。

### 3. 当前目录不是 git 仓库

不要依赖 git 状态来判断当前改动。

## 下一步建议

下一步重点开发内容已经明确为 Open WebUI 升级，而不是继续扩展运维脚本。

具体目标是：

1. 在聊天窗口增加右侧边栏
2. 在右侧边栏展示当前用户目录下的文件结构树
3. 文件树必须绑定当前登录用户的目录边界，不能跨用户读取
4. 这个文件树视图应直接服务聊天场景，帮助用户在对话时浏览和定位自己的工作区文件
