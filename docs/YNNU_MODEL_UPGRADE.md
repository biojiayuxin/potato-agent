# YNNU 生产环境：统一模型目录升级

本手册覆盖对话独立模型、统一模型目录及旧兼容代码清理这次升级。
它补充 [HPC 部署指南](../HPC_DEPLOYMENT.md)，不替代其中的系统安全、离线构建、维护页和备份步骤。
本机测试服务器的部署不代表 YNNU 已升级；执行前须由生产负责人确认目标主机、维护窗口和回退方案。
本次文档检查没有连接或修改 YNNU，不能据此假定其当前 schema、用户数、release 或服务配置。

## 1. 先判断现状

在生产主机记录当前 `current`、wheel SHA、代码来源和服务状态。模型配置只由 root 在受保护位置读取；
不要把 YAML 全文、用户 home、请求正文、API 地址或密钥放进终端录屏和工单。

| 生产现状 | 升级路径 |
| --- | --- |
| schema v2、稳定会话 ID、用户配置均为稳定 ID，目录无旧别名字段 | 通过新版只读检查后，直接执行正式 cutover |
| schema v2，但仍有 `legacy_ids` / `legacy_routes` 或用户使用旧路由 | 先用下述独立过渡包完成 ID / 路由收尾，再部署新版 |
| 仍使用 mapping 的 `hermes.model` / `hermes.model_options` 或旧代理 `models` 格式 | 先部署过渡版并转换目录与 SQLite，再清理别名，最后部署新版 |
| 状态仍在源码树、明文全局密钥、可预测 token、缺少维护页或独立账号边界 | 先按主指南“一次性安全升级”完成相应前置工作，再进入模型升级 |

不要对已有站点执行新版 `--initialize-from`：它只适用于没有用户和目录的新安装。
不要仅把旧 `model_id` 改成上游模型名；SQLite 的选择值必须是稳定选项 ID，历史执行快照保持原样。

## 2. 准备两个相互独立的版本

准备最终新版的代码 staging、两次一致的 wheel 构建、经过 hash lock 核验的 wheelhouse 和 inactive release，
按主指南 6.1–6.6 操作。仅文档更新也可能改变 wheel 的 METADATA；复用运行时必须通过精确 verifier，
不能只比较版本号或 Python 源码 hash。

有旧格式或旧别名的站点还需要离线过渡包。旧迁移代码没有重新放回新版运行目录。
2026-10-08 已从本机升级前的部署代码保留独立归档，交付记录见本文末尾“过渡包记录”。
由运维通过获准渠道把该归档和校验文件送到生产维护目录；不要传输本机的 mapping、模型配置、
数据库、credentials 或用户 home。如果缺少归档或 checksum 不符，不能跳过迁移直接部署新版。

以下变量均是生产主机本地路径，`NEW_CODE` 指按主指南准备好的最终版 root-owned staging：

```bash
sudo -i
umask 077
UPGRADE_DIR=/root/potato-model-upgrade-20261008
test -d "$UPGRADE_DIR"
cd "$UPGRADE_DIR"
sha256sum -c potato-model-catalog-transition-20261008.tar.gz.sha256
test ! -e transition-source
test ! -e upgrade-support
tar -xzf potato-model-catalog-transition-20261008.tar.gz
TRANSITION_CODE=$UPGRADE_DIR/transition-source
NEW_CODE=/root/potato-agent-code-source
test -f "$NEW_CODE/configure_model_catalog.py"
test -f "$TRANSITION_CODE/configure_model_catalog.py"
test -f "$UPGRADE_DIR/upgrade-support/retire_model_routes.py"
/opt/interface-env/bin/python -B "$TRANSITION_CODE/configure_model_catalog.py" --help
```

确认过渡版 help 有 `--migrate` 和 `--session-db`；新版 help 则应有 `--check-session-db`、
`--check-user-configs`，没有 `--migrate`。不要混用两版的 Python 模块或 helper。
schema v2 站点通常只需离线运行过渡包命令，无须重新部署整个过渡版。
旧格式站点须先用过渡版自己的 cutover 和对应 Lite release 完成一次部署，再执行下一节；
最终版 cutover 会提前拒绝旧格式，不能承担这一步。

## 3. 旧格式及旧别名收尾

先在隔离环境使用生产数据的受控私密副本演练，记录变更数量及回退结果。
正式执行前完成独立私密备份：配置、SQLite 一致性快照和每个用户的 `config.yaml` / `.env`。
主指南 cutover 的 `sensitive-state` 备份不包含每用户配置文件，也不能代替迁移前备份。
后续 cutover 的备份发生在迁移之后，不能用于恢复迁移之前的目录和用户设置。

