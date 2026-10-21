/** "Copy" menu button: a keyboard-navigable menu of clipboard actions with spoken feedback. */

import { el, uid } from '../dom.js';
import { icon } from './icon.js';

/** Copy text, falling back to a selection copy where the async clipboard is unavailable or refused. */
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Embedded browsers expose navigator.clipboard but reject writes; the
    // selection fallback still works while the click's user activation lasts.
  }
  const area = el('textarea', { readonly: true, class: 'visually-hidden' });
  area.value = text;
  document.body.append(area);
  area.select();
  try {
    return document.execCommand('copy');
  } catch {
    return false;
  } finally {
    area.remove();
  }
}

/** items: [{ label, iconName, text: () => string }] */
export function copyMenu(items, { label = 'Copy' } = {}) {
  const menuId = uid('menu');
  const buttonLabel = el('span', {}, label);
  const status = el('span', { class: 'visually-hidden', role: 'status' });
  const button = el(
    'button',
    {
      class: 'btn btn-sm',
      type: 'button',
      'aria-haspopup': 'menu',
      'aria-expanded': 'false',
      'aria-controls': menuId,
    },
    icon('copy', { size: 15 }),
    buttonLabel,
    icon('chevronDown', { size: 14, className: 'btn-chevron' }),
  );
  const entries = items.map((item) =>
    el(
      'button',
      { class: 'menu-item', type: 'button', role: 'menuitem', tabindex: -1, onclick: () => run(item) },
      icon(item.iconName ?? 'copy', { size: 15 }),
      item.label,
    ),
  );
  const menu = el('div', { class: 'menu', id: menuId, role: 'menu', 'aria-label': label, hidden: true }, entries);
  const wrap = el('div', { class: 'menu-wrap' }, button, menu, status);
  let resetTimer = 0;

  const onOutside = (event) => {
    if (!wrap.contains(event.target)) close();
  };

  function open() {
    menu.hidden = false;
    button.setAttribute('aria-expanded', 'true');
    entries[0].focus();
    document.addEventListener('pointerdown', onOutside);
  }

  function close({ refocus = false } = {}) {
    menu.hidden = true;
    button.setAttribute('aria-expanded', 'false');
    document.removeEventListener('pointerdown', onOutside);
    if (refocus) button.focus();
  }

  async function run(item) {
    close({ refocus: true });
    const ok = await copyText(item.text());
    const message = ok ? 'Copied' : 'Copy failed';
    buttonLabel.textContent = message;
    status.textContent = ok ? `${item.label}: copied to the clipboard` : `${item.label}: copy failed`;
    wrap.dataset.result = ok ? 'ok' : 'error';
    clearTimeout(resetTimer);
    resetTimer = setTimeout(() => {
      buttonLabel.textContent = label;
      delete wrap.dataset.result;
    }, 1600);
  }

  button.addEventListener('click', () => (menu.hidden ? open() : close()));
  button.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown' && menu.hidden) {
      event.preventDefault();
      open();
    }
  });
  menu.addEventListener('keydown', (event) => {
    const index = entries.indexOf(document.activeElement);
    const moves = { ArrowDown: index + 1, ArrowUp: index - 1, Home: 0, End: entries.length - 1 };
    if (event.key in moves) {
      event.preventDefault();
      entries[(moves[event.key] + entries.length) % entries.length].focus();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      close({ refocus: true });
    } else if (event.key === 'Tab') {
      close();
    }
  });
  return wrap;
}
