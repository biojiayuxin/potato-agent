#!/usr/bin/env Rscript

# Create publication-ready 2–4 set Venn diagrams from a TSV file.
# The final figure is a vector PDF. An optional PNG is intended only for preview.

usage <- function() {
  cat(paste0(
    "Usage:\n",
    "  Rscript venn_from_tsv.R --input DATA.tsv --output FIGURE.pdf [options]\n\n",
    "Required:\n",
    "  --input PATH          Input TSV in long or wide format\n",
    "  --output PATH         Output vector PDF\n\n",
    "Options:\n",
    "  --format auto|long|wide  Input layout (default: auto)\n",
    "  --group-column NAME   Group column in long format (default: group)\n",
    "  --item-column NAME    Item column in long format (default: item)\n",
    "  --groups A,B[,C,D]    Groups/columns to plot, in the requested order\n",
    "  --title TEXT          Figure title (default: Gene set overlap)\n",
    "  --subtitle TEXT       Figure note; use an empty string to omit it\n",
    "  --summary PATH        Exclusive-region TSV (default: OUTPUT.regions.tsv)\n",
    "  --preview PATH        Optional PNG preview\n",
    "  --width INCHES        Figure width (default: 7)\n",
    "  --height INCHES       Figure height (default: 7)\n",
    "  --help                Show this help\n\n",
    "Long format requires one group–item pair per row. Wide format uses one\n",
    "column per group; blank cells are ignored. Repeated items within a group\n",
    "are deduplicated. Exactly 2–4 non-empty groups must be selected.\n"
  ))
}

parse_args <- function(argv) {
  values <- list(
    input = NULL,
    output = NULL,
    format = "auto",
    group_column = "group",
    item_column = "item",
    groups = NULL,
    title = "Gene set overlap",
    subtitle = "Numbers denote exclusive regions; areas are not proportional to set size",
    summary = NULL,
    preview = NULL,
    width = "7",
    height = "7"
  )
  flag_map <- c(
    "--input" = "input",
    "--output" = "output",
    "--format" = "format",
    "--group-column" = "group_column",
    "--item-column" = "item_column",
    "--groups" = "groups",
    "--title" = "title",
    "--subtitle" = "subtitle",
    "--summary" = "summary",
    "--preview" = "preview",
    "--width" = "width",
    "--height" = "height"
  )

  i <- 1L
  while (i <= length(argv)) {
    flag <- argv[[i]]
    if (flag == "--help") {
      usage()
      quit(save = "no", status = 0L)
    }
    if (!flag %in% names(flag_map)) {
      stop(sprintf("Unknown argument: %s (use --help)", flag), call. = FALSE)
    }
    if (i == length(argv)) {
      stop(sprintf("Missing value after %s", flag), call. = FALSE)
    }
    values[[unname(flag_map[[flag]])]] <- argv[[i + 1L]]
    i <- i + 2L
  }
  values
}

clean_items <- function(values) {
  values <- trimws(as.character(values))
  values <- values[!is.na(values) & nzchar(values)]
  unique(values)
}

parse_requested_groups <- function(value) {
  if (is.null(value)) {
    return(NULL)
  }
  groups <- trimws(strsplit(value, ",", fixed = TRUE)[[1L]])
  groups <- groups[nzchar(groups)]
  if (length(groups) != length(unique(groups))) {
    stop("--groups contains duplicated names.", call. = FALSE)
  }
  groups
}

