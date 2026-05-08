---
name: ena-fastq-download
description: 高效批量下载 ENA/SRA 的 SRR/ERR/DRR FASTQ 文件。使用 ENA Portal API 先解析 run 对应的实际 fastq_aspera/fastq_ftp 链接，再用 Aspera 批量下载、断点续传、失败项重试和最终完整性核对。适用于用户要下载一批 SRR 文件、GEO/SRA 项目 run 列表、NCBI prefetch 太慢、需要直接拿 FASTQ 而不是 .sra、或需要补下失败文件并验证 100% 完整性的场景。
---

# ENA FASTQ Download

## 概览

默认采用这条路径：**run 列表 → ENA manifest → Aspera 批量下载 → 失败项单独重试 → 完整性核对**。

对大批量 SRR 下载，优先这条路线，不要默认走 `prefetch + fasterq-dump`。

需要原理、参数经验值、失败项补下载策略时，再读 `references/strategy.md`。

## 输入要求

优先准备一个包含 `Run` 列的 TSV/CSV。可选带 `SampleName` 列。

也支持简单文本：
- 每行一个 `SRR/ERR/DRR`
- 或每行两列：`run<TAB>sample_name`

## 工作流

### 1. 生成 manifest

运行：

```bash
python3 scripts/build_ena_fastq_manifest.py runs.tsv logs_ena_fastq/ena_fastq_manifest.tsv
```

manifest 会展开为**每个实际 FASTQ 文件一行**，字段包括：
- `run_accession`
- `sample_name`
- `mate`
- `filename`
- `aspera_source`
- `ftp_url`
- `bytes`
- `layout`

不要根据 paired/single 自己猜文件数；以 ENA 返回的实际文件行为准。

### 2. 批量下载

运行：

```bash
PARALLEL=6 MAX_RETRIES=3 ASCP_RATE=500m \
  bash scripts/download_ena_fastq_aspera.sh \
  runs.tsv fastq_ena logs_ena_fastq
```

要点：
- 输出按 run 分目录保存
- 使用 `ascp -k1`，支持断点续传
- 默认记录：
  - `downloaded_files.txt`
  - `failed_files.txt`
  - 每个 FASTQ 的单独日志
- 如果已有 manifest，可把它作为第 4 个参数传入，避免重复查询 ENA
- **对大批量、长时间下载任务，启动时优先使用 `nohup + setsid + ... &` 挂到后台，并把 stdout/stderr 重定向到总日志**，避免任务因 OpenClaw 前台执行超时而被杀掉

推荐形式：

```bash
nohup setsid env PARALLEL=6 MAX_RETRIES=3 ASCP_RATE=500m \
  bash scripts/download_ena_fastq_aspera.sh \
  runs.tsv fastq_ena logs_ena_fastq logs_ena_fastq/ena_fastq_manifest.tsv \
  >> logs_ena_fastq/download_all_ena_aspera.nohup.log 2>&1 < /dev/null &
```

如本机没有自动识别到 Aspera，可显式设置：

```bash
ASCP_BIN=/path/to/ascp
ASCP_KEY=/path/to/aspera_bypass_rsa.pem   # 优先 SDK RSA bypass key；Connect DSA key 仅作为候选
```

#### Aspera key 失效时的定位与重新获取

触发条件：`ascp` 日志出现 `Password: Password: ascp: failed to authenticate, exiting.`、同一 key 对当前失败文件和历史成功文件都认证失败、或脚本反复回退 HTTPS。此时不要只判断为网络慢，应优先排查 key/ascp 组合。

处理顺序：

1. **确认旧 key 真的不可用**：从 retry manifest 取一个当前失败的 `aspera_source`，再取一个历史上成功过的 `aspera_source`，分别做 1 MB 范围测试：
   ```bash
   TESTDIR=$(mktemp -d); trap 'rm -rf "$TESTDIR"' EXIT
   "$ASCP_BIN" -Q -P 33001 -i "$ASCP_KEY" -k1 -l 100m -T \
     -@ 0:1048575 "$ASPERA_SRC" "$TESTDIR/"
   stat -c '%n\t%s' "$TESTDIR"/*
   ```
   成功标准：日志含 `Completed`，测试文件大小约为 1 MB。不要输出私钥内容。
