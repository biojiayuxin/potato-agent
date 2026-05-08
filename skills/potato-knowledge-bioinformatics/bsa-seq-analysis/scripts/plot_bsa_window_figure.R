#!/usr/bin/env Rscript
suppressPackageStartupMessages({
  library(ggplot2)
  library(gridExtra)
  library(tidyr)
  library(grid)
})

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 5) {
  stop("Usage: Rscript plot_bsa_window_figure.R <window_result.txt> <output.pdf> <sample_A> <sample_B> <delta_threshold>")
}

input_file <- args[1]
output_file <- args[2]
sample_a <- args[3]
sample_b <- args[4]
delta_threshold <- suppressWarnings(as.numeric(args[5]))
if (is.na(delta_threshold)) {
  stop("delta_threshold must be a numeric value")
}

df <- read.table(input_file, header = TRUE, sep = "\t", check.names = FALSE, stringsAsFactors = FALSE)
if (nrow(df) == 0) {
  stop("window_result.txt is empty")
}

required_cols <- c("Chrom", "Start", "End", "SNP_index_A_window", "SNP_index_B_window", "Delta_index_window")
missing_cols <- setdiff(required_cols, colnames(df))
if (length(missing_cols) > 0) {
  stop(paste("Missing columns:", paste(missing_cols, collapse = ", ")))
}

chrom_levels <- unique(df$Chrom)
df$Chrom <- factor(df$Chrom, levels = chrom_levels)

COLOR_POOL_A <- "#FF3030"
COLOR_POOL_B <- "#FFA500"
COLOR_DELTA <- "#0000EE"

plot_snp_list <- list()
plot_delta_list <- list()

for (i in seq_along(chrom_levels)) {
  chr <- chrom_levels[i]
  chr_data <- subset(df, Chrom == chr)
  if (nrow(chr_data) == 0) {
    next
  }

  snp_data <- chr_data[, c("Chrom", "Start", "SNP_index_A_window", "SNP_index_B_window")]
  snp_long <- pivot_longer(
    snp_data,
    cols = c("SNP_index_A_window", "SNP_index_B_window"),
    names_to = "Pool",
    values_to = "SNP_index"
  )
  snp_long$Pool <- factor(
    snp_long$Pool,
    levels = c("SNP_index_A_window", "SNP_index_B_window"),
    labels = c(sample_a, sample_b)
  )

  if (i == 1) {
    p_snp <- ggplot(snp_long, aes(x = Start, y = SNP_index, color = Pool)) +
      geom_point(size = 0.5) +
      labs(x = as.character(chr), y = "SNP index") +
      scale_color_manual(values = setNames(c(COLOR_POOL_A, COLOR_POOL_B), c(sample_a, sample_b))) +
      theme(panel.background = element_blank()) +
      theme(legend.position = "none") +
      scale_y_continuous(breaks = seq(0, 1, 0.1), limits = c(0, 1), expand = c(0.05, 0)) +
      theme(
        axis.line.x = element_line(color = "black", linetype = "solid"),
        axis.ticks.x = element_blank(),
        axis.text.x = element_blank(),
        axis.line.y = element_line(color = "black", linetype = "solid")
      )
  } else {
    p_snp <- ggplot(snp_long, aes(x = Start, y = SNP_index, color = Pool)) +
      geom_point(size = 0.5) +
      labs(x = as.character(chr)) +
      scale_color_manual(values = setNames(c(COLOR_POOL_A, COLOR_POOL_B), c(sample_a, sample_b))) +
      theme(panel.background = element_blank()) +
      scale_y_continuous(labels = NULL, limits = c(0, 1), expand = c(0.05, 0)) +
      theme(
        axis.line.x = element_line(color = "black", linetype = "solid"),
        axis.ticks.y = element_blank(),
        axis.text.y = element_blank(),
        axis.title.y = element_blank(),
        axis.ticks.x = element_blank(),
        axis.text.x = element_blank(),
        legend.position = "none"
      )
  }
  plot_snp_list[[length(plot_snp_list) + 1]] <- p_snp

  if (i == 1) {
    p_delta <- ggplot(chr_data, aes(x = Start, y = Delta_index_window)) +
      geom_point(size = 0.5, color = COLOR_DELTA) +
      labs(x = as.character(chr), y = "Delta index") +
      theme(panel.background = element_blank()) +
      theme(legend.position = "none") +
      scale_y_continuous(breaks = seq(0, 1, 0.1), limits = c(0, 1), expand = c(0.05, 0)) +
      theme(
        axis.line.x = element_line(color = "black", linetype = "solid"),
        axis.ticks.x = element_blank(),
        axis.text.x = element_blank(),
        axis.line.y = element_line(color = "black", linetype = "solid")
      ) +
      geom_hline(yintercept = delta_threshold, linetype = "dashed", color = "red")
  } else {
    p_delta <- ggplot(chr_data, aes(x = Start, y = Delta_index_window)) +
      geom_point(size = 0.5, color = COLOR_DELTA) +
      labs(x = as.character(chr)) +
      theme(panel.background = element_blank()) +
      scale_y_continuous(labels = NULL, limits = c(0, 1), expand = c(0.05, 0)) +
      theme(
        axis.line.x = element_line(color = "black", linetype = "solid"),
        axis.ticks.y = element_blank(),
        axis.text.y = element_blank(),
        axis.title.y = element_blank(),
        axis.ticks.x = element_blank(),
        axis.text.x = element_blank(),
        legend.position = "none"
      ) +
      geom_hline(yintercept = delta_threshold, linetype = "dashed", color = "red")
  }
  plot_delta_list[[length(plot_delta_list) + 1]] <- p_delta
}

all1 <- do.call(grid.arrange, c(plot_snp_list, nrow = 1))
all2 <- do.call(grid.arrange, c(plot_delta_list, nrow = 1))
legend_panel <- grobTree(
  rectGrob(gp = gpar(fill = "white", col = NA)),
  pointsGrob(x = unit(0.03, "npc"), y = unit(0.80, "npc"), pch = 16, size = unit(3.5, "mm"), gp = gpar(col = COLOR_POOL_A)),
  textGrob(paste0("A pool: ", sample_a), x = unit(0.055, "npc"), y = unit(0.80, "npc"), just = c("left", "center"), gp = gpar(col = "black", fontsize = 12, fontface = "bold")),
  pointsGrob(x = unit(0.03, "npc"), y = unit(0.50, "npc"), pch = 16, size = unit(3.5, "mm"), gp = gpar(col = COLOR_POOL_B)),
  textGrob(paste0("B pool: ", sample_b), x = unit(0.055, "npc"), y = unit(0.50, "npc"), just = c("left", "center"), gp = gpar(col = "black", fontsize = 12, fontface = "bold")),
  pointsGrob(x = unit(0.03, "npc"), y = unit(0.20, "npc"), pch = 16, size = unit(3.5, "mm"), gp = gpar(col = COLOR_DELTA)),
  textGrob("Delta index", x = unit(0.055, "npc"), y = unit(0.20, "npc"), just = c("left", "center"), gp = gpar(col = "black", fontsize = 12, fontface = "bold"))
)
all <- grid.arrange(all1, all2, legend_panel, ncol = 1, heights = c(1, 1, 0.18))
ggsave(output_file, all, width = 40, height = 16.2, units = "cm")
