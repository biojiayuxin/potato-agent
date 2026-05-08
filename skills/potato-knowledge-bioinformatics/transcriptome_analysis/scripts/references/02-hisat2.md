# hisat2 索引构建与比对参数参考

## 功能
- 构建参考基因组索引（hisat2-build）
- 将 reads 比对到参考基因组，生成排序 BAM 文件

## 步骤 0：构建索引

```bash
hisat2-build -p {THREADS} {GENOME_FASTA} {INDEX_PREFIX}
```

### 参数说明

| 参数 | 说明 | 必需 |
|------|------|------|
| `-p` | 线程数 | 推荐 |
| GENOME_FASTA | 参考基因组 FASTA 文件 | ✅ |
| INDEX_PREFIX | 索引前缀（不含 .ht2 后缀） | ✅ |

### 输出

- `{prefix}.1.ht2` ~ `{prefix}.N.ht2`（数量取决于基因组大小）

---

## 步骤 1：hisat2 比对

```bash
hisat2 -p {THREADS} \
       -x {HISAT2_INDEX} \
       -1 {R1} \
       -2 {R2} \
       -S {OUTPUT_SAM}
```

### 参数说明

| 参数 | 说明 | 必需 |
|------|------|------|
| `-p` | 线程数 | 推荐 |
| `-x` | hisat2 索引前缀（不含 .ht2 后缀） | ✅ |
| `-1` | R1 输入文件 | ✅ |
| `-2` | R2 输入文件 | ✅ |
| `-S` | SAM 输出文件 | ✅ |
| `--rna-strandness` | 链特异性（如 RF 或 FR） | 可选 |
| `--dta` | 为 StringTie 转录本组装优化 | 可选 |

## 步骤 2：SAM 转 BAM

```bash
samtools view -@ {THREADS} -bS {INPUT_SAM} -o {OUTPUT_BAM}
```

### 参数说明

| 参数 | 说明 |
|------|------|
| `-@` | 线程数 |
| `-b` | 输出 BAM 格式 |
| `-S` | 输入为 SAM 格式 |

## 步骤 3：BAM 排序

```bash
samtools sort -@ {THREADS} {INPUT_BAM} -o {OUTPUT_SORT_BAM}
```

### 参数说明

| 参数 | 说明 |
|------|------|
| `-@` | 线程数 |
| `-o` | 输出文件路径 |

## 步骤 4：建立索引（可选）

```bash
samtools index {SORT_BAM} {SORT_BAM}.bai
```

## 批量处理示例

```bash
INPUT_DIR="./00-fastp"
OUTPUT_DIR="./01-hisat2"
HISAT2_INDEX="<hisat2索引前缀>"
THREADS=8

mkdir -p "$OUTPUT_DIR"

for R1 in "$INPUT_DIR"/*_R1.fq.gz; do
    sample=$(basename "$R1" | sed 's/_R1\.fq\.gz//')
    R2="$INPUT_DIR/${sample}_R2.fq.gz"
    
    # 1. hisat2 比对
    hisat2 -p "$THREADS" \
           -x "$HISAT2_INDEX" \
           -1 "$R1" \
           -2 "$R2" \
           -S "$OUTPUT_DIR/${sample}.sam"
    
    # 2. SAM 转 BAM
    samtools view -@ "$THREADS" -bS "$OUTPUT_DIR/${sample}.sam" \
        -o "$OUTPUT_DIR/${sample}.bam"
    
    # 3. BAM 排序
    samtools sort -@ "$THREADS" "$OUTPUT_DIR/${sample}.bam" \
        -o "$OUTPUT_DIR/${sample}_hisat.sort.bam"
    
    # 4. 建立索引
    samtools index "$OUTPUT_DIR/${sample}_hisat.sort.bam"
    
    # 5. 清理中间文件
    rm -f "$OUTPUT_DIR/${sample}.sam" "$OUTPUT_DIR/${sample}.bam"
done
```

## 输出文件

- `{sample}_hisat.sort.bam` - 排序后的 BAM 文件
- `{sample}_hisat.sort.bam.bai` - BAM 索引

## 注意事项

- hisat2 索引文件命名格式：`{prefix}.1.ht2`, `{prefix}.2.ht2`, ...
- `-x` 参数只需提供前缀，不含 `.ht2` 后缀
- 中间文件（SAM、未排序 BAM）可删除以节省空间

---

## 可选参数

| 参数 | 说明 | 适用场景 |
|------|------|----------|
| `--rna-strandness RF` | 链特异性测序 | 链特异性 RNA-seq |
| `--dta` | 为 StringTie 转录本组装优化 | 新转录本发现 |

使用方式：在 Snakefile 的 `hisat2_align` 规则中添加到 hisat2 命令。
