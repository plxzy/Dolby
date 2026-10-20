/** Data table in a horizontally scrollable, keyboard-focusable wrapper. */

import { el } from '../dom.js';

/**
 * columns: [{ label, value: (row, index) => child, className }]
 * `caption` names the table for assistive tech and the scroll region. With
 * `stack`, narrow screens show each row as a labelled record instead of scrolling.
 */
export function dataTable({ columns, rows, caption, className, stack = false }) {
  return el(
    'div',
    { class: ['table-wrap', stack && 'table-stack'], tabindex: 0, role: 'region', 'aria-label': caption },
    el(
      'table',
      { class: ['table', className] },
      el('caption', { class: 'visually-hidden' }, caption),
      el(
        'thead',
        {},
        el(
          'tr',
          {},
          columns.map((col) => el('th', { scope: 'col', class: col.className }, col.label)),
        ),
      ),
      el(
        'tbody',
        {},
        rows.map((row, index) =>
          el(
            'tr',
            {},
            columns.map((col) => el('td', { class: col.className, dataset: { label: col.label } }, col.value(row, index))),
          ),
        ),
      ),
    ),
  );
}
