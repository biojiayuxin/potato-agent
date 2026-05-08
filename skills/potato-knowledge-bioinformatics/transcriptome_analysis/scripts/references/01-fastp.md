# fastp 质控参数参考

## 功能
双端测序数据质量控制：去除接头、低质量序列、N 过高序列

## 命令模板

```bash
timeout --kill-after={FASTP_KILL_AFTER:-5m} {FASTP_TIMEOUT:-3h} \
  fastp -i {INPUT_R1} -o {OUTPUT_R1} \
        -I {INPUT_R2} -O {OUTPUT_R2} \
        -j {OUTPUT_JSON} \
        -h {OUTPUT_HTML} \
        -w {THREADS} \
        {FASTP_EXTRA}
```

## 参数说明

| 参数 | 说明 | 必需 |
|------|------|------|
| `-i` | 输入 R1 文件 | ✅ |
| `-o` | 输出 R1 文件 | ✅ |
| `-I` | 输入 R2 文件 | ✅ |
| `-O` | 输出 R2 文件 | ✅ |
| `-j` | JSON 报告输出路径 | ✅ |
| `-h` | HTML 报告输出路径 | ✅ |
| `-w` | 线程数 | 推荐 |
| `timeout --kill-after` | 外层 GNU timeout 防止 fastp 假死；推荐默认 `3h` + `5m` 强杀窗口 | 推荐 |
| `-q` | 低质量碱基阈值（默认 15） | 可选 |
| `-l` | 最小序列长度（默认 30） | 可选 |
| `--detect_adapter_for_pe` | 自动检测双端接头 | 可选 |
| `--cut_tail` | 切除尾部低质量 | 可选 |

## 输出文件

- `{sample}_R1.fq.gz` - 质控后 R1
- `{sample}_R2.fq.gz` - 质控后 R2
- `{sample}.json` - JSON 格式报告
- `{sample}.html` - HTML 格式报告

## 批量处理示例

```bash
INPUT_DIR="<原始数据目录>"
OUTPUT_DIR="./00-fastp"
THREADS=8
FASTP_TIMEOUT="${FASTP_TIMEOUT:-3h}"
FASTP_KILL_AFTER="${FASTP_KILL_AFTER:-5m}"

mkdir -p "$OUTPUT_DIR"

for R1 in "$INPUT_DIR"/*_R1.fq.gz; do
    sample=$(basename "$R1" | sed 's/_R1\.fq\.gz//')
    R2="$INPUT_DIR/${sample}_R2.fq.gz"
    
    timeout --kill-after="$FASTP_KILL_AFTER" "$FASTP_TIMEOUT" \
      fastp -i "$R1" -o "$OUTPUT_DIR/${sample}_R1.fq.gz" \
            -I "$R2" -O "$OUTPUT_DIR/${sample}_R2.fq.gz" \
            -j "$OUTPUT_DIR/${sample}.json" \
            -h "$OUTPUT_DIR/${sample}.html" \
            -w "$THREADS"
done
```

## 注意事项

- 默认启用接头检测和去除
- 默认启用质量修剪
- 输出文件自动 gzip 压缩
- 建议在 Snakemake `rule fastp` 中使用 GNU `timeout` 包装 fastp，避免偶发假死长期占用后台任务。超时退出会使 Snakemake 判定该 rule 失败并清理不完整输出，后续可断点重跑。
- 若 fastp 日志为空、clean FASTQ 已写出但 `.json/.html` 未生成且进程长时间不退出，应优先按“fastp 假死”处理；但仍需先对原始 FASTQ 做 `gzip -t` 或 MD5 校验，排除输入损坏。
