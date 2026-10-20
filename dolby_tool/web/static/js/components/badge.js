/** Status chips and the large verdict tiles used in hero strips. */

import { el } from '../dom.js';
import { icon, TONE_GLYPHS } from './icon.js';

const SPOKEN_TONE = {
  success: 'Good',
  warn: 'Warning',
  danger: 'Problem',
  info: 'Note',
  neutral: 'Not present',
  unknown: 'Unknown',
};

/** Small chip. Tones: neutral | accent | success | warn | danger | info. */
export function badge(text, { tone = 'neutral', iconName, title } = {}) {
  return el('span', { class: 'badge', dataset: { tone }, title }, iconName && icon(iconName, { size: 12 }), text);
}

/**
 * A verdict tile (an <li> for a `.verdicts` list): category, headline and one
 * detail line. Tone `plain` is informational and carries no status glyph.
 */
export function verdict({ label, iconName, value, detail, tone = 'plain' }) {
  return el(
    'li',
    { class: 'verdict', dataset: { tone } },
    el('div', { class: 'verdict-label' }, icon(iconName, { size: 14 }), label),
    el('div', { class: 'verdict-value' }, value),
    detail && el('div', { class: 'verdict-detail' }, detail),
    tone in TONE_GLYPHS &&
      el('span', { class: 'verdict-status' }, icon(TONE_GLYPHS[tone], { size: 18, label: SPOKEN_TONE[tone] })),
  );
}
