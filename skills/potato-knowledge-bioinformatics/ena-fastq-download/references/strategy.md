# ENA FASTQ 下载策略

## 核心结论

批量下载一串 `SRR/ERR/DRR` 时，优先走：

1. 用 ENA Portal API 查询每个 run 的 `fastq_aspera / fastq_ftp / fastq_bytes`
2. 把结果展开成**每个实际 FASTQ 文件一行**的 manifest
3. 用 Aspera `ascp -k1` 批量下载
4. 对失败项单独重试
5. 最后核对完整性，并检查 `.partial` / `.aspera-ckpt`

对大批量项目，这通常比 `prefetch + fasterq-dump` 更省时、省磁盘中间开销，也更容易断点续传。

## 为什么这套更有效

### 1. 直接拿 FASTQ，跳过 `.sra`

`prefetch` 先下 `.sra`，再解包成 FASTQ：

- 多一步转换
- 需要更多中间磁盘空间
- 大项目整体更慢

而 ENA 直接给 FASTQ 链接，省掉了解包阶段。

### 2. 以“文件”为单位，而不是“run”为单位

manifest 必须是一行一个实际文件，而不是一行一个 run。

原因：
- 有些 run 是双端，返回 2 个文件
- 有些 run 虽然 `library_layout=PAIRED`，但 ENA 实际只返回 1 个 FASTQ
- 是否 `_1/_2`，要以 ENA 返回的实际链接数为准，不能靠名字或 layout 猜

### 3. 长任务用 `nohup + setsid + ... &` 挂后台

对于大批量 FASTQ 下载，任务常常会运行很久。
如果是在 OpenClaw 这类会管理前台执行超时的环境里启动，优先用 `nohup + setsid + ... &` 把下载脚本挂到后台，并把 stdout/stderr 统一重定向到总日志，避免前台执行超时后任务被杀掉。

推荐形式：

```bash
nohup setsid env PARALLEL=6 MAX_RETRIES=3 ASCP_RATE=500m \
  bash scripts/download_ena_fastq_aspera.sh \
  runs.tsv fastq_ena logs_ena_fastq logs_ena_fastq/ena_fastq_manifest.tsv \
  >> logs_ena_fastq/download_all_ena_aspera.nohup.log 2>&1 < /dev/null &
```

这样做的好处：
- 下载任务脱离当前前台会话
- 总日志集中到一个文件里，便于追踪
- 即使任务很长，也不容易因 OpenClaw 前台 exec 超时被中断

### 4. 用 per-run 目录组织输出

推荐输出结构：

```text
fastq_ena/
  SRRxxxx/
    SRRxxxx_1.fastq.gz
    SRRxxxx_2.fastq.gz
```

优点：
- 避免重名冲突
- 更容易定位失败项
- 后续按 run 清理或核对更简单

### 4. 失败重试时只重跑失败项

大批量第一次跑完后：
- 不要全量重下
- 从原始 manifest 里抽出失败文件对应的行
- 新建一个 retry manifest 单独跑
- 最好换一个新的 `LOG_DIR`，不要覆盖第一次总日志

### 5. 保留 partial/checkpoint，直到最终核对结束

`ascp -k1` 会利用 `.partial` 和 `.aspera-ckpt` 续传。
在确认最终文件齐全之前，不要先删这两类文件。

## 参数经验值

- 并发：`PARALLEL=4~8` 常常比较稳
- 速率：`ASCP_RATE=300m~800m` 按链路调
- 重试：`MAX_RETRIES=3~5`
- 大规模补下失败项时：先串行或低并发重试，成功率往往更高

## Aspera key 失效后的复盘与处置

本次问题的关键线索是：旧 Aspera Connect DSA key 对当前失败文件和历史成功文件都报 `failed to authenticate`，而 HTTPS 可以慢速下载，说明不是 ENA 文件不存在，优先怀疑 `ascp + key` 组合失效。排查时不要只沿用 `asperaweb_id_dsa.openssh`。

推荐流程：

1. **取两个测试源**：一个当前失败的 `aspera_source`，一个历史上确认成功过的 `aspera_source`；都用 1 MB 范围下载测试，避免误伤长任务。
2. **搜索候选 `ascp` 与 key**：除 `~/.aspera/connect` 和 `.aspera-connect-unpacked` 外，还要查 Aspera CLI/SDK 自动生成目录，例如 `~/.aspera/sdk/`、`~/.aspera/ascli/sdk/`、项目内 `.aspera-sdk/`、项目内伪 HOME 如 `.home-ruby/.aspera/sdk/`。重点文件名：`ascp`、`aspera_bypass_rsa.pem`、`aspera_bypass_dsa.pem`、`asperaweb_id_dsa.openssh`。
3. **矩阵验证**：对每个候选 `ascp × key` 组合运行：
   ```bash
   TESTDIR=$(mktemp -d); trap 'rm -rf "$TESTDIR"' EXIT
   "$ASCP_BIN" -Q -P 33001 -i "$ASCP_KEY" -k1 -l 100m -T \
     -@ 0:1048575 "$ASPERA_SRC" "$TESTDIR/"
   stat -c '%n\t%s' "$TESTDIR"/*
   ```
   成功标准是返回码为 0、日志含 `Completed`、输出文件约 1 MB。不要打印或保存私钥内容；如需检查 key，只看公钥指纹。
4. **本次可用组合来源**：OpenClaw/Aspera CLI 自动配置留下了 SDK 版 `ascp` 与 RSA bypass key；Connect DSA key 认证失败，但 SDK `ascp` + `aspera_bypass_rsa.pem` 成功。找到后应在脚本/Slurm wrapper 中显式设置 `ASCP_BIN` / `ASCP_KEY`，防止自动探测选回旧 key。
5. **若本机没有可用 key/SDK**：安装或重建 Aspera SDK。首选 `ascli config ascp install`，它会下载 IBM Aspera Transfer SDK，并在默认 SDK 目录落地 `ascp`、`aspera-license` 与标准 bypass key。离线环境可先下载 SDK zip，再运行 `ascli config ascp install --sdk-url=file:///ABS/PATH/sdk.zip`。如果 `ascli` 缺 Ruby 依赖（如 `webrick`），先在对应 Ruby/gem 环境补装依赖或重建 aspera-cli 环境。若 SDK 不可用，再安装 IBM Aspera Connect Client，但仍必须用 1 MB 范围测试验证其 key。

## 典型补下载流程

```bash
awk 'BEGIN{FS=OFS="\t"} NR==1 || $4=="SRR15560106_2.fastq.gz" || $4=="SRR15560159.fastq.gz"' \
  logs_ena_fastq/ena_fastq_manifest.tsv > retry_manifest.tsv

PARALLEL=1 MAX_RETRIES=5 RETRY_SLEEP=20 \
  scripts/download_ena_fastq_aspera.sh data_list.tsv fastq_ena logs_retry retry_manifest.tsv
```

## 什么时候不用这套

- 用户只要少量 run，且不在乎速度：直接 HTTPS 也行
- ENA 没有 FASTQ，只能拿到 SRA：再考虑 `prefetch/fasterq-dump`
- 环境完全没有 Aspera：可退回 HTTPS/FTP，但默认仍优先 Aspera
