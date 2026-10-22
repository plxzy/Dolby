/**
 * Entry point — the only module with top-level effects. Applies the theme,
 * starts the router, loads capabilities once and hands them to each view.
 */

import { getJson } from './api.js';
import { environmentNote, notice } from './components/feedback.js';
import { hydrateIcons } from './components/icon.js';
import { el } from './dom.js';
import { initEmbed } from './embed.js';
import { EMBED, MODE } from './mode.js';
import { initRouter } from './router.js';
import { initTheme } from './theme.js';
import * as capture from './views/capture.js';
import * as compare from './views/compare.js';
import * as inspect from './views/inspect.js';
import * as convert from './views/convert.js';

const DISMISS_KEY = 'dolby-tool.capabilities-dismissed';

/** What each optional tool powers, in the user's terms. */
const TOOL_ROLES = {
  ffprobe: 'ffprobe — files can’t be inspected',
  mediainfo: 'MediaInfo Atmos check',
  osascript: 'native file picker',
  log: 'live TV.app capture',
  mdfind: 'Spotlight lookup for dropped files',
};

function platformName(sysname) {
  return sysname === 'Darwin' ? 'macOS' : sysname || 'this system';
}

/** A quiet, dismissible note listing what the missing tools switch off. */
function showCapabilityNotice(caps) {
  const missing = Object.entries(caps?.tools ?? {})
    .filter(([, available]) => !available)
    .map(([tool]) => tool);
  if (!missing.length) return;
  const signature = missing.join(',');
  try {
    if (localStorage.getItem(DISMISS_KEY) === signature) return;
  } catch {
    // Storage unavailable: show the notice; dismissal just won't persist.
  }
  // Without ffprobe nothing can be inspected, so only that case is worth raising a flag for.
  const critical = missing.includes('ffprobe');
  document.getElementById('notices').append(
    environmentNote({
      title: critical ? 'Setup needed' : `Limited on ${platformName(caps.platform)}`,
      items: missing.map((tool) => `No ${TOOL_ROLES[tool] ?? tool}`),
      tone: critical ? 'warn' : 'quiet',
      open: critical,
      onDismiss: () => {
        try {
          localStorage.setItem(DISMISS_KEY, signature);
        } catch {
          // Ignore: the notice stays dismissed for this page.
        }
      },
    }),
  );
}

/** Dropping a file outside a drop zone must not navigate away from the app. */
function guardStrayDrops() {
  for (const type of ['dragover', 'drop']) window.addEventListener(type, (event) => event.preventDefault());
}

hydrateIcons();
initTheme(document.getElementById('theme-form'));
if (EMBED) {
  document.documentElement.dataset.embed = '';
  location.hash = '#/inspect';
  document.getElementById('main').prepend(el('nav', { class: 'embed-switch', 'aria-label': 'Browser tools' },
    el('a', { class: 'btn', href: '#/inspect', dataset: { nav: 'inspect' } }, 'Inspect'),
    el('a', { class: 'btn', href: '#/compare', dataset: { nav: 'compare' } }, 'Compare')));
}
initRouter({ fallback: 'inspect', appName: 'dolby·tool' });
guardStrayDrops();

const capabilities = MODE === 'browser' ? Promise.resolve(null) : getJson('/api/capabilities').catch(() => null);
if (MODE === 'browser') {
  inspect.init({ capabilities });
  compare.init({ capabilities });
  document.querySelector('.sidebar-caption').textContent =
    'Runs in your browser — files are read on this device and never uploaded.';
  for (const [name, title] of [['capture', 'TV Capture'], ['convert', 'Convert / Tag']]) {
    const section = document.querySelector(`[data-view="${name}"]`);
    const header = section.querySelector('.view-header');
    for (const child of [...section.children]) if (child !== header) child.remove();
    header.querySelector('.view-actions')?.remove();
    section.append(
      notice({
        tone: 'info',
        title: 'Runs in the macOS app',
        body: `${title} needs local tools (ffprobe, ffmpeg or the macOS log) that a web page can’t run.`,
      }),
      el('a', { class: 'btn', href: 'https://github.com/plxzy/Dolby#run', target: '_blank', rel: 'noopener' }, 'Get the macOS app'),
    );
  }
  if (EMBED) initEmbed();
} else {
  for (const view of [inspect, compare, capture, convert]) view.init({ capabilities });
  capabilities.then(showCapabilityNotice);
}