等待运行中的回合、审批和委派任务完成，冻结管理操作和新注册，进入维护模式。
保存原 active 服务清单并按主指南停止模型代理等写入方；确认没有 Gateway / slash worker 残留。
不要直接修改 live-state 表来绕过迁移的忙碌检查。服务恢复时仅恢复原 active 集合。

所有写入方停止后，用最终版的独立备份程序保存一致性数据库快照；目标目录必须是新目录：

```bash
PRE_MODEL_BACKUP=/var/backups/potato-agent/pre-model-catalog-$(date -u +%Y%m%dT%H%M%SZ)
test ! -e "$PRE_MODEL_BACKUP"
install -d -o root -g root -m 0700 "$PRE_MODEL_BACKUP"
export PRE_MODEL_BACKUP
/opt/interface-env/bin/python -B "$NEW_CODE/hermes-lite/scripts/backup_cutover_state.py" \
  --mapping /var/lib/potato-agent/config/users_mapping.yaml \
  --destination "$PRE_MODEL_BACKUP/sensitive-state" \
  --interface-db /var/lib/potato-agent/data/interface.db \
  --archive-db /var/lib/potato-agent/data/archive.db \
  --chat-shares-db /var/lib/potato-agent/data/chat_shares.db \
  --model-proxy-config /var/lib/potato-agent/config/model_proxy.yaml \
  --usage-db /var/lib/potato-agent/model-proxy/usage.db
test -f "$PRE_MODEL_BACKUP/sensitive-state.complete"
install -o root -g root -m 0600 \
  /var/lib/potato-agent/config/users_mapping.yaml "$PRE_MODEL_BACKUP/users_mapping.yaml"
PYTHONPATH="$TRANSITION_CODE" /opt/interface-env/bin/python -B - <<'PY'
import json
import os
from pathlib import Path
from interface.mapping import DEFAULT_MAPPING_PATH, MappingStore
from interface.user_private_files import read_user_private_text

root = Path(os.environ["PRE_MODEL_BACKUP"])
users = root / "user-configs"
users.mkdir(mode=0o700)
manifest = []
for index, target in enumerate(MappingStore(DEFAULT_MAPPING_PATH).load_targets()):
    for name in ("config.yaml", ".env"):
        source = target.hermes_home / name
        raw = read_user_private_text(target, source)
        if raw is None:
            if name == "config.yaml":
                raise RuntimeError("Required user config is missing")
            continue
        filename = f"{index:04d}-{name.lstrip('.')}"
        fd = os.open(users / filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        manifest.append({"backup": filename, "path": str(source), "linux_user": target.linux_user})
(users / "manifest.json").write_text(json.dumps(manifest))
(root / "user-configs.complete").write_text("complete\n")
print("User configuration backup complete:", len(manifest), "files")
PY
```

此处依赖前置安全升级已建立完整的独立数据库及用户状态边界；缺少数据库或权限不符时先修复对应前置条件，
不能创建空库来让备份通过。保留两个 complete marker 和私密 manifest。

先转换目录及会话选择；两条命令分别是预览和应用：

```bash
/opt/interface-env/bin/python -B "$TRANSITION_CODE/configure_model_catalog.py" \
  --mapping /var/lib/potato-agent/config/users_mapping.yaml \
  --proxy-config /var/lib/potato-agent/config/model_proxy.yaml \
  --session-db /var/lib/potato-agent/data/interface.db --migrate
/opt/interface-env/bin/python -B "$TRANSITION_CODE/configure_model_catalog.py" \
  --mapping /var/lib/potato-agent/config/users_mapping.yaml \
  --proxy-config /var/lib/potato-agent/config/model_proxy.yaml \
  --session-db /var/lib/potato-agent/data/interface.db --migrate --apply
```

schema v2 站点使用该命令时保留现有目录和签名密钥，继续完成尚未转换的 SQLite 选择 ID。
初次转换按原配置生成选项映射；管理员必须核对公开预览中的选项、实际模型、API 模式、推理强度和窗口，
不要假定生产模型必须与测试主机一致。`default_option_id` 应是 Fast，未配置 Fast 时才使用 primary。

再运行独立过渡包中的一次性路由收尾命令：

```bash
/opt/interface-env/bin/python -B "$UPGRADE_DIR/upgrade-support/retire_model_routes.py"
/opt/interface-env/bin/python -B "$UPGRADE_DIR/upgrade-support/retire_model_routes.py" --apply
```

它按目录的原始别名映射逐项替换用户模型引用，保留所选选项及其他设置，随后删除目录的旧别名字段。
写入前在 `/var/backups/potato-agent/model-route-retirement-*` 私密备份原文件，写入失败时尝试恢复。
输出只有选项 ID、数量和备份位置。它不会根据上游模型名猜测选项，也不会调整端点、凭据、签名密钥、
后端定义或对话选择。发现 mapping override、环境变量、未知引用或无法识别的配置时停止；
由维护人员在私密环境核查并修正这些明确引用后重跑，不能批量文本替换或删除报错文件。

