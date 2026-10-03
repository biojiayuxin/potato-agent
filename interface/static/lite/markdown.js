import markedCjkFriendly from './vendor/marked-cjk-friendly.js?v=0.1.2';

const isEscaped = (source, index) => {
  let backslashes = 0;
  while (index > 0 && source[--index] === '\\') backslashes += 1;
  return backslashes % 2 === 1;
};

const findMathEnd = (source, closing, start) => {
  let braces = 0;
  for (let index = start; index < source.length; index += 1) {
    if (braces === 0 && source.startsWith(closing, index)) return index;
    const character = source[index];
    if (character === '\\') index += 1;
    else if (character === '{') braces += 1;
    else if (character === '}') braces = Math.max(0, braces - 1);
  }
  return -1;
};

const readMath = (source, block = false) => {
  const leading = block ? /^ {0,3}/.exec(source)[0].length : 0;
  const body = source.slice(leading);
  const opening = ['\\[', '$$', '\\(', '$'].find((delimiter) => body.startsWith(delimiter));
  if (!opening || (block && opening !== '\\[' && opening !== '$$')) return undefined;
  const closing = { '\\[': '\\]', '\\(': '\\)', '$$': '$$', '$': '$' }[opening];
  const end = findMathEnd(body, closing, opening.length);
  if (end < 0) return undefined;
  const text = body.slice(opening.length, end);
  if (!text.trim()) return undefined;
  // Single dollars must stay on one line and avoid common currency strings
  // such as "$5 and $10". Escaped dollars are handled by Marked's escape token.
  if (opening === '$' && (/^\s|\s$|\n/.test(text) || /\d/.test(body[end + 1] || ''))) return undefined;
  let raw = source.slice(0, leading + end + closing.length);
  if (block) {
    const tail = source.slice(raw.length);
    const lineEnd = /^[ \t]*(?:\n|$)/.exec(tail);
    if (!lineEnd) return undefined;
    raw += lineEnd[0];
  }
  return { raw, text, display: opening === '\\[' || opening === '$$' };
};

const readBareDisplayMath = (source) => {
  // Some model responses omit the backslashes around a display equation.
  // Accept only standalone bracket lines containing a recognizable TeX command,
  // leaving arrays, ordinary brackets and Markdown reference links alone.
  const match = /^ {0,3}\[[ \t]*\n([\s\S]*?)\n[ \t]*\][ \t]*(?:\n|$)/.exec(source);
  if (!match || !/\\(?:text|[dt]?frac|sqrt|sum|prod|int|begin|left|operatorname)\b/.test(match[1])) return undefined;
  return { raw: match[0], text: match[1], display: true };
};

const readBlockMath = (source) => readMath(source, true) || readBareDisplayMath(source);

export const createMarkdownRenderer = ({ marked, katex, sanitize, escapeHtml }) => {
  if (!marked?.Marked) return (source) => escapeHtml(source).replaceAll('\n', '<br>');

  let mathFragments = [];
  let mathMarker = '';
  const mathPlaceholder = (token) => {
    const index = mathFragments.push(token) - 1;
    const placeholder = `<code>${mathMarker}${index}</code>`;
    return token.type === 'chatBlockMath' ? `<p>${placeholder}</p>\n` : placeholder;
  };
  const parser = new marked.Marked({ gfm: true, breaks: true });
  parser.use(markedCjkFriendly());
  parser.use({
    extensions: [
      {
        name: 'chatBlockMath',
        level: 'block',
        start(source) {
          for (const match of source.matchAll(/^ {0,3}(?:\\\[|\$\$|\[[ \t]*\n)/gm)) {
            if (readBlockMath(source.slice(match.index))) return match.index;
          }
          return undefined;
        },
        tokenizer(source) {
          const token = readBlockMath(source);
          if (token) return { type: 'chatBlockMath', ...token };
          return undefined;
        },
        renderer: mathPlaceholder,
      },
      {
        name: 'chatInlineMath',
        level: 'inline',
        start(source) {
          for (const match of source.matchAll(/\\[([]|\$+/g)) {
            if (!isEscaped(source, match.index) && readMath(source.slice(match.index))) return match.index;
          }
          return undefined;
        },
        tokenizer(source) {
          if (this.lexer.state.inRawBlock) return undefined;
          const token = readMath(source);
          if (token) return { type: 'chatInlineMath', ...token };
          return undefined;
        },
        renderer: mathPlaceholder,
      },
    ],
  });

  return (text) => {
    const source = String(text ?? '').replace(/\r\n?/g, '\n');
    mathFragments = [];
    mathMarker = `potato-math-${Array.from(crypto.getRandomValues(new Uint32Array(4)), (n) => n.toString(16)).join('')}-`;
    const html = sanitize(parser.parse(source));
    // Keep the message sanitizer strict. Only KaTeX's generated output is added
    // afterwards, using an unpredictable per-render marker for parsed tokens.
    return html.replace(new RegExp(`<code>${mathMarker}(\\d+)</code>`, 'g'), (_, index) => {
      const token = mathFragments[Number(index)];
      if (!token) return '';
      try {
        if (!katex?.renderToString) return escapeHtml(token.raw);
        const rendered = katex.renderToString(token.text, {
          displayMode: token.display,
          throwOnError: true,
          strict: 'ignore',
          trust: false,
          maxExpand: 1000,
          maxSize: 20,
        });
        return `<span class="markdown-math${token.display ? ' markdown-math-display' : ''}">${rendered}</span>`;
      } catch {
        // Invalid or unfinished model output must not break the whole message.
        return escapeHtml(token.raw);
      }
    });
  };
};
