/**
 * DOM helpers. `el()` turns every string child into a text node, so data from
 * the server is never parsed as HTML; `trustedSvg()` is the one place markup is
 * parsed, and it only ever receives the constant icon strings from icons.js.
 */

/** Shorthand for querySelector. */
export const $ = (selector, root = document) => root.querySelector(selector);

/**
 * Build an element.
 *
 *   el('button', { class: 'btn', type: 'button', onclick: run }, 'Inspect')
 *
 * Attributes: `class` (string or array), `dataset`, `style` (object; keys are
 * CSS property names, custom properties included), `on<event>` listeners, and
 * plain attributes (`true` sets an empty attribute; `null`/`false` skip it).
 * Children: nodes, strings/numbers (as text), nested arrays; nullish and
 * booleans are skipped so `cond && node` works inline.
 */
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key === 'class') {
      node.className = Array.isArray(value) ? value.filter(Boolean).join(' ') : value;
    } else if (key === 'dataset') {
      Object.assign(node.dataset, value);
    } else if (key === 'style') {
      for (const [prop, v] of Object.entries(value)) node.style.setProperty(prop, v);
    } else if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2), value);
    } else {
      node.setAttribute(key, value === true ? '' : String(value));
    }
  }
  return append(node, children);
}

/** Append children with the same rules as `el()`. Returns the parent. */
export function append(parent, children) {
  for (const child of [children].flat(Infinity)) {
    if (child == null || typeof child === 'boolean') continue;
    parent.append(child instanceof Node ? child : String(child));
  }
  return parent;
}

/** Replace all children of `parent`. */
export function replace(parent, ...children) {
  parent.replaceChildren();
  return append(parent, children);
}

let idCounter = 0;

/** A document-unique id for aria-labelledby / aria-controls wiring. */
export function uid(prefix = 'id') {
  idCounter += 1;
  return `${prefix}-${idCounter}`;
}

/** Parse constant, trusted SVG markup into an element. Never pass data here. */
export function trustedSvg(markup) {
  const template = document.createElement('template');
  template.innerHTML = markup;
  return template.content.firstElementChild;
}
