# potato_agent

本目录现在承载的是一套已经跑通的多用户方案：

- Open WebUI 作为统一前端
- Hermes 作为后端执行层
- 每个 Open WebUI 用户绑定一个 Linux 账号
- 每个 Linux 账号运行一个独立 Hermes systemd 服务
- 用户前端最终只看到一个模型名 `Hermes`
- 后台用唯一 wrapper `model_id` 做隔离，例如 `hermes-user-test`

## 当前目录

- `Hermes_OpenWebUI_multiuser_SOP.md`
  目标设计与原则
- `README.md`
  当前状态与使用入口
- `CURRENT_PROGRESS.md`
  更细的开发过程记录
- `users_mapping.example.yaml`
  统一 mapping 示例
- `users_mapping.yaml`
  当前机器上的真实 mapping
- `generate_multiuser_bundle.py`
  从统一 mapping 生成完整部署 bundle
- `provision_openwebui_hermes_user.py`
  一键创建 Open WebUI 用户并绑定独立 Linux/Hermes 实例
- `deprovision_openwebui_hermes_user.py`
  一键删除 Open WebUI 用户并解绑 Linux/Hermes 实例
- `end_to_end_multiuser_integration.md`
  端到端落地说明
- `hermes-agent/`
  Hermes 源码快照
- `open-webui/`
  Open WebUI 源码快照

## 最近已完成的修改

### 1. 项目目录已改名为 `potato_agent`

原目录名已经从：

- `hermes_webUI_SOP`

改为：

- `potato_agent`

同时，项目自身文档里的使用入口已经统一改成相对路径，例如：

```bash
python3 ./provision_openwebui_hermes_user.py ...
python3 ./deprovision_openwebui_hermes_user.py ...
python3 ./generate_multiuser_bundle.py ...
```

这样更换部署服务器时，不会因为复制了旧的绝对路径而报错。

### 2. 统一 mapping 驱动已经落地

入口：

- `./generate_multiuser_bundle.py`

它基于一份 `users_mapping.yaml` 同时生成：

- 每用户 Hermes `.env`
- 每用户 Hermes `config.yaml`
- 每用户 systemd unit
- `apply_host.sh`
- Open WebUI `connections.json`
- Open WebUI `wrapper_mapping.yaml`
- Open WebUI `wrappers.import.json`
- `summary.json`
- `checklist.md`

这一步把此前分散的 Hermes helper 和 Open WebUI helper 串成了一条主线。

### 3. 一键新增用户已经实现并做过真实验证

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
- 写入 Hermes `.env` 和 `config.yaml`
- 安装并启动 per-user systemd 服务
- 更新 Open WebUI connection 配置
- 导入该用户的 private wrapper model
- 重启 Open WebUI 并验证登录

已做过真实验证：

- `user_test`
  - `user_test@example.com`
  - Open WebUI 登录成功
  - 独立 Linux 用户与 Hermes 服务已生效
  - Open WebUI 里只看到一个 `Hermes`
- `auto_test`
  - 用来验证一键创建流程本身
  - 创建成功、登录成功、Hermes API 成功返回模型

### 4. 一键删除用户已经实现并做过真实验证

入口：

- `./deprovision_openwebui_hermes_user.py`

输入：

- `username`
- `password`

默认会自动完成：

- 删除 Open WebUI wrapper model 与 access grants
- 删除 Open WebUI connection 配置
- 删除 Open WebUI 用户账号
- 删除相关聊天/目录/频道成员等用户残留数据
- 停止并移除 per-user Hermes systemd 服务
- 删除 Linux 用户
- 从 `users_mapping.yaml` 中移除该用户
- 重启 Open WebUI 并验证该用户不能再登录

附加选项：

- `--delete-home`
  删除 Linux 用户 home 目录
- `--keep-openwebui-user`
  只解绑 Hermes，不删除 Open WebUI 用户账号

已做过真实验证：

