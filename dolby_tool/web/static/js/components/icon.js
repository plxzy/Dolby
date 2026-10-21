/** Render an icon from the inline set. Decorative unless a `label` is given. */

import { trustedSvg } from '../dom.js';
import { ICONS } from '../icons.js';

/** Status glyph per semantic tone, so a state never relies on colour alone. */
export const TONE_GLYPHS = {
  success: 'checkCircle',
  warn: 'alert',
  danger: 'xCircle',
  info: 'info',
  quiet: 'info',
  neutral: 'minusCircle',
  unknown: 'help',
};

export function icon(name, { size = 16, label, className } = {}) {
  const svg = trustedSvg(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" ` +
      `stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] ?? ''}</svg>`,
  );
  svg.setAttribute('width', size);
  svg.setAttribute('height', size);
  if (className) svg.setAttribute('class', className);
  if (label) {
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', label);
  } else {
    svg.setAttribute('aria-hidden', 'true');
  }
  return svg;
}

/** Replace `<span data-icon="name">` placeholders in static markup with icons. */
export function hydrateIcons(root = document) {
  for (const slot of root.querySelectorAll('[data-icon]')) {
    slot.replaceChildren(icon(slot.dataset.icon, { size: Number(slot.dataset.iconSize) || 18 }));
    slot.removeAttribute('data-icon');
  }
}
