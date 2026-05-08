# stringtie 定量参数参考

## 功能
基于比对结果进行转录本组装和基因表达量定量

## 命令模板

```bash
stringtie {INPUT_BAM} \
    -G {GFF_FILE} \
    -p {THREADS} \
    -o {OUTPUT_GTF} \
    -A {OUTPUT_ABUNDANCE} \
    -B -e
```

## 参数说明

| 参数 | 说明 | 必需 |
|------|------|------|
| 输入 BAM | 排序后的 BAM 文件 | ✅ |
| `-G` | 参考注释文件（GFF/GTF 格式） | ✅ |
| `-p` | 线程数 | 推荐 |
| `-o` | 输出 GTF 文件路径 | ✅ |
| `-A` | 基因丰度输出文件 | 推荐 |
| `-B` | 生成 Ballgown 格式文件 | 推荐 |
| `-e` | 仅定量参考注释中的转录本 | 推荐 |
| `-l` | 输出转录本名称前缀 | 可选 |

## 关键参数解释

### `-B` 参数
生成 Ballgown 格式文件，用于差异表达分析：
- `e2t.ctab` - 外显子-转录本关系
- `e_data.ctab` - 外显子表达数据
- `i2t.ctab` - 内含子-转录本关系
- `i_data.ctab` - 内含子表达数据
- `t_data.ctab` - 转录本表达数据

### `-e` 参数
限制定量仅针对参考注释中的转录本，提高定量准确性和一致性。

## 输出文件

- `{sample}.gtf` - 转录本组装结果
- `{sample}_stringtie.txt` 或 `{sample}_gene_abundance.txt` - 基因丰度表
- Ballgown 格式文件（当使用 `-B` 时）

## 基因丰度表格式

```
Gene ID     Transcript ID   Cov     FPKM    TPM
gene1       transcript1     10.5    1.23    0.45
gene2       transcript2     5.2     0.56    0.21
```

列说明：
- 第 1 列：基因 ID
- 第 2 列：转录本 ID
- 第 7 列：FPKM 值
- 第 8 列：TPM 值

## 批量处理示例

```bash
INPUT_DIR="./01-hisat2"
OUTPUT_DIR="./02-stringtie"
GFF_FILE="<参考注释GFF路径>"
THREADS=8

mkdir -p "$OUTPUT_DIR"

for BAM in "$INPUT_DIR"/*_hisat.sort.bam; do
    sample=$(basename "$BAM" | sed 's/_hisat\.sort\.bam//')
    sample_outdir="$OUTPUT_DIR/${sample}_stringtie"
    mkdir -p "$sample_outdir"
    
    stringtie "$BAM" \
        -G "$GFF_FILE" \
        -p "$THREADS" \
        -o "$sample_outdir/${sample}.gtf" \
        -A "$sample_outdir/${sample}_gene_abundance.txt" \
        -B -e
done
```

## 注意事项

- 输入 BAM 必须是排序后的
- `-e` 参数需要配合 `-G` 使用
- 每个样本生成独立的输出目录
- 后续使用 prepDE.py3 生成 count 矩阵