成功后先执行最终新版的只读检查：

```bash
/opt/interface-env/bin/python -B "$NEW_CODE/configure_model_catalog.py" \
  --mapping /var/lib/potato-agent/config/users_mapping.yaml \
  --proxy-config /var/lib/potato-agent/config/model_proxy.yaml \
  --check-session-db /var/lib/potato-agent/data/interface.db --check-user-configs
```

还应复核所有用户配置为各自用户所有、`0600`，目录为 `root:potato-model-proxy 0640`，用户文件仅包含
本地代理地址和用户自己的代理 token。真实上游地址、密钥和签名密钥继续只在保护目录中保存。
先恢复原 active 服务并验证过渡版在规范化配置下可运行，再退出本次维护模式。
确认 `potato-maintenance.service` 为 inactive 后，才能开始最终版 cutover；该脚本不接受嵌套维护模式。

## 4. 最终切换及验收

按主指南 6.6 使用最终版 staging、已验证的 inactive release，以及从生产 mapping 重新读取的用户数量：

```bash
: "${RELEASE_ID:?set the verified inactive release on this production host}"
EXPECTED_USER_COUNT=$(
  /opt/interface-env/bin/python -c \
    'import yaml; print(len(yaml.safe_load(open("/var/lib/potato-agent/config/users_mapping.yaml"))["users"]))'
)
"$NEW_CODE/hermes-lite/scripts/cutover_lite_production.sh" \
  "$NEW_CODE" "$RELEASE_ID" "$EXPECTED_USER_COUNT"
```

切换后完整执行主指南 6.7，使用生产实际 HTTPS origin 重新登录；不要照抄测试服务器的 IP、
HTTP Cookie 设置、固定用户数或本机 release ID。补充验收项如下：

| 验收 | 预期结果 |
| --- | --- |
| `GET /api/models` | `default_id` 正确；显示目录标签；不含真实端点、密钥、签名路由或旧 `active_id` |
| 两个对话分别选择 Fast / Deep，刷新并重新登录 | 各自选择保留 |
| A 响应或等待审批，B 切换模型并发送 | B 正常响应，A 继续原模型；当前忙碌对话不能切换 |
| 新建、导入、分支、压缩续接及委派续答 | 默认、继承及冻结快照符合 [模型契约](../interface/MODEL_CATALOG.md) |
| 实际出站请求 | 在受保护 usage 记录中核对 `upstream_model`、`config_revision`；结合 mock-provider 回归核对推理强度/API 模式；不以模型自述作为唯一依据 |
| 旧接口与非法模型 | `/api/models/active` 已移除；旧路由、裸上游模型名、越权会话均不能用于新选择 |
| 保密边界 | 浏览器、用户配置及快照数据库中没有实际上游地址、凭据或签名密钥 |

## 5. 回退

区分两层回退。最终 cutover 失败时脚本自动恢复代码、unit、symlink 和原服务状态；
它不自动撤销之前的目录/用户配置迁移。人工回退到支持 schema v2 的过渡版通常可保留规范化配置；
回退到不支持 schema v2 的更早版本必须配套恢复迁移前配置和选择 ID，不能只改 `current`。

迁移失败时保持维护模式，先核实是否部分成功，按迁移前备份和一次性工具的 manifest 恢复配置，
再检查 owner/mode、目录签名密钥和 SQLite 选择。恢复用户文件必须使用映射用户权限及原子替换，
不能用 root 跟随用户可替换的 symlink。只有备份完整性、服务版本与配置一致才恢复入口。
若升级后已有新聊天或新消息，禁止直接覆盖整个旧数据库；需要制定保留新增记录的专项回退。

保留两次 cutover 的完整备份、迁移前独立备份、过渡包、最终 wheel/manifest、校验记录和验收截图，
直到生产负责人确认观察期结束。备份不应进入 Git 或普通可读目录。

## 过渡包记录

归档文件名：`potato-model-catalog-transition-20261008.tar.gz`。
包含升级前代码 `transition-source/` 和独立的一次性工具 `upgrade-support/retire_model_routes.py`，
不包含模型配置、数据库、用户数据或凭据。交付时必须一并提供 `.sha256` 文件，并在目标主机核验。
本机保存位置及最终 SHA256 记录在本次部署交付记录中；不能仅凭文件名认定归档可信。

本次本机归档位于：
`/var/tmp/potato-model-cleanup-20261008T133536Z-d9fm03gv/potato-model-catalog-transition-20261008.tar.gz`。
独立核对值为：

```text
b495e72e03b3cb8366ecfbad8cce9a57b6f7fd883268a9758906fd88cfe429cf
```

该本机路径不是生产服务器上的路径。归档应由管理员保存到受控制品库，并与生产交付的校验文件一并核对。
