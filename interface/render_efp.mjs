/** One-shot server renderer. Input is trusted API data over stdin, never code. */
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {adaptExpression} from './static/efp/expression.mjs';
import {prepareExpression} from './static/efp/potato-efp.mjs';
import {buildFigure, expressionMetadata} from './static/efp/figure.mjs';
import {vectorPdf} from './static/efp/pdf.mjs';

try {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  const {format, data} = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  const adapted = adaptExpression(data);
  const result = prepareExpression(adapted.payload);
  if (format === 'json') {
    process.stdout.write(JSON.stringify({
      ...expressionMetadata(result, adapted), unit: result.unit, ticks: result.ticks,
      regions: result.rows, missingIds: result.missingIds,
    }));
  } else if (format === 'pdf') {
    const base = new URL('./static/efp/', import.meta.url);
    const geometry = JSON.parse(await readFile(new URL('pdf-geometry.json', base), 'utf8'));
    const checkedRead = async (file, checksum) => {
      const bytes = await readFile(new URL(file, base));
      if (createHash('sha256').update(bytes).digest('hex') !== checksum) throw new Error('Stale eFP PDF assets.');
      return bytes;
    };
    await checkedRead('potato-template.svg', geometry.templateSha256);
    const fonts = await Promise.all(geometry.fonts.map(font => checkedRead(font.file, font.sha256)));
    const blob = await vectorPdf(buildFigure(result, adapted), {geometry, fonts});
    process.stdout.write(Buffer.from(await blob.arrayBuffer()));
  } else throw new Error('Unknown eFP output format.');
} catch {
  // No paths or query payloads in stderr; the API returns a controlled error.
  process.stderr.write('eFP rendering failed. Check the expression data and PDF assets.\n');
  process.exitCode = 1;
}
