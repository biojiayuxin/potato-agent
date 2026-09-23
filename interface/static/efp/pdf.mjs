/** PDF 1.4 export with flattened vector paths, embedded fonts and editable text. */
const encoder = new TextEncoder();
const PDF_SCALE = 0.75;
const n = value => String(Number(Number(value).toFixed(5)));
const rgb = color => {
  if (!/^#[0-9a-f]{6}$/i.test(color)) throw new Error(`Unsupported PDF color: ${color}`);
  return [1, 3, 5].map(index => n(parseInt(color.slice(index, index + 2), 16) / 255)).join(' ');
};
const literal = value => String(value).replace(/[\\()]/g, character => '\\' + character);
const unicode = value => '<FEFF' + Array.from(String(value), character => {
  const units = [];
  for (let i = 0; i < character.length; i++) units.push(character.charCodeAt(i).toString(16).padStart(4, '0'));
  return units.join('');
}).join('') + '>';

let assetsPromise;
async function assets() {
  if (!assetsPromise) assetsPromise = (async () => {
    const base = new URL('.', import.meta.url);
    const response = await fetch(new URL('pdf-geometry.json?v=20260923-pdf1', base));
    if (!response.ok) throw new Error('PDF drawing data could not be loaded. Please retry.');
    const geometry = await response.json();
    const fonts = await Promise.all(geometry.fonts.map(async font => {
      const response = await fetch(new URL(font.file + '?v=20260923-pdf1', base));
      if (!response.ok) throw new Error('PDF fonts could not be loaded. Please retry.');
      return new Uint8Array(await response.arrayBuffer());
    }));
    return {geometry, fonts};
  })().catch(error => { assetsPromise = null; throw error; });
  return assetsPromise;
}

async function stream(bytes, attributes = '') {
  let data = typeof bytes === 'string' ? encoder.encode(bytes) : bytes;
  let filter = '';
  if (typeof CompressionStream === 'function') {
    data = new Uint8Array(await new Response(new Blob([data]).stream().pipeThrough(new CompressionStream('deflate'))).arrayBuffer());
    filter = ' /Filter /FlateDecode';
  }
  return [encoder.encode(`<< /Length ${data.length}${filter}${attributes} >>\nstream\n`), data, encoder.encode('\nendstream')];
}

async function documentBytes(width, height, commands, geometry, fonts, metadata) {
  const objects = [];
  const add = object => { objects.push(typeof object === 'string' ? [encoder.encode(object)] : object); return objects.length; };
  add('<< /Type /Catalog /Pages 2 0 R >>');
  add('<< /Type /Pages /Kids [3 0 R] /Count 1 >>');
  add(''); // Page resources are completed after allocating the fonts.
  const cmapLines = Array.from({length: 95}, (_, i) => {
    const code = (i + 32).toString(16).padStart(2, '0');
    return `<${code}> <00${code}>`;
  });
  const cmap = add(await stream('/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n'
    + '/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n'
    + '/CMapName /PotatoASCII def\n/CMapType 2 def\n1 begincodespacerange\n<20> <7e>\nendcodespacerange\n'
    + `95 beginbfchar\n${cmapLines.join('\n')}\nendbfchar\nendcmap\nCMapName currentdict /CMap defineresource pop\nend\nend`));
  const fontIds = [];
  for (const [index, font] of geometry.fonts.entries()) {
    const file = add(await stream(fonts[index], ` /Length1 ${fonts[index].length}`));
    const descriptor = add(`<< /Type /FontDescriptor /FontName /${font.name} /Flags 32 /FontBBox [${font.bbox.map(n).join(' ')}]`
      + ` /ItalicAngle 0 /Ascent ${n(font.ascent)} /Descent ${n(font.descent)} /CapHeight ${n(font.capHeight)}`
      + ` /StemV ${index ? 120 : 80} /FontFile2 ${file} 0 R >>`);
    fontIds.push(add(`<< /Type /Font /Subtype /TrueType /BaseFont /${font.name} /FirstChar 32 /LastChar 126`
      + ` /Widths [${font.widths.map(n).join(' ')}] /Encoding /WinAnsiEncoding /FontDescriptor ${descriptor} 0 R /ToUnicode ${cmap} 0 R >>`));
  }
  const content = add(await stream(commands.join('\n')));
  objects[2] = [encoder.encode(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${n(width * PDF_SCALE)} ${n(height * PDF_SCALE)}]`
    + ` /Resources << /Font << /F1 ${fontIds[0]} 0 R /F2 ${fontIds[1]} 0 R >> >> /Contents ${content} 0 R >>`)];
  const info = add(`<< /Title ${unicode(metadata.geneId + ' - Tissue Expression Map (eFP)')}`
    + ` /Creator (Potato Interface eFP vector PDF) /PotatoExpression ${unicode(JSON.stringify(metadata))} >>`);
  const chunks = [], offsets = [0];
  let length = 0;
  const append = bytes => {
    const part = typeof bytes === 'string' ? encoder.encode(bytes) : bytes;
    chunks.push(part); length += part.length;
  };
  append('%PDF-1.4\n% Potato eFP vector export\n');
  objects.forEach((parts, index) => {
    offsets.push(length);
    append(`${index + 1} 0 obj\n`);
    parts.forEach(append);
    append('\nendobj\n');
  });
  const xref = length;
  append(`xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`);
  offsets.slice(1).forEach(offset => append(`${String(offset).padStart(10, '0')} 00000 n \n`));
  append(`trailer\n<< /Size ${objects.length + 1} /Root 1 0 R /Info ${info} 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
  return new Blob(chunks, {type: 'application/pdf'});
}

/** Layout comes from the same complete figure used for the SVG reference. */
export async function vectorPdf(svgText) {
  const doc = new DOMParser().parseFromString(svgText, 'image/svg+xml');
  const svg = doc.documentElement;
  const {geometry, fonts} = await assets();
  const width = Number(svg.getAttribute('width')), height = Number(svg.getAttribute('height'));
  const commands = [`q ${PDF_SCALE} 0 0 ${-PDF_SCALE} 0 ${n(height * PDF_SCALE)} cm`];
  const push = (...parts) => commands.push(...parts);
  const rect = (x, y, w, h, color) => push(`${rgb(color)} rg ${n(x)} ${n(y)} ${n(w)} ${n(h)} re f`);
  const value = (el, attribute, fallback = 0) => Number(el.getAttribute(attribute) ?? fallback);
  function drawing(el) {
    const box = (el.getAttribute('viewBox') || '').split(/\s+/).map(Number);
    if (box.join() !== geometry.viewBox.join()) throw new Error('The PDF template does not match the drawing.');
    const groups = [...el.querySelectorAll('[data-tissue]')];
    if (groups.length !== Object.keys(geometry.regions).length) throw new Error('The PDF tissue regions do not match.');
    push('q', `${n(value(el, 'width') / box[2])} 0 0 ${n(value(el, 'height') / box[3])} ${n(value(el, 'x'))} ${n(value(el, 'y'))} cm`);
    for (const group of groups) {
      const path = geometry.regions[group.dataset.tissue];
      if (!path) throw new Error(`Missing PDF region: ${group.dataset.tissue}`);
      push(`${rgb(group.getAttribute('fill'))} rg`, path, 'f');
    }
    push(`0 0 0 RG ${n(geometry.strokeWidth)} w 0 J 0 j 10 M`);
    for (const path of geometry.linework) push(path, 'S');
    push('Q');
  }
  function render(el) {
    const color = el.getAttribute('fill') || '#14213d';
    if (el.localName === 'svg') { drawing(el); return; }
    if (['metadata', 'desc', 'defs', 'title'].includes(el.localName)) return;
    if (el.localName === 'g') { [...el.children].forEach(render); return; }
    if (el.localName === 'rect') {
      const x = value(el, 'x'), y = value(el, 'y'), w = value(el, 'width'), h = value(el, 'height');
      if (color.startsWith('url(#')) {
        const gradient = doc.getElementById(color.slice(5, -1));
        const stops = [...gradient.children].map(stop => ({
          offset: parseFloat(stop.getAttribute('offset')) / 100,
          rgb: [1, 3, 5].map(i => parseInt(stop.getAttribute('stop-color').slice(i, i + 2), 16)),
        }));
        for (let i = 0; i < 256; i++) {
          const t = i / 255;
          const right = Math.max(1, stops.findIndex(stop => stop.offset >= t));
          const a = stops[right - 1], b = stops[right], fraction = (t - a.offset) / (b.offset - a.offset);
          const fill = '#' + a.rgb.map((v, j) => Math.round(v + (b.rgb[j] - v) * fraction).toString(16).padStart(2, '0')).join('');
          rect(x + w * i / 256, y, w / 256 + (i < 255 ? 0.01 : 0), h, fill);
        }
      } else rect(x, y, w, h, color);
      return;
    }
    if (el.localName === 'line') {
      push(`${rgb(el.getAttribute('stroke'))} RG 1 w ${n(value(el, 'x1'))} ${n(value(el, 'y1'))} m ${n(value(el, 'x2'))} ${n(value(el, 'y2'))} l S`);
      return;
    }
    if (el.localName === 'text') {
      const text = el.textContent;
      if (/[^\x20-\x7e]/.test(text)) throw new Error('PDF export currently supports English tissue labels and gene IDs.');
      const bold = value(el, 'font-weight') >= 600;
      const font = geometry.fonts[bold ? 1 : 0], size = value(el, 'font-size', 13);
      const textWidth = [...text].reduce((sum, character) => sum + font.widths[character.charCodeAt(0) - 32], 0) * size / 1000;
      const outputWidth = value(el, 'textLength', textWidth), scale = textWidth ? outputWidth / textWidth : 1;
      const anchor = el.getAttribute('text-anchor');
      const x = value(el, 'x') - (anchor === 'end' ? outputWidth : anchor === 'middle' ? outputWidth / 2 : 0);
      push(`${rgb(color)} rg BT /F${bold ? 2 : 1} ${n(size)} Tf ${n(scale)} 0 0 -1 ${n(x)} ${n(value(el, 'y'))} Tm (${literal(text)}) Tj ET`);
      return;
    }
    throw new Error(`Unsupported PDF figure element: ${el.localName}`);
  }
  [...svg.children].forEach(render);
  push('Q');
  const metadata = JSON.parse(svg.querySelector('metadata').textContent);
  return documentBytes(width, height, commands, geometry, fonts, metadata);
}