read_sets <- function(path, input_format, group_column, item_column, requested_groups) {
  if (!file.exists(path)) {
    stop(sprintf("Input file does not exist: %s", path), call. = FALSE)
  }
  table <- read.delim(
    path,
    header = TRUE,
    sep = "\t",
    quote = "",
    comment.char = "",
    check.names = FALSE,
    stringsAsFactors = FALSE,
    na.strings = c("", "NA")
  )
  if (ncol(table) < 1L) {
    stop("Input TSV has no columns.", call. = FALSE)
  }

  input_format <- tolower(input_format)
  if (!input_format %in% c("auto", "long", "wide")) {
    stop("--format must be auto, long, or wide.", call. = FALSE)
  }
  if (input_format == "auto") {
    if (all(c(group_column, item_column) %in% colnames(table))) {
      input_format <- "long"
    } else {
      input_format <- "wide"
    }
  }

  if (input_format == "long") {
    missing_columns <- setdiff(c(group_column, item_column), colnames(table))
    if (length(missing_columns) > 0L) {
      stop(
        sprintf("Long-format column(s) not found: %s", paste(missing_columns, collapse = ", ")),
        call. = FALSE
      )
    }
    group_values <- trimws(as.character(table[[group_column]]))
    item_values <- trimws(as.character(table[[item_column]]))
    keep <- !is.na(group_values) & nzchar(group_values) & !is.na(item_values) & nzchar(item_values)
    group_values <- group_values[keep]
    item_values <- item_values[keep]
    available_groups <- unique(group_values)
    selected_groups <- if (is.null(requested_groups)) available_groups else requested_groups
    unknown <- setdiff(selected_groups, available_groups)
    if (length(unknown) > 0L) {
      stop(sprintf("Requested group(s) not found: %s", paste(unknown, collapse = ", ")), call. = FALSE)
    }
    sets <- lapply(selected_groups, function(group_name) {
      clean_items(item_values[group_values == group_name])
    })
    names(sets) <- selected_groups
  } else {
    available_groups <- colnames(table)
    selected_groups <- if (is.null(requested_groups)) available_groups else requested_groups
    unknown <- setdiff(selected_groups, available_groups)
    if (length(unknown) > 0L) {
      stop(sprintf("Requested column(s) not found: %s", paste(unknown, collapse = ", ")), call. = FALSE)
    }
    sets <- lapply(selected_groups, function(group_name) clean_items(table[[group_name]]))
    names(sets) <- selected_groups
  }

  if (length(sets) < 2L || length(sets) > 4L) {
    stop(sprintf("Exactly 2–4 groups are required; found %d.", length(sets)), call. = FALSE)
  }
  empty_groups <- names(sets)[lengths(sets) == 0L]
  if (length(empty_groups) > 0L) {
    stop(sprintf("Selected group(s) contain no items: %s", paste(empty_groups, collapse = ", ")), call. = FALSE)
  }
  sets
}

make_region_summary <- function(sets) {
  group_names <- names(sets)
  n_groups <- length(sets)
  universe <- sort(unique(unlist(sets, use.names = FALSE)))
  membership <- vapply(sets, function(one_set) universe %in% one_set, logical(length(universe)))
  if (n_groups == 1L) {
    membership <- matrix(membership, ncol = 1L)
  }

  combinations <- unlist(
    lapply(
      seq.int(n_groups, 1L),
      function(k) combn(seq_len(n_groups), k, simplify = FALSE)
    ),
    recursive = FALSE
  )

  rows <- lapply(combinations, function(included) {
    pattern <- rep(FALSE, n_groups)
    pattern[included] <- TRUE
    selected <- apply(membership, 1L, function(row) all(row == pattern))
    members <- universe[selected]
    excluded <- setdiff(seq_len(n_groups), included)
    data.frame(
      membership_code = paste(as.integer(pattern), collapse = ""),
      included_groups = paste(group_names[included], collapse = " & "),
      excluded_groups = if (length(excluded) == 0L) "" else paste(group_names[excluded], collapse = " & "),
      count = length(members),
      items = paste(members, collapse = ";"),
      stringsAsFactors = FALSE,
      check.names = FALSE
    )
  })
  summary <- do.call(rbind, rows)

  if (sum(summary$count) != length(universe)) {
    stop("Internal validation failed: exclusive regions do not sum to the union size.", call. = FALSE)
  }
  for (index in seq_along(sets)) {
    represented <- sum(summary$count[substr(summary$membership_code, index, index) == "1"])
    if (represented != length(sets[[index]])) {
      stop(sprintf("Internal validation failed for group %s.", names(sets)[index]), call. = FALSE)
    }
  }
  summary
}

