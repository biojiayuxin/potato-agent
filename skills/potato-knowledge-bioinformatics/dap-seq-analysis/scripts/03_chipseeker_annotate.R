#!/usr/bin/env Rscript
# Annotate one DAP-Seq target's MACS2 narrowPeak file with ChIPseeker.

parse_args <- function(args) {
  if (length(args) == 0 || any(args %in% c("-h", "--help"))) {
    cat(paste0(
      "Usage: Rscript 03_chipseeker_annotate.R \\\n",
      "  --target-id ERF1 \\\n",
      "  --narrowpeak ERF1_peaks.narrowPeak \\\n",
      "  --gff annotation.gff3 \\\n",
      "  --out ERF1.anno.with_intergenic.txt \\\n",
      "  --tmp-dir results/tmp/chipseeker \\\n",
      "  [--annotation-table gene_annotation.tsv] \\\n",
      "  [--tss-upstream 5000] [--tss-downstream 0]\n"
    ))
    quit(status = 0)
  }

  opts <- list()
  i <- 1
  while (i <= length(args)) {
    key <- args[[i]]
    if (!startsWith(key, "--")) {
      stop("Unexpected positional argument: ", key)
    }
    if (i == length(args) || startsWith(args[[i + 1]], "--")) {
      stop("Missing value for argument: ", key)
    }
    opts[[sub("^--", "", key)]] <- args[[i + 1]]
    i <- i + 2
  }
  opts
}

get_opt <- function(opts, key, default = NULL, required = FALSE) {
  value <- opts[[key]]
  if (is.null(value) || identical(value, "")) {
    if (required) {
      stop("Missing required argument --", key)
    }
    return(default)
  }
  value
}

check_file <- function(path, label) {
  if (is.null(path) || identical(path, "") || !file.exists(path)) {
    stop(label, " not found: ", path)
  }
}

load_annotation_map <- function(annotation_table) {
  if (is.null(annotation_table) || identical(annotation_table, "")) {
    return(NULL)
  }
  check_file(annotation_table, "annotation_table")
  anno <- read.delim(
    annotation_table,
    header = FALSE,
    sep = "\t",
    quote = "",
    comment.char = "#",
    stringsAsFactors = FALSE
  )
  if (ncol(anno) < 2) {
    stop("annotation_table must contain at least two tab-delimited columns: gene_id and annotation")
  }
  stats::setNames(as.character(anno[[2]]), as.character(anno[[1]]))
}

append_function_annotation <- function(df, annotation_map) {
  if (is.null(annotation_map)) {
    return(df)
  }

  gene_col <- NULL
  preferred_cols <- c("geneId", "gene_id", "geneID", "transcriptId", "transcript_id")
  for (col in preferred_cols) {
    if (col %in% colnames(df)) {
      gene_col <- col
      break
    }
  }
  if (is.null(gene_col)) {
    candidates <- grep("gene|transcript", colnames(df), ignore.case = TRUE, value = TRUE)
    if (length(candidates) > 0) {
      gene_col <- candidates[[1]]
    }
  }
  if (is.null(gene_col)) {
    df$Annotation <- "NA"
    return(df)
  }

  ids <- as.character(df[[gene_col]])
  values <- unname(annotation_map[ids])
  values[is.na(values)] <- "NA"
  df$Annotation <- values
  df
}

args <- commandArgs(trailingOnly = TRUE)
opts <- parse_args(args)

target_id <- get_opt(opts, "target-id", required = TRUE)
narrowpeak <- get_opt(opts, "narrowpeak", required = TRUE)
gff_file <- get_opt(opts, "gff", required = TRUE)
out_file <- get_opt(opts, "out", required = TRUE)
tmp_dir <- get_opt(opts, "tmp-dir", required = TRUE)
annotation_table <- get_opt(opts, "annotation-table", default = "")
tss_upstream <- as.integer(get_opt(opts, "tss-upstream", default = "5000"))
tss_downstream <- as.integer(get_opt(opts, "tss-downstream", default = "0"))

check_file(narrowpeak, "narrowPeak")
check_file(gff_file, "GFF/GTF")

dir.create(dirname(out_file), recursive = TRUE, showWarnings = FALSE)
dir.create(tmp_dir, recursive = TRUE, showWarnings = FALSE)

suppressPackageStartupMessages({
  library(ChIPseeker)
  library(GenomicRanges)
  library(IRanges)
  library(txdbmaker)
})

cat("[DAP-Seq] ChIPseeker target:", target_id, "\n")
cat("[DAP-Seq] GFF/GTF:", gff_file, "\n")
cat("[DAP-Seq] narrowPeak:", narrowpeak, "\n")
cat("[DAP-Seq] TSS window:", paste0("-", abs(tss_upstream), "..", tss_downstream), "\n")

txdb <- txdbmaker::makeTxDbFromGFF(file = gff_file)

np_cols <- c("chrom", "chromStart", "chromEnd", "name", "score",
             "strand", "signalValue", "pValue", "qValue", "peak")
np_df <- read.table(narrowpeak, header = FALSE, sep = "\t",
                    col.names = np_cols, comment.char = "#", stringsAsFactors = FALSE)
if (nrow(np_df) == 0) {
  stop("narrowPeak has no peak rows: ", narrowpeak)
}
np_df$strand[!(np_df$strand %in% c("+", "-", "*"))] <- "*"

peak <- GenomicRanges::GRanges(
  seqnames = np_df$chrom,
  ranges = IRanges::IRanges(start = as.integer(np_df$chromStart) + 1L,
                            end = as.integer(np_df$chromEnd)),
  strand = np_df$strand
)

peak_anno <- ChIPseeker::annotatePeak(
  peak,
  tssRegion = c(-abs(tss_upstream), tss_downstream),
  TxDb = txdb,
  verbose = FALSE
)

out_df <- as.data.frame(peak_anno)
out_df <- append_function_annotation(out_df, load_annotation_map(annotation_table))

write.table(
  out_df,
  file = out_file,
  sep = "\t",
  quote = FALSE,
  row.names = FALSE,
  col.names = TRUE
)

cat("[DAP-Seq] wrote:", out_file, "\n")