2. **在本机搜索候选组合**：优先找 SDK 版 `ascp` 与 SDK bypass key，再找 Connect DSA key。Hermes 中优先用 `search_files(target='files')` 搜索这些模式：
   - `*.aspera-sdk*/ascp`、`*/.aspera/sdk/ascp`、`*/.aspera/ascli/sdk/ascp`、`*/.aspera/connect/bin/ascp`
   - `aspera_bypass_rsa.pem`、`aspera_bypass_dsa.pem`、`asperaweb_id_dsa.openssh`、`aspera_web_key.pem`
   - OpenClaw/脚本化环境可能把 `HOME` 指到项目内临时目录，需额外检查如 `.home-ruby/.aspera/sdk/` 这类本地 HOME 目录。
3. **做 `ascp × key` 矩阵测试**：每个候选组合只拉 1 MB，记录 `ascp -A` 版本、key 路径、公钥指纹/能否解析、返回码、测试文件大小；不要读取、打印、复制或写入私钥内容。若必须复制 key 排查权限，复制到临时目录并 `chmod 600`，测试后立即删除。
4. **优先采用验证成功的 SDK RSA 组合**：实际案例中，旧 Connect `asperaweb_id_dsa.openssh` 对 ENA 认证失败，但 Aspera SDK `ascp` + `aspera_bypass_rsa.pem` 对 `era-fasp@fasp.sra.ebi.ac.uk` 范围下载成功。
5. **若本机没有可用 key，则重新生成/获取 SDK key**：
   - 首选使用 `ascli config ascp install` 安装 IBM Aspera Transfer SDK；它会在默认 SDK 目录生成/落地 `ascp`、`aspera-license` 以及标准 bypass key（常见为 `~/.aspera/sdk/aspera_bypass_rsa.pem`）。
   - 若不能联网，可先用 `ascli --show-config --fields=sdk_url` 查看 SDK URL，或从 IBM SDK location 页面获取 SDK zip，然后执行：`ascli config ascp install --sdk-url=file:///ABS/PATH/sdk.zip`。
   - 若 `ascli` 因 Ruby 依赖报错（例如缺 `webrick`），在同一 Ruby/gem 环境补装依赖（如 `gem install webrick`）或重建 aspera-cli 环境后再执行 install。
   - 如果 SDK 路线不可用，再安装 IBM Aspera Connect Client；但 Connect 自带 DSA key 只能作为备选，必须经过 1 MB 范围测试后才能用于批量下载。
6. **固定可用组合**：一旦找到可用组合，在下载脚本或 Slurm wrapper 中显式设置 `ASCP_BIN` / `ASCP_KEY`，避免下次自动探测又选回失效 key。

### 3. 只重试失败项

第一次批量完成后，如果还有少量失败：

1. 从原始 manifest 里筛出失败文件对应的行
2. 生成一个小的 retry manifest
3. 用新的 `LOG_DIR` 单独重跑
4. 失败项重试时优先低并发，必要时串行

示例：

```bash
awk 'BEGIN{FS=OFS="\t"} NR==1 || $4=="SRR15560106_2.fastq.gz" || $4=="SRR15560159.fastq.gz"' \
  logs_ena_fastq/ena_fastq_manifest.tsv > logs_retry/retry_manifest.tsv

PARALLEL=1 MAX_RETRIES=5 RETRY_SLEEP=20 \
  bash scripts/download_ena_fastq_aspera.sh \
  runs.tsv fastq_ena logs_retry logs_retry/retry_manifest.tsv
```

不要为了几个失败项重新全量下载。

#### Slurm 后台补下载模式（少量异常文件）

当用户要求“不要卡在前台、扔到 Slurm 后台下载”时，推荐生成一个专用 retry 目录，例如 `logs_ena_fastq_retry_missing5_slurm/`，包含：

