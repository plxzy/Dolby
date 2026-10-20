/** Feedback surfaces: notices (inline), toasts (transient), empty states and loading skeletons. */

import { el } from '../dom.js';
import { icon, TONE_GLYPHS } from './icon.js';

/** Inline notice. Tones: info | success | warn | danger. `body` may be text or nodes. */
export function notice({ tone = 'info', title, body, actions, onDismiss, iconName }) {
  const node = el(
    'div',
    { class: 'notice', dataset: { tone }, role: tone === 'danger' ? 'alert' : null },
    el('span', { class: 'notice-icon' }, icon(iconName ?? TONE_GLYPHS[tone], { size: 18 })),
    el(
      'div',
      { class: 'notice-content' },
      title && el('p', { class: 'notice-title' }, title),
      body && el('div', { class: 'notice-body' }, body),
      actions && el('div', { class: 'notice-actions' }, actions),
    ),
    onDismiss &&
      el(
        'button',
        {
          class: 'btn btn-ghost btn-sm btn-icon notice-dismiss',
          type: 'button',
          'aria-label': 'Dismiss',
          onclick: () => {
            node.remove();
            onDismiss();
          },
        },
        icon('close', { size: 15 }),
      ),
  );
  return node;
}

/** Split text on `backticks` into text and <code> nodes — safe rich text for server messages. */
export function withCode(text) {
  return String(text)
    .split('`')
    .map((part, index) => (index % 2 ? el('code', {}, part) : part));
}

/** Transient message in the page's live toast region. */
export function toast(message, { tone = 'info' } = {}) {
  const region = document.getElementById('toasts');
  const node = el(
    'div',
    { class: 'toast', dataset: { tone } },
    icon(TONE_GLYPHS[tone], { size: 16 }),
    el('span', {}, message),
  );
  region.append(node);
  setTimeout(() => {
    node.dataset.leaving = '';
    setTimeout(() => node.remove(), 220);
  }, 4500);
}

export function emptyState({ iconName, title, body, actions }) {
  return el(
    'div',
    { class: 'empty' },
    el('span', { class: 'empty-icon' }, icon(iconName, { size: 22 })),
    el('h3', { class: 'empty-title' }, title),
    body && el('p', { class: 'empty-body' }, body),
    actions && el('div', { class: 'empty-actions' }, actions),
  );
}

/** A shimmering placeholder block. */
export function bone(width = '100%', height = '12px', className) {
  return el('span', {
    class: ['bone', className],
    'aria-hidden': 'true',
    style: { 'inline-size': width, 'block-size': height },
  });
}
