(function attachSpatialExpressionPdf(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.SpatialExpressionPdf = api;
}(typeof globalThis !== "undefined" ? globalThis : this, () => {
  const PDF_POINTS_PER_PIXEL = 72 / 150;
  const DEFAULT_WIDTH = 2000;
  const STREAM_FLUSH_SIZE = 512 * 1024;
  const REDS = [
    [255, 245, 240],
    [254, 224, 210],
    [252, 187, 161],
    [252, 146, 114],
    [251, 106, 74],
    [239, 59, 44],
    [203, 24, 29],
    [165, 15, 21],
    [103, 0, 13],
  ];

  const encoder = new TextEncoder();

  const pdfNumber = (value) => {
    const rounded = Math.round(Number(value) * 1000) / 1000;
    return String(Math.abs(rounded) < 0.0005 ? 0 : rounded);
  };

  const normalizedPdfText = (value) => String(value ?? "")
    .replace(/[\u2010-\u2015]/g, "-")
    .replace(/[\u2018\u2019]/g, "'")
    .replace(/[\u201c\u201d]/g, '"')
    .replace(/\u2026/g, "...")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "");

  const escapePdfText = (value) => {
    let escaped = "";
    for (const character of normalizedPdfText(value)) {
      const code = character.codePointAt(0);
      if (character === "\\" || character === "(" || character === ")") {
        escaped += `\\${character}`;
      } else if (code >= 32 && code <= 126) {
        escaped += character;
      } else if (character === "\n" || character === "\r" || character === "\t") {
        escaped += " ";
      } else {
        escaped += "?";
      }
    }
    return escaped;
  };

  const colorOperator = (rgb, stroke = false) => {
    const values = (Array.isArray(rgb) ? rgb : [0, 0, 0]).map((component) => (
      pdfNumber(Math.max(0, Math.min(255, Number(component) || 0)) / 255)
    ));
    return `${values.join(" ")} ${stroke ? "RG" : "rg"}`;
  };

  const invertRgb = (rgb) => [255 - rgb[0], 255 - rgb[1], 255 - rgb[2]];

  const formatNumber = (value, digits = 3) => {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return "-";
    if (Math.abs(numeric) >= 1000) return numeric.toLocaleString("en-US", { maximumFractionDigits: 0 });
    return String(Number(numeric.toFixed(digits)));
  };

  const approximateTextWidth = (value, size) => {
    let units = 0;
    for (const character of normalizedPdfText(value)) {
      if (/[ilI1.,'|!]/.test(character)) units += 0.3;
      else if (/[MW@%&]/.test(character)) units += 0.9;
      else if (/[A-Z0-9]/.test(character)) units += 0.64;
      else if (/\s/.test(character)) units += 0.3;
      else units += 0.53;
    }
    return units * size;
  };

  const truncateText = (value, size, maxWidth) => {
    const text = String(value ?? "");
    if (approximateTextWidth(text, size) <= maxWidth) return text;
    const suffix = "...";
    let low = 0;
    let high = text.length;
    while (low < high) {
      const middle = Math.ceil((low + high) / 2);
      if (approximateTextWidth(`${text.slice(0, middle)}${suffix}`, size) <= maxWidth) {
        low = middle;
      } else {
        high = middle - 1;
      }
    }
    return `${text.slice(0, low)}${suffix}`;
  };

  const colorForValue = (value, range) => {
    const minimum = Number.isFinite(Number(range?.vmin)) ? Number(range.vmin) : 0;
    const maximum = Number.isFinite(Number(range?.vmax)) ? Number(range.vmax) : 0;
    const numeric = Number(value) || 0;
    const fraction = maximum > minimum
      ? Math.max(0, Math.min(1, (numeric - minimum) / (maximum - minimum)))
      : 0;
    const scaled = fraction * (REDS.length - 1);
    const index = Math.min(REDS.length - 2, Math.floor(scaled));
    const local = scaled - index;
    return REDS[index].map((component, componentIndex) => (
      Math.round(component + (REDS[index + 1][componentIndex] - component) * local)
    ));
  };

  class VectorPage {
    constructor(width, height) {
      this.width = Number(width);
      this.height = Number(height);
      this.commands = [];
      this.commandSize = 0;
      this.writer = null;
      this.compressedBytes = null;
      if (typeof CompressionStream === "function") {
        try {
          const compression = new CompressionStream("deflate");
          this.writer = compression.writable.getWriter();
          this.compressedBytes = new Response(compression.readable).arrayBuffer();
          this.compressedBytes.catch(() => {});
        } catch (_error) {
          this.writer = null;
          this.compressedBytes = null;
        }
      }
      this.push(
        `q ${pdfNumber(PDF_POINTS_PER_PIXEL)} 0 0 ${pdfNumber(-PDF_POINTS_PER_PIXEL)} `
        + `0 ${pdfNumber(this.height * PDF_POINTS_PER_PIXEL)} cm`,
      );
    }

    push(command) {
      const chunk = `${command}\n`;
      this.commands.push(chunk);
      this.commandSize += chunk.length;
    }

    async checkpoint(force = false) {
      if (!force && this.commandSize < STREAM_FLUSH_SIZE) return;
      if (!this.writer || !this.commands.length) return;
      const commands = this.commands;
      this.commands = [];
      this.commandSize = 0;
      await this.writer.write(encoder.encode(commands.join("")));
    }

    async abort(reason) {
      this.commands = [];
      this.commandSize = 0;
      if (!this.writer) return;
      try {
        await this.writer.abort(reason);
      } catch (_error) {
        // The compression stream can already be closed after a transport error.
      }
    }

    rect(x, y, width, height, options = {}) {
      const fill = options.fill;
      const stroke = options.stroke;
      if (!fill && !stroke) return;
      const operation = fill && stroke ? "B" : fill ? "f" : "S";
      const styles = [];
      if (fill) styles.push(colorOperator(fill));
      if (stroke) styles.push(colorOperator(stroke, true), `${pdfNumber(options.lineWidth || 1)} w`);
      this.push(
        `q ${styles.join(" ")} ${pdfNumber(x)} ${pdfNumber(y)} ${pdfNumber(width)} ${pdfNumber(height)} re ${operation} Q`,
      );
    }

    line(x1, y1, x2, y2, options = {}) {
      this.push(
        `q ${colorOperator(options.color || [0, 0, 0], true)} ${pdfNumber(options.lineWidth || 1)} w `
        + `${pdfNumber(x1)} ${pdfNumber(y1)} m ${pdfNumber(x2)} ${pdfNumber(y2)} l S Q`,
      );
    }

    polygon(points, options = {}) {
      if (!Array.isArray(points) || points.length < 3) return;
      const fill = options.fill || [0, 0, 0];
      const stroke = options.stroke || fill;
      const path = points.map((point, index) => (
        `${pdfNumber(point[0])} ${pdfNumber(point[1])} ${index === 0 ? "m" : "l"}`
      )).join(" ");
      this.push(
        `q ${colorOperator(fill)} ${colorOperator(stroke, true)} ${pdfNumber(options.lineWidth || 0.5)} w ${path} h B Q`,
      );
    }

    circle(x, y, radius, options = {}) {
      const control = radius * 0.5522847498;
      const path = [
        `${pdfNumber(x + radius)} ${pdfNumber(y)} m`,
        `${pdfNumber(x + radius)} ${pdfNumber(y + control)} ${pdfNumber(x + control)} ${pdfNumber(y + radius)} ${pdfNumber(x)} ${pdfNumber(y + radius)} c`,
        `${pdfNumber(x - control)} ${pdfNumber(y + radius)} ${pdfNumber(x - radius)} ${pdfNumber(y + control)} ${pdfNumber(x - radius)} ${pdfNumber(y)} c`,
        `${pdfNumber(x - radius)} ${pdfNumber(y - control)} ${pdfNumber(x - control)} ${pdfNumber(y - radius)} ${pdfNumber(x)} ${pdfNumber(y - radius)} c`,
        `${pdfNumber(x + control)} ${pdfNumber(y - radius)} ${pdfNumber(x + radius)} ${pdfNumber(y - control)} ${pdfNumber(x + radius)} ${pdfNumber(y)} c`,
      ].join(" ");
      this.push(
        `q ${colorOperator(options.fill || [0, 0, 0])} ${colorOperator(options.stroke || [0, 0, 0], true)} `
        + `${pdfNumber(options.lineWidth || 1)} w ${path} B Q`,
      );
    }

    text(value, x, y, options = {}) {
      const size = Number(options.size || 10);
      const content = normalizedPdfText(value);
      let offsetX = 0;
      if (options.align === "center") offsetX = -approximateTextWidth(content, size) / 2;
      if (options.align === "right") offsetX = -approximateTextWidth(content, size);
      this.push(
        `q ${colorOperator(options.color || [0, 0, 0])} 1 0 0 -1 ${pdfNumber(x)} ${pdfNumber(y)} cm `
        + `BT /${options.bold ? "F2" : "F1"} ${pdfNumber(size)} Tf `
        + `1 0 0 1 ${pdfNumber(offsetX)} 0 Tm (${escapePdfText(content)}) Tj ET Q`,
      );
    }

    gradient(x, y, width, height, colors = REDS) {
      const selectedColors = Array.isArray(colors) && colors.length ? colors : REDS;
      const segments = Math.max(32, Math.min(256, Math.round(width)));
      const segmentWidth = width / segments;
      for (let index = 0; index < segments; index += 1) {
        const fraction = index / Math.max(1, segments - 1);
        const scaled = fraction * (selectedColors.length - 1);
        const colorIndex = Math.min(selectedColors.length - 2, Math.floor(scaled));
        const local = scaled - colorIndex;
        const color = selectedColors[colorIndex].map((component, componentIndex) => (
          Math.round(component + (selectedColors[colorIndex + 1][componentIndex] - component) * local)
        ));
        this.rect(x + index * segmentWidth, y, segmentWidth + 0.02, height, { fill: color });
      }
      this.rect(x, y, width, height, { stroke: [208, 180, 170], lineWidth: 0.8 });
    }

    async finalize() {
      this.push("Q");
      if (this.writer) {
        await this.checkpoint(true);
        await this.writer.close();
        return {
          width: this.width,
          height: this.height,
          bytes: new Uint8Array(await this.compressedBytes),
          compressed: true,
        };
      }
      const source = new Blob(this.commands, { type: "application/octet-stream" });
      this.commands = [];
      this.commandSize = 0;
      return {
        width: this.width,
        height: this.height,
        bytes: new Uint8Array(await source.arrayBuffer()),
        compressed: false,
      };
    }
  }

  const toBytes = (value) => typeof value === "string" ? encoder.encode(value) : value;

  const assemblePdf = (pages, title) => {
    const pageObjectIds = pages.map((_, index) => 5 + index * 2);
    const infoObjectId = 5 + pages.length * 2;
    const objects = new Map([
      [1, ["<< /Type /Catalog /Pages 2 0 R >>"]],
      [2, [`<< /Type /Pages /Kids [${pageObjectIds.map((id) => `${id} 0 R`).join(" ")}] /Count ${pages.length} >>`]],
      [3, ["<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]],
      [4, ["<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"]],
    ]);

    pages.forEach((page, index) => {
      const pageObjectId = pageObjectIds[index];
      const contentObjectId = pageObjectId + 1;
      const pageWidth = pdfNumber(page.width * PDF_POINTS_PER_PIXEL);
      const pageHeight = pdfNumber(page.height * PDF_POINTS_PER_PIXEL);
      objects.set(pageObjectId, [
        `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${pageWidth} ${pageHeight}] `
        + `/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents ${contentObjectId} 0 R >>`,
      ]);
      objects.set(contentObjectId, [
        `<< /Length ${page.bytes.length}${page.compressed ? " /Filter /FlateDecode" : ""} >>\nstream\n`,
        page.bytes,
        "\nendstream",
      ]);
    });
    objects.set(infoObjectId, [
      `<< /Title (${escapePdfText(title)}) /Creator (Potato Agent Spatial Expression vector export) >>`,
    ]);

    const chunks = [];
    const offsets = [0];
    let length = 0;
    const append = (value) => {
      const bytes = toBytes(value);
      chunks.push(bytes);
      length += bytes.length;
    };

    append("%PDF-1.4\n% Potato Agent vector export\n");
    for (let objectId = 1; objectId <= infoObjectId; objectId += 1) {
      offsets[objectId] = length;
      append(`${objectId} 0 obj\n`);
      for (const part of objects.get(objectId)) append(part);
      append("\nendobj\n");
    }
    const xrefOffset = length;
    append(`xref\n0 ${infoObjectId + 1}\n`);
    append("0000000000 65535 f \n");
    for (let objectId = 1; objectId <= infoObjectId; objectId += 1) {
      append(`${String(offsets[objectId]).padStart(10, "0")} 00000 n \n`);
    }
    append(`trailer\n<< /Size ${infoObjectId + 1} /Root 1 0 R /Info ${infoObjectId} 0 R >>\n`);
    append(`startxref\n${xrefOffset}\n%%EOF\n`);

    const output = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) {
      output.set(chunk, offset);
      offset += chunk.length;
    }
    return output;
  };

  const lookupPanel = (cellToPanel, cellId) => {
    if (cellToPanel instanceof Map) return cellToPanel.get(cellId);
    return cellToPanel?.[cellId] || cellToPanel?.[String(cellId)];
  };

  const drawCategoryLegend = (page, items, width, top) => {
    const columns = 4;
    const margin = 36;
    const columnWidth = (width - margin * 2) / columns;
    page.text("Color key", margin, top + 23, { size: 13, color: [37, 48, 68], bold: true });
    items.forEach((item, index) => {
      const column = index % columns;
      const row = Math.floor(index / columns);
      const x = margin + column * columnWidth;
      const y = top + 47 + row * 27;
      page.rect(x, y - 11, 15, 15, {
        fill: item.color,
        stroke: invertRgb(item.color),
        lineWidth: 0.5,
      });
      page.text(truncateText(item.label, 11, columnWidth - 28), x + 23, y + 1, {
        size: 11,
        color: [65, 80, 100],
      });
    });
  };

  const buildSpatialPage = async (options) => {
    const width = Math.max(800, Number(options.width || DEFAULT_WIDTH));
    const headerHeight = 82;
    const outerMargin = 36;
    const layoutWidth = Math.max(1, Number(options.layoutWidth || 1));
    const layoutHeight = Math.max(1, Number(options.layoutHeight || 1));
    const layoutScale = (width - outerMargin * 2) / layoutWidth;
    const stageHeight = Math.max(520, Math.ceil(layoutHeight * layoutScale + outerMargin * 2));
    const legendItems = Array.isArray(options.legend?.items) ? options.legend.items : [];
    const legendRows = Math.ceil(legendItems.length / 4);
    const legendHeight = options.legend?.type === "categories"
      ? Math.max(76, 55 + legendRows * 27)
      : options.legend?.type === "gradient" ? 78 : 0;
    const height = headerHeight + stageHeight + legendHeight;
    const page = new VectorPage(width, height);
    const sx = (value) => outerMargin + Number(value) * layoutScale;
    const sy = (value) => headerHeight + outerMargin + Number(value) * layoutScale;

    page.rect(0, 0, width, height, { fill: [238, 241, 244] });
    page.rect(0, 0, width, headerHeight, { fill: [255, 255, 255] });
    page.text(options.title || "Spatial expression", 28, 36, {
      size: 24,
      color: [37, 48, 68],
      bold: true,
    });
    page.text(options.subtitle || "", 30, 63, { size: 14, color: [102, 112, 133] });

    for (const panel of options.panels || []) {
      page.rect(
        sx(panel.x),
        sy(panel.y),
        Number(panel.width) * layoutScale,
        Number(panel.height) * layoutScale,
        { fill: [255, 255, 255], stroke: [184, 193, 204], lineWidth: 0.7 },
      );
    }

    const drawn = new Set();
    try {
      for await (const cell of options.cells || []) {
        const cellId = Number(cell?.id);
        if (!Number.isFinite(cellId) || drawn.has(cellId)) continue;
        const panel = lookupPanel(options.cellToPanel, cellId);
        const bbox = cell?.bbox;
        if (!panel || !Array.isArray(bbox) || bbox.length < 2) continue;
        const fill = options.colorForCell(cell);
        if (!Array.isArray(fill) || fill.length < 3) continue;
        for (const contour of cell.contours || []) {
          if (!Array.isArray(contour) || contour.length < 3) continue;
          const points = [];
          for (const point of contour) {
            if (!Array.isArray(point) || point.length < 2) continue;
            const sourceX = Number(bbox[0]) + Number(point[0]);
            const sourceY = Number(bbox[1]) + Number(point[1]);
            const layoutX = Number(panel.x) + (sourceX - Number(panel.bbox[0])) * Number(panel.scaleX);
            const layoutY = Number(panel.y) + (sourceY - Number(panel.bbox[1])) * Number(panel.scaleY);
            points.push([sx(layoutX), sy(layoutY)]);
          }
          page.polygon(points, { fill, stroke: invertRgb(fill), lineWidth: 0.5 });
        }
        drawn.add(cellId);
        await page.checkpoint();
      }
    } catch (error) {
      await page.abort(error);
      throw error;
    }

    for (const panel of options.panels || []) {
      const count = Number(panel.assignedCellCount || panel.cellIds?.length || 0);
      const label = `${panel.label || panel.id || ""} - ${count.toLocaleString("en-US")} cells`;
      const labelX = sx(panel.x) + 6;
      const baselineY = sy(panel.y) - 10;
      page.rect(labelX - 4, baselineY - 14, approximateTextWidth(label, 13) + 10, 19, {
        fill: [255, 255, 255],
      });
      page.text(label, labelX, baselineY, { size: 13, color: [37, 48, 68] });
    }

    if (legendHeight) {
      const legendTop = headerHeight + stageHeight;
      page.rect(0, legendTop, width, legendHeight, { fill: [255, 255, 255] });
      page.line(0, legendTop, width, legendTop, { color: [214, 223, 239], lineWidth: 1 });
      if (options.legend.type === "gradient") {
        const legendX = 36;
        page.text(options.legend.label || "Expression", legendX, legendTop + 25, {
          size: 12,
          color: [37, 48, 68],
          bold: true,
        });
        page.text(formatNumber(options.legend.min), legendX, legendTop + 49, {
          size: 11,
          color: [102, 112, 133],
          bold: true,
        });
        page.gradient(legendX + 58, legendTop + 36, 260, 12, options.legend.colors || REDS);
        page.text(formatNumber(options.legend.max), legendX + 330, legendTop + 49, {
          size: 11,
          color: [102, 112, 133],
          bold: true,
        });
      } else {
        drawCategoryLegend(page, legendItems, width, legendTop);
      }
    }

    await page.checkpoint();

    return { page, drawnCells: drawn.size };
  };

  const dotRadius = (pctExpr, range) => {
    if (!Number.isFinite(Number(pctExpr))) return 3;
    if (range.max <= range.min) return 9;
    const fraction = Math.max(0, Math.min(1, (Number(pctExpr) - range.min) / (range.max - range.min)));
    return 3 + fraction * 12;
  };

  const buildDotplotPage = (payload, options = {}) => {
    const clusters = Array.isArray(payload?.clusters) ? payload.clusters : [];
    if (!clusters.length) return null;
    const width = Math.max(800, Number(options.width || DEFAULT_WIDTH));
    const keyColumns = 4;
    const keyRows = Math.ceil(clusters.length / keyColumns);
    const height = 300 + keyRows * 27;
    const page = new VectorPage(width, height);
    const left = 190;
    const right = width - 210;
    const titleY = 36;
    const centerY = 108;
    const band = Math.max(1, right - left) / clusters.length;
    const scaledValues = clusters.map((cluster) => Number(cluster.avgExprScaled)).filter(Number.isFinite);
    const pctValues = clusters.map((cluster) => Number(cluster.pctExpr)).filter(Number.isFinite);
    const useScaled = scaledValues.length > 0;
    const colorRange = useScaled
      ? { vmin: Math.min(...scaledValues), vmax: Math.max(...scaledValues) }
      : { vmin: 0, vmax: Math.max(0, ...clusters.map((cluster) => Number(cluster.avgExpr) || 0)) };
    const pctRange = pctValues.length
      ? { min: Math.min(...pctValues), max: Math.max(...pctValues) }
      : { min: 0, max: 100 };

    page.rect(0, 0, width, height, { fill: [255, 255, 255] });
    page.text("Seurat Clusters", left, titleY, { size: 18, color: [37, 48, 68], bold: true });
    page.line(left, centerY, right, centerY, { color: [215, 221, 229], lineWidth: 1 });
    page.text(payload.gene || "-", left - 14, centerY + 5, {
      size: 14,
      color: [37, 48, 68],
      align: "right",
      bold: true,
    });

    clusters.forEach((cluster, index) => {
      const x = left + band * (index + 0.5);
      const avgExpr = Number(cluster.avgExpr) || 0;
      const scaledValue = Number(cluster.avgExprScaled);
      const pctExpr = Number(cluster.pctExpr) || 0;
      const radius = dotRadius(pctExpr, pctRange);
      const value = useScaled && Number.isFinite(scaledValue) ? scaledValue : avgExpr;
      page.circle(x, centerY, radius, {
        fill: colorForValue(value, colorRange),
        stroke: [122, 28, 22],
        lineWidth: 0.8,
      });
      page.text(cluster.label || cluster.id || "", x, centerY + 41, {
        size: 13,
        color: [65, 80, 100],
        align: "center",
      });
    });

    const legendLeft = width - 162;
    const legendTop = centerY - 42;
    const legendWidth = 116;
    page.text(useScaled ? "Scaled avg expr" : "Avg expr", legendLeft, legendTop - 5, {
      size: 11,
      color: [37, 48, 68],
    });
    page.gradient(legendLeft, legendTop, legendWidth, 10, REDS);
    page.text(formatNumber(colorRange.vmin, 1), legendLeft, legendTop + 26, {
      size: 11,
      color: [102, 112, 133],
    });
    page.text(formatNumber(colorRange.vmax, 1), legendLeft + legendWidth, legendTop + 26, {
      size: 11,
      color: [102, 112, 133],
      align: "right",
    });

    const sizeY = legendTop + 72;
    const sizes = pctRange.max > pctRange.min
      ? [pctRange.min, pctRange.min + (pctRange.max - pctRange.min) / 2, pctRange.max]
      : [pctRange.min];
    page.text("% cells", legendLeft, sizeY - 26, { size: 11, color: [37, 48, 68] });
    sizes.forEach((pct, index) => {
      const x = legendLeft + (sizes.length === 1 ? 56 : 12 + index * 40);
      page.circle(x, sizeY, dotRadius(pct, pctRange), {
        fill: [252, 187, 161],
        stroke: [138, 29, 24],
        lineWidth: 1,
      });
      page.text(formatNumber(pct, 1), x, sizeY + 32, {
        size: 11,
        color: [102, 112, 133],
        align: "center",
      });
    });

    const keyTop = 205;
    const columnWidth = (width - 72) / keyColumns;
    page.line(36, keyTop - 20, width - 36, keyTop - 20, { color: [214, 223, 239], lineWidth: 1 });
    page.text("Cluster key", 36, keyTop, { size: 12, color: [37, 48, 68], bold: true });
    clusters.forEach((cluster, index) => {
      const column = index % keyColumns;
      const row = Math.floor(index / keyColumns);
      const label = `${cluster.label || cluster.id || ""} - ${cluster.name || "Unnamed cluster"}`;
      page.text(truncateText(label, 10, columnWidth - 14), 36 + column * columnWidth, keyTop + 28 + row * 27, {
        size: 10,
        color: [65, 80, 100],
      });
    });
    page.text("Dot size: pctExpr | Color: avgExprScaled | Source: Potato Agent Interface", 36, height - 18, {
      size: 10,
      color: [102, 112, 133],
    });
    return page;
  };

  const render = async (options) => {
    if (!options?.spatial) throw new Error("Spatial export data is required");
    const spatialResult = await buildSpatialPage(options.spatial);
    const pages = [await spatialResult.page.finalize()];
    const dotplotPage = options.dotplot ? buildDotplotPage(options.dotplot, options.dotplotOptions) : null;
    if (dotplotPage) pages.push(await dotplotPage.finalize());
    const bytes = assemblePdf(pages, options.title || options.spatial.title || "Spatial expression");
    return {
      blob: new Blob([bytes], { type: "application/pdf" }),
      bytes,
      pageCount: pages.length,
      drawnCells: spatialResult.drawnCells,
    };
  };

  return {
    DEFAULT_WIDTH,
    PDF_POINTS_PER_PIXEL,
    REDS,
    VectorPage,
    assemblePdf,
    buildDotplotPage,
    buildSpatialPage,
    colorForValue,
    render,
  };
}));
