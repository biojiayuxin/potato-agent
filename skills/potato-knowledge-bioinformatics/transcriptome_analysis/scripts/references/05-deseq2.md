# DESeq2 差异分析参数参考

## 功能
基于 count 矩阵进行差异表达基因分析

## 输入文件要求

### 1. count 矩阵 (gene_count_matrix.csv)
```
gene_id,CK1,CK2,Treat1,Treat2
gene1,150,120,180,200
gene2,0,5,2,3
gene3,500,480,520,550
```

### 2. 分组文件 (groups.txt)
```
sample_name	group
CK1	CK
CK2	CK
Treat1	Treat
Treat2	Treat
```

**注意**：
- 分组文件的样本名必须与 count 矩阵列名一致
- 列分隔符为制表符 `\t`
- 分组列名默认为 `group` 或 `condition`

## R 代码模板

```r
suppressPackageStartupMessages({
  library(DESeq2)
})

# 参数设置
CONTROL_GROUP <- "CK"      # 对照组名称
TREAT_GROUP <- "Treat"     # 处理组名称

# 读取数据
count_data <- read.csv("gene_count_matrix.csv", row.names = 1, check.names = FALSE)
group_info <- read.table("groups.txt", header = TRUE, row.names = 1, sep = "\t")

# 对齐样本顺序
common_samples <- intersect(colnames(count_data), rownames(group_info))
count_data <- count_data[, common_samples]
group_info <- group_info[common_samples, , drop = FALSE]

# 构建 DESeq2 对象
dds <- DESeqDataSetFromMatrix(
  countData = count_data,
  colData = group_info,
  design = ~ group
)

# 过滤低表达基因
keep <- rowSums(counts(dds)) >= 10
dds <- dds[keep, ]

# 差异分析
dds <- DESeq(dds)
res <- results(dds, contrast = c("group", TREAT_GROUP, CONTROL_GROUP))

# 结果整理
res_df <- as.data.frame(res)
res_df$Significance <- ifelse(
  res_df$log2FoldChange >= 1 & res_df$padj <= 0.05, "up",
  ifelse(res_df$log2FoldChange <= -1 & res_df$padj <= 0.05, "down", "ns")
)

# 保存结果
write.table(res_df, "deseq2_result.xls", sep = "\t", quote = FALSE, row.names = TRUE)
```

## 关键参数说明

### results() 函数

| 参数 | 说明 |
|------|------|
| `contrast` | 比较组设置，格式 `c("分组列名", "处理组", "对照组")` |
| `alpha` | 显著性阈值（默认 0.05） |
| `lfcThreshold` | log2FC 阈值（默认 0） |

### 显著性判定标准
- `padj < 0.05`（校正后 p 值）
- `|log2FoldChange| >= 1`（2 倍变化）

## 输出格式

### deseq2_result.xls
```
		baseMean	log2FoldChange	lfcSE	stat	pvalue	padj	Significance
gene1		167.5		1.52		0.23	6.61	3.8e-11	5.2e-09	up
gene2		2.5		-0.1		0.5	-0.2	0.84	0.91	ns
gene3		512.3		-2.1		0.15	-14.0	2.1e-44	1.5e-41	down
```

### 列说明
| 列名 | 说明 |
|------|------|
| baseMean | 各样本平均表达量 |
| log2FoldChange | log2 倍数变化（处理/对照） |
| lfcSE | log2FC 标准误 |
| stat | Wald 统计量 |
| pvalue | 原始 p 值 |
| padj | 校正后 p 值（Benjamini-Hochberg） |
| Significance | 显著性标记（up/down/ns） |

## 可视化（可选）

### MA 图
```r
plotMA(res, main = "MA Plot", ylim = c(-5, 5))
```

### PCA 图
```r
vsd <- vst(dds, blind = FALSE)
plotPCA(vsd, intgroup = "group")
```

### 火山图
```r
library(ggplot2)
res_df$neg_log10_padj <- -log10(res_df$padj)
ggplot(res_df, aes(x = log2FoldChange, y = neg_log10_padj, color = Significance)) +
  geom_point(alpha = 0.6) +
  scale_color_manual(values = c("down" = "blue", "ns" = "gray", "up" = "red")) +
  geom_hline(yintercept = -log10(0.05), linetype = "dashed") +
  geom_vline(xintercept = c(-1, 1), linetype = "dashed") +
  theme_bw()
```

## 注意事项

- 输入数据必须是原始 count 值，不能是 FPKM/TPM
- 样本数建议每组至少 2 个生物学重复
- 过滤低表达基因可提高分析准确性
- `padj` 是经过多重检验校正的 p 值，推荐使用
