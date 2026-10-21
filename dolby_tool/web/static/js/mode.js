/** Runtime mode and the exact parent origins permitted to control an embed. */
export const MODE = document.querySelector('meta[name="dolby-mode"]')?.content === 'browser' ? 'browser' : 'native';
const params = new URLSearchParams(location.search);
export const EMBED = MODE === 'browser' && params.get('embed') === '1';
const parent = params.get('parent');
export const PARENT_ORIGIN = ['https://psporidis.com', 'https://www.psporidis.com'].includes(parent) ||
  /^http:\/\/(localhost|127\.0\.0\.1|\[::1\]):\d{1,5}$/.test(parent ?? '') ? parent : null;
