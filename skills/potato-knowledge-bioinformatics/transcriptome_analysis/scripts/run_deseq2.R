#!/usr/bin/env Rscript
# ============================================================
# DESeq2 差异表达分析脚本
# 智能体根据实际参数修改 CONTROL_GROUP 和 TREAT_GROUP
# ============================================================

suppressPackageStartupMessages({
  library(DESeq2)
  library(ggplot2)
})

# ============================================================
# 【智能体修改区域】根据实际分组名称修改
# ============================================================
CONTROL_GROUP <- "CK"      # 对照组名称
TREAT_GROUP <- "Treat"     # 处理组名称

# ============================================================
# 读取数据
# ============================================================
count_data <- read.csv("02-stringtie/gene_count_matrix.csv", row.names = 1, check.names = FALSE)
group_info <- read.table("groups.txt", header = TRUE, row.names = 1, sep = "\t")

# 对齐样本顺序
common_samples <- intersect(colnames(count_data), rownames(group_info))
count_data <- count_data[, common_samples]
group_info <- group_info[common_samples, , drop = FALSE]

cat(sprintf("[INFO] 样本数: %d, 基因数: %d\n", ncol(count_data), nrow(count_data)))

# ============================================================
# 构建 DESeq2 对象
# ============================================================
dds <- DESeqDataSetFromMatrix(
  countData = count_data,
  colData = group_info,
  design = ~ group
)

# 过滤低表达基因
keep <- rowSums(counts(dds)) >= 10
dds <- dds[keep, ]
cat(sprintf("[INFO] 过滤后基因数: %d\n", nrow(dds)))

# ============================================================
# 差异分析
# ============================================================
dds <- DESeq(dds)
res <- results(dds, contrast = c("group", TREAT_GROUP, CONTROL_GROUP))

# 整理结果
res_df <- as.data.frame(res)
res_df$Significance <- ifelse(
  !is.na(res_df$padj) & res_df$padj < 0.05 & res_df$log2FoldChange >= 1, "up",
  ifelse(
    !is.na(res_df$padj) & res_df$padj < 0.05 & res_df$log2FoldChange <= -1, "down", "ns"
  )
)

# 统计
n_up <- sum(res_df$Significance == "up", na.rm = TRUE)
n_down <- sum(res_df$Significance == "down", na.rm = TRUE)
cat(sprintf("[INFO] 上调: %d, 下调: %d\n", n_up, n_down))

# ============================================================
# 保存结果
# ============================================================
write.table(res_df, "03-deseq2/deseq2_result.xls", sep = "\t", quote = FALSE, row.names = TRUE)
cat("[INFO] 结果已保存: 03-deseq2/deseq2_result.xls\n")

# ============================================================
# 可视化
# ============================================================
# MA 图
pdf("03-deseq2/MA_plot.pdf", width = 8, height = 6)
plotMA(res, main = paste0("MA Plot: ", TREAT_GROUP, " vs ", CONTROL_GROUP), ylim = c(-5, 5))
dev.off()

# 火山图
volcano_data <- res_df
volcano_data$neg_log10_padj <- -log10(volcano_data$padj)
volcano_data$neg_log10_padj[is.infinite(volcano_data$neg_log10_padj)] <- max(
  volcano_data$neg_log10_padj[is.finite(volcano_data$neg_log10_padj)], na.rm = TRUE
)

p_volcano <- ggplot(volcano_data, aes(x = log2FoldChange, y = neg_log10_padj, color = Significance)) +
  geom_point(alpha = 0.6, size = 1.5) +
  scale_color_manual(values = c("down" = "#3498DB", "ns" = "#95A5A6", "up" = "#E74C3C")) +
  geom_hline(yintercept = -log10(0.05), linetype = "dashed", color = "gray50") +
  geom_vline(xintercept = c(-1, 1), linetype = "dashed", color = "gray50") +
  labs(title = paste0("Volcano Plot: ", TREAT_GROUP, " vs ", CONTROL_GROUP),
       x = expression(log[2]~Fold~Change),
       y = expression(-log[10]~(adjusted~p-value))) +
  theme_bw() +
  theme(plot.title = element_text(hjust = 0.5, size = 14, face = "bold"))

ggsave("03-deseq2/volcano_plot.pdf", p_volcano, width = 10, height = 7)

# PCA 图
vsd <- vst(dds, blind = FALSE)
pca_data <- plotPCA(vsd, intgroup = "group", returnData = TRUE)
percentVar <- round(100 * attr(pca_data, "percentVar"))

p_pca <- ggplot(pca_data, aes(PC1, PC2, color = group)) +
  geom_point(size = 4) +
  labs(title = "PCA Plot",
       x = paste0("PC1: ", percentVar[1], "% variance"),
       y = paste0("PC2: ", percentVar[2], "% variance")) +
  theme_bw() +
  theme(plot.title = element_text(hjust = 0.5, size = 14, face = "bold"))

ggsave("03-deseq2/pca_plot.pdf", p_pca, width = 8, height = 6)

cat("[INFO] 分析完成!\n")
