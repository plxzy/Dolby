/**
 * Stacked horizontal score bar. The track spans the effective maximum (the sum
 * of the weights), each segment is one component's contribution in its fixed
 * series colour, segments are separated by a 2px surface gap and only the data
 * end is rounded. Identity is carried by the legend; values by the tooltip and
 * the score table twin.
 */

import { el } from '../dom.js';
import { num } from '../format.js';
import { attachTooltip } from './tooltip.js';

/** parts: [{ key, label, color, value, weight }] in fixed component order. */
export function scoreBar({ parts, total, max, label }) {
  const filled = parts.filter((part) => part.value > 0);
  const segments = filled.map((part, index) =>
    el('span', {
      class: ['scorebar-seg', index === filled.length - 1 && 'is-end'],
      dataset: { key: part.key },
      style: { '--seg': part.color, '--share': `${(part.value / max) * 100}%` },
    }),
  );
  const spoken = parts.map((part) => `${part.label} ${num(part.value, 1)} of ${num(part.weight)}`).join(', ');
  const bar = el(
    'div',
    {
      class: 'scorebar',
      tabindex: 0,
      role: 'img',
      'aria-label': `${label}: ${num(total, 1)} of ${num(max)}. ${spoken}.`,
    },
    segments,
    total < max && el('span', { class: 'scorebar-rest' }),
  );
  // The hit area is taller than the 14px mark (see .scorebar::after), so the
  // hovered segment is found by x position rather than by event target.
  let active = null;
  bar.addEventListener('pointermove', (event) => {
    const hit = segments.find((segment) => {
      const box = segment.getBoundingClientRect();
      return event.clientX >= box.left && event.clientX <= box.right;
    });
    if (hit === active) return;
    active?.removeAttribute('data-active');
    hit?.setAttribute('data-active', '');
    active = hit ?? null;
  });
  bar.addEventListener('pointerleave', () => {
    active?.removeAttribute('data-active');
    active = null;
  });
  attachTooltip(bar, () => breakdown(parts, active?.dataset.key, total, max));
  return bar;
}

function breakdown(parts, activeKey, total, max) {
  return [
    el('div', { class: 'tooltip-title' }, el('strong', {}, num(total, 1)), ` of ${num(max)} points`),
    parts.map((part) =>
      el(
        'div',
        { class: 'tooltip-row', dataset: { active: part.key === activeKey ? 'true' : 'false' } },
        el('span', { class: 'tooltip-key', style: { '--seg': part.color } }),
        el('span', { class: 'tooltip-name' }, part.label),
        el('span', { class: 'tooltip-value' }, `${num(part.value, 1)} / ${num(part.weight)}`),
      ),
    ),
  ];
}

/** The legend for every score bar on the page (always shown: there are seven series). */
export function scoreLegend(parts) {
  return el(
    'ul',
    { class: 'legend', 'aria-label': 'Score components' },
    parts.map((part) =>
      el(
        'li',
        { class: 'legend-item' },
        el('span', { class: 'legend-swatch', style: { '--seg': part.color } }),
        part.label,
      ),
    ),
  );
}
