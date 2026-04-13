# End-to-End Multi-User Integration

本文档描述当前目录中已经实现的推荐落地方式：

- Open WebUI 作为统一前端
- Hermes 作为后端执行层
- 每个 Open WebUI 用户绑定一个 Linux 账号
- 每个 Linux 账号运行一个独立 Hermes systemd 服务
- 前端最终统一显示模型名 `Hermes`
- 后台保持唯一 wrapper `model_id`

## 重要约束

有一个实现细节必须先说清楚：

- Hermes API server 的 `/v1/models` 只能返回一个基础模型 id。
- Open WebUI 默认会按基础模型 `id` 合并多个 OpenAI 连接返回的模型。

因此，如果 Alice 和 Bob 的两个 Hermes 连接都直接返回同一个基础模型 id `Hermes`，而 Open WebUI 连接没有设置唯一前缀，那么两个连接会在 Open WebUI 里冲突。

正确做法是：

1. Hermes 实例都可以对外广告同一个基础模型名 `Hermes`。
2. Open WebUI 给每个连接配置唯一 `prefix_id`，例如：
   - `hermes-alice`
   - `hermes-bob`
3. 这样 Open WebUI 内部看到的基础模型会变成：
   - `hermes-alice.Hermes`
   - `hermes-bob.Hermes`
4. 然后再为每个用户创建一个 private wrapper model：
   - `id: hermes-alice`
   - `id: hermes-bob`
   - `name: Hermes`

最终用户只看到自己的 wrapper `Hermes`，而不是底层基础模型。

## 1. 准备统一 mapping

复制示例文件：

```bash
cp ./users_mapping.example.yaml ./users_mapping.yaml

export POTATO_AGENT_SHARED_API_KEY='sk-...'
```

至少补齐这些字段：

- `openwebui_user_id`
- `api_key`
- `hermes.model.base_url`
- `hermes.model.api_key`

说明：

- `users_mapping.yaml` 支持 `${ENV_NAME}` 形式的环境变量占位符
- 当前推荐把共享 API key 写成 `${POTATO_AGENT_SHARED_API_KEY}`
- 在执行 `generate_multiuser_bundle.py`、`provision_openwebui_hermes_user.py`、`deprovision_openwebui_hermes_user.py` 前，先在当前 shell 里导出对应环境变量

建议每个用户至少明确维护：

- `webui_user`
- `webui_display_name`
- `username`
- `linux_user`
- `home_dir`
- `hermes_home`
- `workdir`
- `api_port`
- `api_key`
- `connection_prefix`
- `model_id`
- `model_name`
- `systemd_service`

## 2. 生成部署 bundle

运行：

```bash
python3 ./generate_multiuser_bundle.py \
  ./users_mapping.yaml \
  --output-dir ./generated_bundle
```

输出目录会包含：

- `users/<username>/.hermes/.env`
- `users/<username>/.hermes/config.yaml`
- `systemd/<service>.service`
- `openwebui/connections.json`
- `openwebui/wrapper_mapping.yaml`
- `openwebui/wrappers.full.json`
- `openwebui/wrappers.import.json`
- `apply_host.sh`
- `summary.json`
- `checklist.md`

## 3. 安装 Linux 用户与 Hermes 服务

以 root 执行：

```bash
./generated_bundle/apply_host.sh
```

这个脚本会：

- 创建缺失的 Linux 用户
- 创建 `/home/<linux_user>/work`
- 创建 `/home/<linux_user>/.hermes`
- 安装 `.env` 和 `config.yaml`
- 安装 systemd unit 到 `/etc/systemd/system/`
- `systemctl enable --now` 启动每个服务

验证服务：

```bash
systemctl status hermes-alice.service
systemctl status hermes-bob.service
```

验证 API：

```bash
curl -H "Authorization: Bearer <alice-api-key>" http://127.0.0.1:8643/v1/models
curl -H "Authorization: Bearer <bob-api-key>" http://127.0.0.1:8644/v1/models
```

预期每个实例都返回基础模型 id `Hermes`。

## 4. 配置 Open WebUI Connections

在 Open WebUI 管理界面中，为每个 Hermes 实例新增一个 OpenAI-compatible 连接。

以 Alice 为例：

- URL: `http://127.0.0.1:8643/v1`
- API Key: Alice 的 `API_SERVER_KEY`
- `prefix_id`: `hermes-alice`

以 Bob 为例：

- URL: `http://127.0.0.1:8644/v1`
- API Key: Bob 的 `API_SERVER_KEY`
- `prefix_id`: `hermes-bob`

配置完成后，Open WebUI 内部基础模型会变成：

- `hermes-alice.Hermes`
- `hermes-bob.Hermes`

这个 `prefix_id` 是必须的。没有它，多个 Hermes 连接会因为相同基础模型 id 而发生合并冲突。

## 5. 生成并导入 wrapper models

如果只想单独验证 Open WebUI helper，也可以直接运行：

```bash
python3 ./open-webui/backend/open_webui/tools/hermes_model_wrapper_helper.py \
  ./generated_bundle/openwebui/wrapper_mapping.yaml \
  --dry-run
```

或直接使用 bundle 中已经生成好的 import payload：

- `./generated_bundle/openwebui/wrappers.import.json`

通过 Open WebUI API 导入：

```bash
curl -X POST "http://127.0.0.1:3000/api/v1/models/import" \
  -H "Authorization: Bearer <admin-token>" \
  -H "Content-Type: application/json" \
  --data @./generated_bundle/openwebui/wrappers.import.json
```

导入后：

- Alice 的 wrapper：
  - `id = hermes-alice`
  - `base_model_id = hermes-alice.Hermes`
  - `name = Hermes`
- Bob 的 wrapper：
  - `id = hermes-bob`
  - `base_model_id = hermes-bob.Hermes`
  - `name = Hermes`

## 6. 验证隔离

逐个用户验证：

1. 登录 Open WebUI。
2. 确认只看到一个模型，显示名为 `Hermes`。
3. 发起一次终端任务并执行 `pwd`。
4. 确认落在自己的工作目录：
   - Alice -> `/home/hmx_alice/work`
   - Bob -> `/home/hmx_bob/work`

还应验证：

```bash
sudo -u hmx_alice -H bash -lc 'pwd && ls -la ~ && test -d /home/hmx_alice/.hermes'
sudo -u hmx_bob -H bash -lc 'pwd && ls -la ~ && test -d /home/hmx_bob/.hermes'
```

## 7. 常见误区

### 误区 1：只用 Hermes profile 就够了

不够。

- profile 可以隔离 Hermes 内部状态。
- 但如果多个实例仍由同一个 Linux 用户运行，就不是操作系统级强隔离。

### 误区 2：只要 `terminal.cwd` 分开就够了

不够。

- `cwd` 只影响默认相对路径。
- 它不能代替 Linux 文件权限和进程身份隔离。

### 误区 3：每个 Hermes 连接都直接把基础模型 id 设成 `Hermes`

只有在 Open WebUI 连接层设置唯一 `prefix_id` 时才安全。

否则多个连接会在 Open WebUI 模型列表中冲突合并。

## 8. 推荐顺序

建议按这个顺序推进：

1. 先把 Alice 单独跑通。
2. 确认 Linux 用户、systemd、Open WebUI connection `prefix_id`、wrapper import 都正确。
3. 再复制到 Bob 和更多用户。