- `missing_retry_manifest.tsv`：只保留失败/缺失/大小不匹配的 FASTQ 行；
- `download_missing_fastq.sh`：真实下载脚本；
- `verify_missing.py`：只校验 retry manifest 中的文件；
- `download_missing_fastq.slurm.sh`：Slurm wrapper；
- `slurm-%j.out/.err` 与主日志。

脚本要点：

1. **以 manifest 的 `bytes` 为准**，不要只用 `-s final_file` 判断完成；已存在但大小不匹配的 final FASTQ 要视为异常。
2. 对大小不匹配的 final FASTQ，真实运行时先移动为 `*.bad.TIMESTAMP` 再重下，避免下载脚本误判完成；但 `DRY_RUN=1` 必须无副作用，只报告“would move”，不能真的移动文件。若 dry-run 会触发真实移动，必须立即修复脚本并恢复文件。
3. 优先尝试 Aspera；若 Aspera 不可用或失败，自动切换 HTTPS/curl 断点续传：
   ```bash
   curl -L --fail --retry 10 --retry-delay 30 --retry-all-errors \
     --connect-timeout 60 --speed-limit 1024 --speed-time 600 \
     -C - -o "$tmp_file" "$ftp_url"
   ```
   HTTPS 完成后必须比较临时文件大小与 manifest bytes，一致才 `mv` 为最终 FASTQ。
4. Slurm 提交前做：`bash -n`、`python3 -m py_compile verify_missing.py`、`DRY_RUN=1 bash download_missing_fastq.sh`、`submit-job.sh --print-only ...`。
4. 提交 Slurm 时优先使用 `slurm-user-jobs/scripts/submit-job.sh --script ...`，不要用复杂 `--command`；提交后用 `list-jobs.sh` 和 `job-status.sh JOBID` 确认 `RUNNING/PENDING`，并汇报日志路径。
5. 在重跑补下载前，先把旧的 retry 目录日志和状态文件归档一份；许多补下载脚本会在启动时清空/重建 `downloaded_files.txt`、`failed_files.txt` 和主日志，直接复跑会丢失上一次 job 的诊断痕迹。
6. 判断 Slurm 补下载是否完成时，不要只看 `downloaded_files.txt`/`failed_files.txt` 的行数；某些并发/包装脚本场景下这些汇总文件可能为空，但 `status/*.done`、主日志 `[OK]`/`[DONE]`、Slurm stderr、manifest 文件大小核对才是更可靠依据。最小核对组合：`list-jobs.sh` 确认任务已离队；统计 `status/*.done` 与 retry manifest 行数一致；`status/*.fail` 为 0；按 manifest `bytes` 逐行检查目标 FASTQ 存在且大小一致；检查没有 `.partial`/`.aspera-ckpt` 残留。
7. 如果本目录找不到 `ascp` 或 key，可搜索旧下载目录中已解包/安装的 Aspera 组件。不要只找 Connect DSA key，优先找 SDK 版 `ascp` + SDK bypass key：
   - `.../.aspera-sdk/ascp`
   - `.../.home-ruby/.aspera/sdk/aspera_bypass_rsa.pem`
   - `~/.aspera/sdk/aspera_bypass_rsa.pem`
   - `~/.aspera/ascli/sdk/aspera_bypass_rsa.pem`
   - `.../.aspera-connect-unpacked/bin/ascp`
   - `.../.aspera-connect-unpacked/etc/asperaweb_id_dsa.openssh`
   但所有候选组合都必须先做 1 MB 范围下载验证；仍需在脚本中保留 HTTPS/curl fallback。