SOFT_FILL_COLORS <- c("#E57373", "#81C784", "#64B5F6", "#FFD54F")
SOFT_LINE_COLORS <- c("#C74F4F", "#4F9A61", "#3E82C4", "#B88900")

build_equal_quad_grob <- function(sets, title, subtitle, fill_colors, line_colors) {
  # Congruent ellipses: every set uses the same semi-major and semi-minor axes.
  # The two lower ellipses have separate centres; the two upper ellipses share
  # a centre. This topology retains all 15 non-empty four-set Venn regions.
  ellipses <- data.frame(
    x = c(0.35, 0.65, 0.50, 0.50),
    y = c(0.43, 0.43, 0.53, 0.53),
    a = rep(0.35, 4L),
    b = rep(0.20, 4L),
    rotation = c(135, 45, 135, 45)
  )
  if (length(unique(ellipses$a)) != 1L || length(unique(ellipses$b)) != 1L) {
    stop("Internal validation failed: four-set ellipses are not congruent.", call. = FALSE)
  }

  # Coordinates were selected at the maximum interior margin of each region
  # for the fixed congruent-ellipse geometry above. Codes follow group order.
  label_positions <- data.frame(
    membership_code = c(
      "0001", "0010", "0011", "0100", "0101",
      "0110", "0111", "1000", "1001", "1010",
      "1011", "1100", "1101", "1110", "1111"
    ),
    x = c(0.646, 0.354, 0.500, 0.816, 0.718, 0.680, 0.617, 0.184, 0.320, 0.282, 0.383, 0.500, 0.416, 0.584, 0.500),
    y = c(0.728, 0.728, 0.652, 0.530, 0.643, 0.363, 0.528, 0.530, 0.363, 0.643, 0.528, 0.231, 0.299, 0.299, 0.395),
    stringsAsFactors = FALSE
  )
  point_membership_code <- function(point_x, point_y) {
    membership <- vapply(seq_len(4L), function(index) {
      angle <- ellipses$rotation[index] * pi / 180
      delta_x <- point_x - ellipses$x[index]
      delta_y <- point_y - ellipses$y[index]
      rotated_x <- delta_x * cos(angle) + delta_y * sin(angle)
      rotated_y <- -delta_x * sin(angle) + delta_y * cos(angle)
      normalized_distance <-
        (rotated_x / ellipses$a[index])^2 + (rotated_y / ellipses$b[index])^2
      as.integer(normalized_distance <= 1)
    }, integer(1L))
    paste(membership, collapse = "")
  }
  observed_codes <- mapply(
    point_membership_code,
    label_positions$x,
    label_positions$y,
    USE.NAMES = FALSE
  )
  if (!all(observed_codes == label_positions$membership_code)) {
    stop("Internal validation failed: a four-set label is outside its intended region.", call. = FALSE)
  }

  region_summary <- make_region_summary(sets)
  label_positions$count <- region_summary$count[
    match(label_positions$membership_code, region_summary$membership_code)
  ]
  if (anyNA(label_positions$count)) {
    stop("Internal validation failed: a four-set region label is missing.", call. = FALSE)
  }

  grobs <- grid::gList()
  for (index in seq_len(4L)) {
    grobs <- grid::gList(
      grobs,
      VennDiagram::ellipse(
        x = ellipses$x[index],
        y = ellipses$y[index],
        a = ellipses$a[index],
        b = ellipses$b[index],
        rotation = ellipses$rotation[index],
        gp = grid::gpar(col = NA, fill = fill_colors[index], alpha = 0.43)
      )
    )
  }
  for (index in seq_len(4L)) {
    grobs <- grid::gList(
      grobs,
      VennDiagram::ellipse(
        x = ellipses$x[index],
        y = ellipses$y[index],
        a = ellipses$a[index],
        b = ellipses$b[index],
        rotation = ellipses$rotation[index],
        gp = grid::gpar(col = line_colors[index], fill = "transparent", lwd = 1.8)
      )
    )
  }
  for (index in seq_len(nrow(label_positions))) {
    grobs <- grid::gList(
      grobs,
      grid::textGrob(
        label = label_positions$count[index],
        x = label_positions$x[index],
        y = label_positions$y[index],
        gp = grid::gpar(col = "#202020", cex = 0.92, fontface = "bold", fontfamily = "sans")
      )
    )
  }

  category_positions <- data.frame(
    x = c(0.10, 0.90, 0.30, 0.70),
    y = c(0.76, 0.76, 0.86, 0.86)
  )
  for (index in seq_len(4L)) {
    grobs <- grid::gList(
      grobs,
      grid::textGrob(
        label = names(sets)[index],
        x = category_positions$x[index],
        y = category_positions$y[index],
        gp = grid::gpar(col = line_colors[index], cex = 0.98, fontface = "bold", fontfamily = "sans")
      )
    )
  }
  grobs <- grid::gList(
    grobs,
    grid::textGrob(
      label = title,
      x = 0.5,
      y = 0.97,
      gp = grid::gpar(col = "#202020", cex = 1.35, fontface = "bold", fontfamily = "sans")
    ),
    grid::textGrob(
      label = subtitle,
      x = 0.5,
      y = 0.925,
      gp = grid::gpar(col = "#555555", cex = 0.78, fontfamily = "sans")
    )
  )
  grid::gTree(children = grobs)
}

