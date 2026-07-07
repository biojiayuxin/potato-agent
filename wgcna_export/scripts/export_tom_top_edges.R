#!/usr/bin/env Rscript

parse_args <- function(args) {
  values <- list()
  i <- 1
  while (i <= length(args)) {
    key <- args[[i]]
    if (!startsWith(key, "--")) {
      stop(sprintf("unexpected argument: %s", key))
    }
    name <- sub("^--", "", key)
    if (i == length(args) || startsWith(args[[i + 1]], "--")) {
      values[[name]] <- TRUE
      i <- i + 1
    } else {
      values[[name]] <- args[[i + 1]]
      i <- i + 2
    }
  }
  values
}

read_tsv <- function(path) {
  read.delim(path, stringsAsFactors = FALSE, check.names = FALSE)
}

module_map_for <- function(path) {
  modules <- read_tsv(path)
  if (!all(c("gene_id", "module") %in% colnames(modules))) {
    stop(sprintf("module table missing gene_id/module columns: %s", path))
  }
  stats::setNames(modules$module, modules$gene_id)
}

write_edge <- function(con, network_id, gene_id, neighbor_gene_id, tom, tom_percentile, rank, same_module, gene_module, neighbor_module) {
  line <- paste(
    network_id,
    gene_id,
    neighbor_gene_id,
    format(tom, digits = 10, scientific = FALSE),
    format(tom_percentile, digits = 10, scientific = FALSE),
    rank,
    ifelse(same_module, "true", "false"),
    gene_module,
    neighbor_module,
    sep = "\t"
  )
  writeLines(line, con = con, sep = "\n")
}

export_network <- function(base_dir, output_con, network_id, top_n) {
  tom_path <- file.path(base_dir, "05-tom", sprintf("%s.TOM-block.1.RData", network_id))
  rds_path <- file.path(base_dir, "02-modules", sprintf("%s.blockwiseModules.rds", network_id))
  module_path <- file.path(base_dir, "02-modules", sprintf("%s.gene_modules.tsv", network_id))

  for (path in c(tom_path, rds_path, module_path)) {
    if (!file.exists(path)) {
      stop(sprintf("missing required input: %s", path))
    }
  }

  loaded <- load(tom_path)
  if (!"TOM" %in% loaded && !exists("TOM", inherits = FALSE)) {
    stop(sprintf("TOM object not found in %s", tom_path))
  }
  net <- readRDS(rds_path)
  gene_order <- names(net$colors)
  if (is.null(gene_order) || length(gene_order) == 0) {
    stop(sprintf("names(net$colors) is empty in %s", rds_path))
  }
  tom_size <- attr(TOM, "Size")
  if (is.null(tom_size)) {
    tom_size <- nrow(as.matrix(TOM))
  }
  if (length(gene_order) != tom_size) {
    stop(sprintf(
      "gene order length mismatch for %s: names(net$colors)=%s TOM size=%s",
      network_id,
      length(gene_order),
      tom_size
    ))
  }

  modules <- module_map_for(module_path)
  missing_modules <- setdiff(gene_order, names(modules))
  if (length(missing_modules) > 0) {
    stop(sprintf("module table missing %s TOM genes for %s", length(missing_modules), network_id))
  }

  message(sprintf("exporting %s top %s edges per gene", network_id, top_n))
  tom_matrix <- as.matrix(TOM)
  diag(tom_matrix) <- -Inf
  n_genes <- length(gene_order)
  keep_n <- min(as.integer(top_n), n_genes - 1)

  for (i in seq_len(n_genes)) {
    row <- tom_matrix[i, ]
    top_idx <- order(row, decreasing = TRUE, na.last = NA)[seq_len(keep_n)]
    gene_id <- gene_order[[i]]
    gene_module <- modules[[gene_id]]
    for (rank in seq_along(top_idx)) {
      j <- top_idx[[rank]]
      neighbor_gene_id <- gene_order[[j]]
      neighbor_module <- modules[[neighbor_gene_id]]
      tom_percentile <- 1 - ((rank - 1) / max(1, n_genes - 2))
      write_edge(
        output_con,
        network_id,
        gene_id,
        neighbor_gene_id,
        row[[j]],
        tom_percentile,
        rank,
        identical(gene_module, neighbor_module),
        gene_module,
        neighbor_module
      )
    }
  }

  rm(tom_matrix, TOM)
  invisible(gc())
}

main <- function() {
  args <- parse_args(commandArgs(trailingOnly = TRUE))
  base_dir <- normalizePath(args[["base-dir"]] %||% "/mnt/data/potato_agent/work/WGCNA/03-network", mustWork = TRUE)
  output_dir <- path.expand(args[["output-dir"]] %||% "~/tmp/wgcna_coexpression_export")
  networks <- strsplit(args[["networks"]] %||% "leaf,stem,root,reproductive,tuberization", ",", fixed = TRUE)[[1]]
  top_n <- as.integer(args[["top-n"]] %||% "100")
  if (is.na(top_n) || top_n < 1) {
    stop("--top-n must be a positive integer")
  }

  table_dir <- file.path(output_dir, "tables")
  log_dir <- file.path(output_dir, "logs")
  dir.create(table_dir, recursive = TRUE, showWarnings = FALSE)
  dir.create(log_dir, recursive = TRUE, showWarnings = FALSE)
  output_path <- file.path(table_dir, "coexpression_edges_top.tsv.gz")
  audit_path <- file.path(log_dir, "tom_export_audit.tsv")

  con <- gzfile(output_path, open = "wt")
  on.exit(close(con), add = TRUE)
  writeLines(
    "network_id\tgene_id\tneighbor_gene_id\ttom\ttom_percentile\trank\tsame_module\tgene_module\tneighbor_module",
    con = con,
    sep = "\n"
  )

  audit <- data.frame(
    network_id = character(),
    tom_path = character(),
    rds_path = character(),
    module_path = character(),
    status = character(),
    stringsAsFactors = FALSE
  )

  for (network_id in networks) {
    network_id <- trimws(network_id)
    if (!nzchar(network_id)) next
    export_network(base_dir, con, network_id, top_n)
    audit[nrow(audit) + 1, ] <- list(
      network_id,
      file.path(base_dir, "05-tom", sprintf("%s.TOM-block.1.RData", network_id)),
      file.path(base_dir, "02-modules", sprintf("%s.blockwiseModules.rds", network_id)),
      file.path(base_dir, "02-modules", sprintf("%s.gene_modules.tsv", network_id)),
      "ok"
    )
  }

  write.table(audit, audit_path, sep = "\t", row.names = FALSE, quote = FALSE)
  message(output_path)
}

`%||%` <- function(left, right) {
  if (is.null(left) || length(left) == 0 || !nzchar(as.character(left))) right else left
}

main()