7. Aspera 失败排查不要直接猜测网络问题。对一个目标文件做独立小范围测试，且不要影响正在运行的 Slurm job：
   - 从 retry manifest 取一条 `aspera_source`，用 `ascp -P 33001 -i KEY -k1 -l 100m -T -@ 0:1048575 SRC TESTDIR/` 只拉取小范围；
   - 同时用历史上成功过的 manifest 文件再测一次，以区分“目标文件问题”和“全局 Aspera 配置/认证问题”；
   - 若日志为 `Password: Password: ascp: failed to authenticate, exiting.`，优先判断为 key/认证配置问题，而不是 HTTPS 慢或文件不存在；历史日志若曾出现 `Partial Completion` / `.aspera-ckpt`，说明当时 Aspera 至少能认证并传输，当前失败可能是 key/安装来源变化或服务端认证策略变化；
   - 不要只测试传统 Connect DSA key（如 `asperaweb_id_dsa.openssh`）。OpenClaw/Aspera CLI 自动配置环境可能同时留下 SDK 版 `ascp` 与 RSA bypass key；例如可搜索 `.aspera-sdk/ascp`、`.home-ruby/.aspera/sdk/aspera_bypass_rsa.pem`、`aspera_bypass_dsa.pem`、`aspera_web_key.pem` 等候选。实际案例中 Connect DSA key 认证失败，但 SDK `ascp` + `aspera_bypass_rsa.pem` 可成功对 ENA `era-fasp@fasp.sra.ebi.ac.uk` 做范围下载。
   - 对多个候选组合做矩阵测试：`ascp` 候选 × key 候选 × 当前失败源/历史成功源。只下载 1 MB 范围，记录状态、耗时、文件大小和公钥指纹即可；不要输出私钥内容。成功标准是 `Completed` 且测试输出文件大小约等于请求范围。
   - 可把 key 复制到临时目录并 `chmod 600` 后测试，排除权限过宽问题；可用 `ssh-keygen -y/-lf` 输出公钥指纹验证 key 是否可解析，但不要输出或保存私钥内容；
   - 测试目录中若复制过 key，测试结束后必须删除，避免留下额外私钥副本。
8. 注意 Bash 陷阱：不要用 `if [[ "$(prepare_target ...)" == complete ]]` 包住会写日志/移动文件的函数；命令替换会吞掉 stdout 日志，并且可能隐藏副作用。让函数用 return code 表示是否已完成：`if prepare_target ...; then ... fi`。
9. 对 MD5 mismatch 文件做补下载脚本时，`DRY_RUN=1` 不应重新计算所有大 FASTQ 的 MD5；否则 dry-run 也会被数百 GB I/O 拖慢甚至超时。dry-run 只检查文件存在/大小并打印“would move / would download”，必要时把“大小一致但已知 MD5 异常”的文件视为需重下即可。
10. 若并发下载用 `python manifest_to_nul | xargs -0 -n N -P P bash -c 'download_one "$@"' _`，在 `set -euo pipefail` 脚本中应临时 `set +e` 包住该 pipeline，保存 `xargs_rc` 后再 `set -e`；否则任一文件失败会使主脚本提前退出，跳过最终 verify/summary。真实运行仍应在下载阶段后统一执行 retry manifest 的 size+MD5 验证并以验证结果作为最终退出码。
11. 1 MB Aspera 连通性测试脚本若用 Python 从环境变量读取 `MANIFEST`，必须先 `export MANIFEST=...`；普通 shell 变量不会传给 Python 子进程。

### 4. 最终核对

运行：

```bash
python3 scripts/verify_ena_fastq_download.py \
  logs_ena_fastq/ena_fastq_manifest.tsv fastq_ena --json
```

重点检查：
- `expected_count == existing_count`
- `missing_count == 0`
- `partial_count == 0`
- `ckpt_count == 0`

如果需要把“不完整下载”作为失败条件，添加 `--strict`。

#### 用 ENA MD5 复核 FASTQ 完整性

当 manifest 或 ENA Portal API 可提供 `fastq_md5` 时，优先用 MD5 作为最终完整性依据；文件大小等于 `fastq_bytes` 只能说明字节数一致，不能排除内容错误。`fastp` 成功输出也不能证明原始 FASTQ 完整，fastp 可能容忍尾部 CRC/FASTQ 格式异常并以 0 退出。

若本地 manifest 没有 MD5，可重新查询 ENA Portal API，字段至少包括：

```text
run_accession,fastq_aspera,fastq_ftp,fastq_bytes,fastq_md5,library_layout
```

然后生成文件级 manifest，推荐字段：

```text
run_accession sample_name mate filename aspera_source ftp_url bytes md5 layout
```

大批量核查建议生成独立目录，例如：

