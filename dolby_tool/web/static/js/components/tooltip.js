/**
 * One shared floating tooltip for chart marks. It enhances, never gates: every
 * value it shows is also in a legend, label or table view. Keyboard focus
 * shows the same content as hover.
 */

import { el, replace } from '../dom.js';

let tip = null;

function tooltipNode() {
  if (!tip) {
    tip = el('div', { class: 'tooltip', role: 'tooltip', 'aria-hidden': 'true' });
    document.body.append(tip);
  }
  return tip;
}

function place(node, x, y) {
  const margin = 8;
  const { width, height } = node.getBoundingClientRect();
  const left = Math.min(Math.max(x - width / 2, margin), innerWidth - width - margin);
  const above = y - height - 14;
  node.style.left = `${left}px`;
  node.style.top = `${above < margin ? y + 18 : above}px`;
}

function hide() {
  tip?.removeAttribute('data-open');
}

/** Show `render()` near the pointer, or above `target` when it gains keyboard focus. */
export function attachTooltip(target, render) {
  const show = (x, y) => {
    const node = tooltipNode();
    replace(node, render());
    node.setAttribute('data-open', '');
    place(node, x, y);
  };
  target.addEventListener('pointermove', (event) => show(event.clientX, event.clientY));
  target.addEventListener('pointerleave', hide);
  target.addEventListener('focus', () => {
    const box = target.getBoundingClientRect();
    show(box.left + box.width / 2, box.top);
  });
  target.addEventListener('blur', hide);
  target.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') hide();
  });
}