- `auto_test`
  - Open WebUI 用户已删
  - wrapper model 已删
  - access grants 已删
  - `hermes-auto-test.service` 已停并移除
  - Linux 用户 `hmx_auto_test` 已删
  - 登录已被拒绝

### 5. Hermes / Open WebUI 命名策略已收口

当前约定是：

- Hermes API server 基础模型名：`Hermes`
- Open WebUI connection 必须设置唯一 `prefix_id`
- Open WebUI private wrapper model：
  - `id` 唯一，例如 `hermes-user-test`
  - `name` 统一为 `Hermes`

这是因为 Open WebUI 会按基础模型 `id` 合并多个 OpenAI 连接返回的模型。

正确做法是：

1. 每个 Hermes 实例都可以广告同一个基础模型名 `Hermes`
2. 每个 Open WebUI connection 设置唯一 `prefix_id`
3. Open WebUI 内部基础模型变成如：
   - `hermes-user-test.Hermes`
4. 再通过 wrapper model 给最终用户展示统一名字 `Hermes`

## 当前推荐使用方式

### 一键新增用户

```bash
python3 ./provision_openwebui_hermes_user.py \
  alice \
  alice@example.com \
  'AlicePassword123'
```

如果你需要指定 Hermes API key：

```bash
python3 ./provision_openwebui_hermes_user.py \
  alice \
  alice@example.com \
  'AlicePassword123' \
  --api-key 'sk-...'
```

### 一键删除用户

```bash
python3 ./deprovision_openwebui_hermes_user.py \
  alice \
  'AlicePassword123'
```

如果要同时删除 Linux home：

```bash
python3 ./deprovision_openwebui_hermes_user.py \
  alice \
  'AlicePassword123' \
  --delete-home
```

如果只解绑 Hermes，但保留 Open WebUI 账号：

```bash
python3 ./deprovision_openwebui_hermes_user.py \
  alice \
  'AlicePassword123' \
  --keep-openwebui-user
```

### 生成部署 bundle

```bash
cp ./users_mapping.example.yaml ./users_mapping.yaml

export POTATO_AGENT_SHARED_API_KEY='sk-...'

python3 ./generate_multiuser_bundle.py \
  ./users_mapping.yaml \
  --output-dir ./generated_bundle
```

然后以 root 执行：

```bash
./generated_bundle/apply_host.sh
```

`users_mapping.yaml` 现在支持 `${ENV_NAME}` 形式的环境变量占位符。
当前默认示例使用的是 `${POTATO_AGENT_SHARED_API_KEY}`，脚本会在运行时解析它，而不是把真实 key 固定写进 YAML。

## 当前现场状态

当前机器上已经确认：

- 共享 Hermes 可执行入口：`/usr/local/bin/hermes`
- Open WebUI 服务已恢复运行：`open-webui.service`
- `user_test` 已作为真实可登录测试用户存在
- `user_test` 的 Open WebUI 登录信息为：
  - email: `user_test@example.com`
  - password: `jia123456`

## 当前仍需注意的边界

### 1. 强隔离依赖 Linux 用户，不是只靠 cwd

真正的强隔离来自：

- 独立 Linux 用户
- 独立 `HOME`
- 独立 `systemd User=`
- Linux 文件权限

`terminal.cwd` 只是默认工作目录，不是安全边界。

### 2. `pwd` 默认落点不是当前强制收口项

当前真实测试里，`pwd` 返回到用户 home 目录也是可接受的，后续可以通过提示词或额外环境桥接，把工作目录引导到 `/home/<linux_user>/work`。

### 3. 当前目录不是 git 仓库

`hermes-agent/` 和 `open-webui/` 都是源码快照，不要依赖 git 工作流来判断修改。

## 下一步建议

下一步重点开发内容不是继续做运维脚本，而是升级 Open WebUI 前端交互：

1. 在聊天窗口增加右侧边栏
2. 右侧边栏显示当前用户目录下的文件结构树
3. 文件树内容需要与当前登录用户绑定，不能跨用户串目录
4. 侧栏应服务于聊天场景，方便用户在对话时查看和定位自己工作目录内的文件
