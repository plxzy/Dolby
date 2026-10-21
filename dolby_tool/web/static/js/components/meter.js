/** Horizontal meter: one value against a fixed scale, with tick labels underneath. */

import { el } from '../dom.js';
import { num } from '../format.js';

export function meter({ label, value, max, unit, ticks, caption }) {
  const known = value != null && value > 0;
  const text = known ? `${num(value)} ${unit}` : 'Unknown';
  const share = known ? Math.min(value / max, 1) : 0;
  return el(
    'div',
    { class: 'meter' },
    el(
      'div',
      { class: 'meter-head' },
      el('span', { class: 'meter-label' }, label),
      el('span', { class: 'meter-value' }, text, value > max && el('span', { class: 'meter-over' }, ' · off scale')),
    ),
    el(
      'div',
      {
        class: 'meter-track',
        role: 'meter',
        'aria-label': label,
        'aria-valuemin': 0,
        'aria-valuemax': max,
        'aria-valuenow': known ? Math.min(value, max) : 0,
        'aria-valuetext': text,
      },
      known && el('span', { class: 'meter-fill', style: { '--share': `${share * 100}%` } }),
    ),
    el(
      'div',
      { class: 'meter-scale', 'aria-hidden': 'true' },
      ticks.map((tick) => el('span', { style: { '--at': `${(tick / max) * 100}%` } }, num(tick))),
    ),
    caption && el('p', { class: 'meter-caption' }, caption),
  );
}
