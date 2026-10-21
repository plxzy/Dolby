/**
 * Theme toggle: "system" follows prefers-color-scheme, "light"/"dark" pin it.
 * The choice is a per-browser convenience, so storage failures are ignored.
 * index.html applies the stored theme before first paint with the same key.
 */

const STORAGE_KEY = 'dolby-tool.theme';
const MODES = new Set(['system', 'light', 'dark']);

function storedMode() {
  try {
    const mode = localStorage.getItem(STORAGE_KEY);
    return MODES.has(mode) ? mode : 'system';
  } catch {
    return 'system';
  }
}

function storeMode(mode) {
  try {
    if (mode === 'system') localStorage.removeItem(STORAGE_KEY);
    else localStorage.setItem(STORAGE_KEY, mode);
  } catch {
    // Private mode or blocked storage: the theme still applies for this page.
  }
}

function applyMode(mode) {
  if (mode === 'system') delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = mode;
}

/** Wire the sidebar's theme radio group (inputs named "theme"). */
export function initTheme(form) {
  const mode = storedMode();
  applyMode(mode);
  const current = form.elements.namedItem('theme');
  current.value = mode;

  form.addEventListener('change', (event) => {
    const next = event.target.value;
    if (!MODES.has(next)) return;
    storeMode(next);
    const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (document.startViewTransition && !reduceMotion) {
      const transition = document.startViewTransition(() => applyMode(next));
      // Resizing or a second theme change can skip the animation; the theme still applies.
      transition.ready.catch(() => {});
    } else applyMode(next);
  });
}
