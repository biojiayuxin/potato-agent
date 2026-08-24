(function attachBulkRnaSeqPdf(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.BulkRnaSeqPdf = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, () => {
  const PDF_SCALE = 0.75;
  const VIEWER_TOOLBAR_HEIGHT = 64;
  const VIEWER_LEGEND_HEIGHT = 42;

  const formatPdfNumber = (value) => {
    const rounded = Math.abs(Number(value)) < 0.000001 ? 0 : Number(value);
    return String(Number(rounded.toFixed(4)));
  };

  const colorComponent = (value) => formatPdfNumber(
    Math.max(0, Math.min(255, Number(value))) / 255,
  );

  const parseColor = (value) => {
    const text = String(value || '').trim().toLowerCase();
    const hex = text.match(/^#([0-9a-f]{6})$/i);
    if (hex) {
      return {
        alpha: 1,
        rgb: [
          parseInt(hex[1].slice(0, 2), 16),
          parseInt(hex[1].slice(2, 4), 16),
          parseInt(hex[1].slice(4, 6), 16),
        ],
      };
    }
    const rgb = text.match(/^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)(?:\s*,\s*([\d.]+))?\s*\)$/);
    if (rgb) {
      const alpha = rgb[4] === undefined ? 1 : Math.max(0, Math.min(1, Number(rgb[4])));
      return {
        alpha,
        rgb: [1, 2, 3].map((index) => Number(rgb[index])),
      };
    }
    return { alpha: 1, rgb: [0, 0, 0] };
  };

  const normalizedPdfText = (value) => String(value ?? '')
    .replace(/[\u2010-\u2015]/g, '-')
    .replace(/[\u2018\u2019]/g, "'")
    .replace(/[\u201c\u201d]/g, '"')
    .replace(/\u2026/g, '...')
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '');

  const escapePdfText = (value) => {
    let escaped = '';
    for (const character of normalizedPdfText(value)) {
      const code = character.codePointAt(0);
      if (character === '\\' || character === '(' || character === ')') {
        escaped += `\\${character}`;
      } else if (code >= 32 && code <= 126) {
        escaped += character;
      } else if (character === '\n' || character === '\r' || character === '\t') {
        escaped += ' ';
      } else {
        escaped += '?';
      }
    }
    return escaped;
  };

  const fontDetails = (font) => {
    const text = String(font || '10px sans-serif');
    const sizeMatch = text.match(/([\d.]+)px/);
    const weightMatch = text.match(/(?:^|\s)([1-9][0-9]{2}|bold|bolder)(?:\s|$)/i);
    const weight = weightMatch?.[1]?.toLowerCase();
    return {
      size: sizeMatch ? Number(sizeMatch[1]) : 10,
      bold: weight === 'bold' || weight === 'bolder' || Number(weight) >= 600,
    };
  };

  const approximateTextWidth = (text, size, bold) => {
    let units = 0;
    for (const character of normalizedPdfText(text)) {
      if (/[ilI1.,'|!]/.test(character)) units += 0.28;
      else if (/[MW@%&]/.test(character)) units += 0.9;
      else if (/[A-Z0-9]/.test(character)) units += 0.64;
      else if (/\s/.test(character)) units += 0.28;
      else units += 0.52;
    }
    return units * size * (bold ? 1.03 : 1);
  };

  const byteLength = (value) => new TextEncoder().encode(value).length;

  const assemblePdf = (width, height, content, alphaStates) => {
    const pageWidth = formatPdfNumber(width * PDF_SCALE);
    const pageHeight = formatPdfNumber(height * PDF_SCALE);
    const alphaEntries = Array.from(alphaStates.entries());
    const alphaObjectStart = 6;
    const contentObjectId = alphaObjectStart + alphaEntries.length;
    const alphaResources = alphaEntries.map(([, name], index) => (
      `/${name} ${alphaObjectStart + index} 0 R`
    )).join(' ');
    const objects = [
      '<< /Type /Catalog /Pages 2 0 R >>',
      '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
      `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${pageWidth} ${pageHeight}] /Resources << /Font << /F1 4 0 R /F2 5 0 R >> /ExtGState << ${alphaResources} >> >> /Contents ${contentObjectId} 0 R >>`,
      '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>',
      '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>',
      ...alphaEntries.map(([alpha]) => (
        `<< /Type /ExtGState /ca ${formatPdfNumber(alpha)} /CA ${formatPdfNumber(alpha)} >>`
      )),
      `<< /Length ${byteLength(content)} >>\nstream\n${content}\nendstream`,
    ];

    let source = '%PDF-1.4\n% Potato Agent vector export\n';
    const offsets = [0];
    objects.forEach((object, index) => {
      offsets.push(byteLength(source));
      source += `${index + 1} 0 obj\n${object}\nendobj\n`;
    });
    const xrefOffset = byteLength(source);
    source += `xref\n0 ${objects.length + 1}\n`;
    source += '0000000000 65535 f \n';
    offsets.slice(1).forEach((offset) => {
      source += `${String(offset).padStart(10, '0')} 00000 n \n`;
    });
    source += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\n`;
    source += `startxref\n${xrefOffset}\n%%EOF\n`;
    return new TextEncoder().encode(source);
  };

  const createVectorContext = (width, height) => {
    const operators = [
      'q',
      `${formatPdfNumber(PDF_SCALE)} 0 0 ${formatPdfNumber(-PDF_SCALE)} 0 ${formatPdfNumber(height * PDF_SCALE)} cm`,
    ];
    const styleStack = [];
    const alphaStates = new Map([[1, 'GS0']]);
    let path = [];

    const setColor = (value, stroke) => {
      const { alpha, rgb } = parseColor(value);
      let alphaName = alphaStates.get(alpha);
      if (!alphaName) {
        alphaName = `GS${alphaStates.size}`;
        alphaStates.set(alpha, alphaName);
      }
      operators.push(`${rgb.map(colorComponent).join(' ')} ${stroke ? 'RG' : 'rg'}`);
      operators.push(`/${alphaName} gs`);
    };

    const context = {
      fillStyle: '#000000',
      strokeStyle: '#000000',
      lineWidth: 1,
      font: '10px sans-serif',
      textAlign: 'start',
      textBaseline: 'alphabetic',

      save() {
        styleStack.push({
          fillStyle: this.fillStyle,
          strokeStyle: this.strokeStyle,
          lineWidth: this.lineWidth,
          font: this.font,
          textAlign: this.textAlign,
          textBaseline: this.textBaseline,
        });
        operators.push('q');
      },

      restore() {
        const style = styleStack.pop();
        if (style) Object.assign(this, style);
        operators.push('Q');
      },

      translate(x, y) {
        operators.push(`1 0 0 1 ${formatPdfNumber(x)} ${formatPdfNumber(y)} cm`);
      },

      rotate(angle) {
        const cosine = formatPdfNumber(Math.cos(angle));
        const sine = formatPdfNumber(Math.sin(angle));
        operators.push(`${cosine} ${sine} ${formatPdfNumber(-Math.sin(angle))} ${cosine} 0 0 cm`);
      },

      clearRect() {},

      fillRect(x, y, rectWidth, rectHeight) {
        setColor(this.fillStyle, false);
        operators.push(`${formatPdfNumber(x)} ${formatPdfNumber(y)} ${formatPdfNumber(rectWidth)} ${formatPdfNumber(rectHeight)} re f`);
      },

      strokeRect(x, y, rectWidth, rectHeight) {
        setColor(this.strokeStyle, true);
        operators.push(`${formatPdfNumber(this.lineWidth)} w`);
        operators.push(`${formatPdfNumber(x)} ${formatPdfNumber(y)} ${formatPdfNumber(rectWidth)} ${formatPdfNumber(rectHeight)} re S`);
      },

      beginPath() {
        path = [];
      },

      moveTo(x, y) {
        path.push(`${formatPdfNumber(x)} ${formatPdfNumber(y)} m`);
      },

      lineTo(x, y) {
        path.push(`${formatPdfNumber(x)} ${formatPdfNumber(y)} l`);
      },

      stroke() {
        if (!path.length) return;
        setColor(this.strokeStyle, true);
        operators.push(`${formatPdfNumber(this.lineWidth)} w`);
        operators.push(...path, 'S');
        path = [];
      },

      measureText(text) {
        const details = fontDetails(this.font);
        return { width: approximateTextWidth(text, details.size, details.bold) };
      },

      fillText(text, x, y, maxWidth, canvasMetrics) {
        const details = fontDetails(this.font);
        const pdfWidth = approximateTextWidth(text, details.size, details.bold);
        const canvasWidth = Number(canvasMetrics?.width);
        const measuredWidth = Number.isFinite(canvasWidth) ? canvasWidth : pdfWidth;
        const renderedWidth = Number.isFinite(maxWidth) && maxWidth > 0
          ? Math.min(measuredWidth, maxWidth)
          : measuredWidth;
        const widthScale = pdfWidth > 0 ? renderedWidth / pdfWidth : 1;
        let offsetX = 0;
        if (this.textAlign === 'center') offsetX = -renderedWidth / 2;
        if (this.textAlign === 'right' || this.textAlign === 'end') offsetX = -renderedWidth;
        let baselineY = Number(y);
        if (this.textBaseline === 'middle') baselineY += details.size * 0.35;
        if (this.textBaseline === 'top' || this.textBaseline === 'hanging') baselineY += details.size * 0.82;

        setColor(this.fillStyle, false);
        operators.push('q');
        operators.push(`1 0 0 -1 ${formatPdfNumber(x)} ${formatPdfNumber(baselineY)} cm`);
        operators.push('BT');
        operators.push(`/${details.bold ? 'F2' : 'F1'} ${formatPdfNumber(details.size)} Tf`);
        if (Math.abs(widthScale - 1) > 0.0001) {
          operators.push(`${formatPdfNumber(widthScale * 100)} Tz`);
        }
        operators.push(`1 0 0 1 ${formatPdfNumber(offsetX)} 0 Tm`);
        operators.push(`(${escapePdfText(text)}) Tj`);
        operators.push('ET');
        operators.push('Q');
      },
    };

    return {
      context,
      toUint8Array() {
        const content = `${operators.join('\n')}\nQ`;
        return assemblePdf(width, height, content, alphaStates);
      },
      toBlob() {
        return new Blob([this.toUint8Array()], { type: 'application/pdf' });
      },
    };
  };

  const viewerExportHeight = (stageHeight) => (
    VIEWER_TOOLBAR_HEIGHT + Number(stageHeight) + VIEWER_LEGEND_HEIGHT
  );

  const drawViewerChrome = (context, width, stageHeight, presentation) => {
    const legendTop = VIEWER_TOOLBAR_HEIGHT + Number(stageHeight);
    const totalHeight = viewerExportHeight(stageHeight);
    const colors = Array.isArray(presentation.legendColors)
      && presentation.legendColors.length
      ? presentation.legendColors
      : ['#f8fafc', '#ff0000'];
    const barWidth = Math.min(320, Math.max(160, width * 0.25));
    const barHeight = 12;
    const barLeft = width - 46 - barWidth;
    const barTop = legendTop + 15;

    context.save();
    context.fillStyle = '#ffffff';
    context.fillRect(0, 0, width, VIEWER_TOOLBAR_HEIGHT);
    context.fillStyle = 'rgba(255, 255, 255, 0.8)';
    context.fillRect(0, legendTop, width, VIEWER_LEGEND_HEIGHT);

    context.textAlign = 'left';
    context.textBaseline = 'middle';
    context.fillStyle = '#14213d';
    context.font = '850 16px Inter, system-ui, sans-serif';
    context.fillText(presentation.title, 14, 22);
    context.fillStyle = '#64748b';
    context.font = '700 12px Inter, system-ui, sans-serif';
    context.fillText(presentation.summary, 14, 43);

    colors.forEach((color, index) => {
      const segmentLeft = barLeft + barWidth * index / colors.length;
      context.fillStyle = color;
      context.fillRect(
        segmentLeft,
        barTop,
        barWidth / colors.length + 0.05,
        barHeight,
      );
    });
    context.strokeStyle = 'rgba(100, 116, 139, 0.28)';
    context.lineWidth = 1;
    context.strokeRect(barLeft, barTop, barWidth, barHeight);
    context.fillStyle = '#64748b';
    context.font = '800 12px Inter, system-ui, sans-serif';
    context.textAlign = 'right';
    context.fillText(presentation.legendMin, barLeft - 9, barTop + barHeight / 2);
    context.textAlign = 'left';
    context.fillText(
      presentation.legendMax,
      barLeft + barWidth + 9,
      barTop + barHeight / 2,
    );

    context.strokeStyle = '#d6dfef';
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(0, VIEWER_TOOLBAR_HEIGHT);
    context.lineTo(width, VIEWER_TOOLBAR_HEIGHT);
    context.moveTo(0, legendTop);
    context.lineTo(width, legendTop);
    context.stroke();
    context.strokeRect(0.5, 0.5, width - 1, totalHeight - 1);
    context.restore();
  };

  const createMirroredContext = (canvasContext, vectorContext) => new Proxy(
    canvasContext,
    {
      get(target, property) {
        const canvasValue = target[property];
        if (typeof canvasValue !== 'function') return canvasValue;
        return (...args) => {
          if (property === 'measureText') return canvasValue.apply(target, args);
          const result = canvasValue.apply(target, args);
          const vectorMethod = vectorContext[property];
          if (typeof vectorMethod !== 'function') return result;
          if (property === 'fillText') {
            const metrics = target.measureText(args[0]);
            vectorMethod.call(
              vectorContext,
              args[0],
              args[1],
              args[2],
              args[3],
              metrics,
            );
          } else {
            vectorMethod.apply(vectorContext, args);
          }
          return result;
        };
      },
      set(target, property, value) {
        target[property] = value;
        if (property in vectorContext) vectorContext[property] = value;
        return true;
      },
    },
  );

  return {
    VIEWER_TOOLBAR_HEIGHT,
    createMirroredContext,
    createVectorContext,
    drawViewerChrome,
    viewerExportHeight,
  };
}));