build_venn_grob <- function(sets, title, subtitle) {
  n_groups <- length(sets)
  fill_colors <- SOFT_FILL_COLORS[seq_len(n_groups)]
  line_colors <- SOFT_LINE_COLORS[seq_len(n_groups)]

  if (n_groups == 4L) {
    return(build_equal_quad_grob(sets, title, subtitle, fill_colors, line_colors))
  }

  category_positions <- list(
    `2` = c(-20, 20),
    `3` = c(-40, 40, 180)
  )[[as.character(n_groups)]]
  category_distances <- list(
    `2` = c(0.06, 0.06),
    `3` = c(0.06, 0.06, 0.055)
  )[[as.character(n_groups)]]

  venn_arguments <- list(
    x = sets,
    filename = NULL,
    disable.logging = TRUE,
    category.names = names(sets),
    force.unique = TRUE,
    print.mode = "raw",
    fill = fill_colors,
    alpha = rep(0.43, n_groups),
    col = line_colors,
    lwd = rep(1.8, n_groups),
    lty = rep("solid", n_groups),
    cex = 1.05,
    fontface = "bold",
    fontfamily = "sans",
    fontcolor = rep("#202020", 2^n_groups - 1L),
    cat.cex = 1.08,
    cat.fontface = "bold",
    cat.fontfamily = "sans",
    cat.col = line_colors,
    cat.pos = category_positions,
    cat.dist = category_distances,
    margin = 0.10,
    main = title,
    main.cex = 1.35,
    main.fontface = "bold",
    main.fontfamily = "sans",
    main.col = "#202020",
    sub = subtitle,
    sub.cex = 0.78,
    sub.fontfamily = "sans",
    sub.col = "#555555",
    scaled = FALSE
  )
  # VennDiagram logs its full argument list at INFO level even when
  # disable.logging=TRUE; keep normal command output concise.
  futile.logger::flog.threshold(futile.logger::WARN, name = "VennDiagramLogger")
  grobs <- do.call(VennDiagram::venn.diagram, venn_arguments)

  # VennDiagram places two- and three-set bodies low on the page. Shift only
  # the circles, counts, and category labels upward so they sit closer to the
  # title block while preserving title/subtitle spacing.
  body_shift <- c(`2` = 0.17, `3` = 0.05)[[as.character(n_groups)]]
  body_indices <- seq_len(length(grobs) - 2L)
  for (index in body_indices) {
    if (!is.null(grobs[[index]]$y)) {
      grobs[[index]]$y <- grobs[[index]]$y + grid::unit(body_shift, "npc")
    }
  }

  title_y <- c(`2` = 0.920, `3` = 0.950)[[as.character(n_groups)]]
  subtitle_y <- c(`2` = 0.855, `3` = 0.890)[[as.character(n_groups)]]
  grobs[[length(grobs) - 1L]]$y <- grid::unit(subtitle_y, "npc")
  grobs[[length(grobs)]]$y <- grid::unit(title_y, "npc")
  grobs
}

