/** Collapsible drawers whose content is only built when first opened (raw JSON, table views). */

import { el } from '../dom.js';
import { icon } from './icon.js';

// Highlighting a huge capture would mean tens of thousands of spans; past this it stays plain text.
const HIGHLIGHT_LIMIT = 250_000;
const JSON_TOKEN = /("(?:\\.|[^"\\])*")(\s*:)?|\b(?:true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g;

function highlight(text) {
  if (text.length > HIGHLIGHT_LIMIT) return text;
  const fragment = document.createDocumentFragment();
  let last = 0;
  for (const match of text.matchAll(JSON_TOKEN)) {
    const [token, string, colon] = match;
    fragment.append(text.slice(last, match.index));
    if (string) {
      fragment.append(el('span', { class: colon ? 'json-key' : 'json-string' }, string));
      if (colon) fragment.append(colon);
    } else {
      fragment.append(el('span', { class: /\d/.test(token) ? 'json-number' : 'json-literal' }, token));
    }
    last = match.index + token.length;
  }
  fragment.append(text.slice(last));
  return fragment;
}

/** A <details> drawer; `content()` runs once, on first open. */
export function drawer({ iconName, label, meta, content }) {
  const body = el('div', { class: 'drawer-body' });
  const node = el(
    'details',
    { class: 'drawer' },
    el(
      'summary',
      {},
      icon(iconName, { size: 16 }),
      el('span', { class: 'drawer-label' }, label),
      meta && el('span', { class: 'drawer-meta' }, meta),
      icon('chevronDown', { size: 16, className: 'drawer-chevron' }),
    ),
    body,
  );
  node.addEventListener('toggle', () => {
    if (node.open && !body.hasChildNodes()) body.append(content());
  });
  return node;
}

/** Raw JSON drawer. `data` is a value or a function returning it, serialised on first open. */
export function jsonDrawer(data, { label = 'Raw JSON', meta } = {}) {
  return drawer({
    iconName: 'code',
    label,
    meta,
    content: () =>
      el(
        'pre',
        { class: 'json', tabindex: 0 },
        highlight(JSON.stringify(typeof data === 'function' ? data() : data, null, 2)),
      ),
  });
}