```text
00-md5-check/
├── scripts/
│   ├── check_fastq_md5.py
│   ├── run_md5_check.sh
│   └── md5_check.slurm.sh
└── latest -> results_YYYYMMDD_HHMMSS
```

实现要点：
1. 以含 `md5` 的 ENA 文件级 manifest 为准逐行核查，不要重新扫描目录猜测文件。
2. 并发计算本地 FASTQ 压缩文件的 MD5，输出：
   - `fastq_md5_results.tsv`：全部文件结果；
   - `fastq_md5_failed.tsv`：`MISSING` / `SIZE_MISMATCH` / `MD5_MISMATCH` 等异常行；
   - `bad_fastq_filenames.txt`：仅问题文件名，一行一个，便于人工查看；
   - `retry_md5_failed_manifest.tsv`：保留原 manifest 字段、仅包含异常 FASTQ，可直接用于后续 Aspera 补下载；
   - `fastq_md5_summary.json` 与 `fastq_md5_progress.log`。
3. `md5sum` 读取压缩字节流，不解压，通常比 `gzip -t` 更适合用 ENA 校验值做最终判定；但仍会受磁盘 I/O 限制，大数据集应走 Slurm/后台。
4. 若 gzip 检查失败清单与 MD5 mismatch 清单不一致，应以 MD5 mismatch/缺失/大小不符作为重下载清单，并保留两者交叉表便于排查。
5. 重下载后必须再次对 retry manifest 或全量 manifest 做 MD5 核查；下游 RNA-seq 质控/比对前，应删除或强制重跑受异常原始 FASTQ 影响的 fastp 输出，避免复用旧 clean FASTQ。

### 5. 检查历史下载任务是否已经完成

当用户询问“下载任务是否完成”，尤其是提交目录和实际运行目录可能不一致时，按下面顺序核对，不要只看一个日志：

1. 检查提交/计划目录中是否有执行计划、run 清单、补下载说明，确认预期 run 数与异常样本记录。
2. 在原始数据目录查找：`fastq_ena/`、`logs_ena_fastq*/`、`data_list*.txt`、`*manifest*.tsv`、`downloaded_files.txt`、`failed_files.txt`、`*.partial`、`*.aspera-ckpt`。
3. 以 `logs_ena_fastq/ena_fastq_manifest.tsv` 为准逐行核对：
   - manifest 预期 FASTQ 文件数与 run 数；
   - 每个预期文件是否存在；
   - 已存在文件大小是否等于 manifest 的 `bytes`；
   - 是否有 0 字节文件；
   - 是否残留 `.partial` / `.aspera-ckpt`。
4. 汇总 run 级别状态：paired-end run 只要缺失任一 mate 或任一 mate 大小不匹配，就标记为不完整；不要把缺失 mate 的 paired-end run 当作 single-end 数据。
5. 查看主日志和补下载日志：
   - 主日志如出现 `[DONE]` 只表示脚本结束，不代表全部成功；仍需看 `failed_files.txt` 和 manifest 完整性核对；
   - 补下载目录可能只有 retry manifest 或单个 HTTPS/curl 日志，必须确认是否有成功完成记录。
6. 同时检查是否仍在运行：
   - Slurm 队列用 `slurm-user-jobs` 技能的 `scripts/list-jobs.sh`；
   - 当前用户进程可查 `download_ena_fastq|ascp|curl|wget|prefetch|fasterq|<任务目录名>`。
7. 汇报时给出：预期文件数/已存在文件数/缺失数/大小不匹配数/partial 或 ckpt 数、未完成 run 清单、是否仍有进程在跑、是否可进入下游分析。

## 资源

### scripts/
- `scripts/build_ena_fastq_manifest.py`：从 run 列表查询 ENA，生成文件级 manifest
- `scripts/download_ena_fastq_aspera.sh`：按 manifest 用 Aspera 批量下载
- `scripts/verify_ena_fastq_download.py`：核对完整性、统计大小、检查 `.partial/.aspera-ckpt`

### references/
- `references/strategy.md`：为什么优先 ENA + Aspera、参数经验值、补下载策略、适用边界
