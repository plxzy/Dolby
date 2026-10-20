/**
 * Entry point — the only module with top-level effects. Applies the theme,
 * starts the router, loads capabilities once and hands them to each view.
 */

import { getJson } from './api.js';
import { notice } from './components/feedback.js';
import { hydrateIcons } from './components/icon.js';
import { initRouter } from './router.js';
import { initTheme } from './theme.js';
import * as capture from './views/capture.js';
import * as compare from './views/compare.js';
import * as inspect from './views/inspect.js';

const DISMISS_KEY = 'dolby-tool.capabilities-dismissed';

/** What each optional tool powers, in the user's terms. */
const TOOL_ROLES = {
  ffprobe: 'inspecting files (ffprobe)',
  mediainfo: 'MediaInfo’s Atmos check',
  osascript: 'the native file picker',
  log: 'live TV.app capture',
  mdfind: 'Spotlight lookup for dropped files',
};
const listFormat = new Intl.ListFormat('en', { type: 'conjunction' });

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
  const roles = listFormat.format(missing.map((tool) => TOOL_ROLES[tool] ?? tool));
  document.getElementById('notices').append(
    notice({
      // Without ffprobe nothing can be inspected, so only that case is worth raising a flag for.
      tone: missing.includes('ffprobe') ? 'warn' : 'quiet',
      body: `Running on ${platformName(caps.platform)} — not available here: ${roles}.`,
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
initRouter({ fallback: 'inspect', appName: 'dolby·tool' });
guardStrayDrops();

const capabilities = getJson('/api/capabilities').catch(() => null);
for (const view of [inspect, compare, capture]) view.init({ capabilities });
capabilities.then(showCapabilityNotice);
