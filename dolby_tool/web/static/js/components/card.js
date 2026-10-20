/** Dashboard card shell plus the two body layouts cards use: key/value grids and checklists. */

import { el, uid } from '../dom.js';
import { DASH } from '../format.js';
import { icon } from './icon.js';

/** A titled card section. `body` is any child (or list of children). */
export function card({ title, iconName, meta, actions, className, body }) {
  const titleId = uid('card');
  return el(
    'section',
    { class: ['card', className], 'aria-labelledby': titleId },
    el(
      'header',
      { class: 'card-header' },
      el('h3', { class: 'card-title', id: titleId }, iconName && icon(iconName, { size: 16 }), title),
      meta != null && el('span', { class: 'card-meta' }, meta),
      actions,
    ),
    el('div', { class: 'card-body' }, body),
  );
}

/**
 * Key/value grid. Items: [label, value, { mono, hint, wide }] where value may be
 * a node; empty values render as a dash, and `hint` adds a quiet suffix.
 */
export function kvList(items) {
  return el(
    'dl',
    { class: 'kv' },
    items.map(([label, value, { mono = false, hint, wide = false } = {}]) =>
      el(
        'div',
        { class: ['kv-item', wide && 'kv-wide'] },
        el('dt', {}, label),
        el(
          'dd',
          { class: mono ? 'mono' : null },
          value === '' || value == null ? DASH : value,
          hint && el('span', { class: 'kv-hint' }, hint),
        ),
      ),
    ),
  );
}

/** Presence checklist. Items: [label, present (true/false/null), detail]. */
export function checklist(items) {
  return el(
    'ul',
    { class: 'checklist' },
    items.map(([label, present, detail]) => {
      const state = present === true ? 'yes' : present === false ? 'no' : 'unknown';
      const glyph = { yes: 'checkCircle', no: 'minusCircle', unknown: 'help' }[state];
      const spoken = { yes: 'present', no: 'absent', unknown: 'unknown' }[state];
      return el(
        'li',
        { dataset: { state } },
        icon(glyph, { size: 18 }),
        el('span', { class: 'checklist-label' }, label),
        el('span', { class: 'checklist-state' }, detail ?? spoken),
      );
    }),
  );
}