render_pdf <- function(grob, path, width, height) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  # Cairo embeds/subsets the sans-serif font and preserves vector geometry.
  grDevices::cairo_pdf(
    filename = path,
    width = width,
    height = height,
    onefile = FALSE,
    family = "sans",
    fallback_resolution = 300
  )
  on.exit(grDevices::dev.off(), add = TRUE)
  grid::grid.newpage()
  grid::grid.draw(grob)
}

render_png <- function(grob, path, width, height) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  grDevices::png(path, width = width, height = height, units = "in", res = 220, type = "cairo")
  on.exit(grDevices::dev.off(), add = TRUE)
  grid::grid.newpage()
  grid::grid.draw(grob)
}

options <- parse_args(commandArgs(trailingOnly = TRUE))
if (is.null(options$input) || is.null(options$output)) {
  usage()
  stop("Both --input and --output are required.", call. = FALSE)
}
if (!grepl("\\.pdf$", options$output, ignore.case = TRUE)) {
  stop("--output must use the .pdf extension.", call. = FALSE)
}
if (!is.null(options$preview) && !grepl("\\.png$", options$preview, ignore.case = TRUE)) {
  stop("--preview must use the .png extension.", call. = FALSE)
}
width <- suppressWarnings(as.numeric(options$width))
height <- suppressWarnings(as.numeric(options$height))
if (!is.finite(width) || width <= 0 || !is.finite(height) || height <= 0) {
  stop("--width and --height must be positive numbers.", call. = FALSE)
}
if (!requireNamespace("VennDiagram", quietly = TRUE)) {
  stop("R package 'VennDiagram' is required but not installed.", call. = FALSE)
}

requested_groups <- parse_requested_groups(options$groups)
sets <- read_sets(
  options$input,
  options$format,
  options$group_column,
  options$item_column,
  requested_groups
)
summary_path <- options$summary
if (is.null(summary_path)) {
  summary_path <- paste0(sub("\\.pdf$", "", options$output, ignore.case = TRUE), ".regions.tsv")
}
region_summary <- make_region_summary(sets)
dir.create(dirname(summary_path), recursive = TRUE, showWarnings = FALSE)
write.table(
  region_summary,
  file = summary_path,
  sep = "\t",
  quote = FALSE,
  row.names = FALSE,
  col.names = TRUE,
  na = ""
)

grob <- build_venn_grob(sets, options$title, options$subtitle)
render_pdf(grob, options$output, width, height)
if (!is.null(options$preview)) {
  render_png(grob, options$preview, width, height)
}

cat(sprintf("Selected groups: %s\n", paste(names(sets), collapse = ", ")))
cat(sprintf("Set sizes: %s\n", paste(sprintf("%s=%d", names(sets), lengths(sets)), collapse = ", ")))
cat(sprintf("Union size: %d\n", length(unique(unlist(sets, use.names = FALSE)))))
cat(sprintf("Created PDF: %s\n", options$output))
cat(sprintf("Created region summary: %s\n", summary_path))
if (!is.null(options$preview)) {
  cat(sprintf("Created preview: %s\n", options$preview))
}
