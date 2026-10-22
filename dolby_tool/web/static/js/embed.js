/** Height reporting and host pause/resume for the browser-only iframe. */
import { PARENT_ORIGIN } from './mode.js';
import { pauseFromHost, resumeFromHost } from './views/inspect.js';

export function initEmbed() {
  if (!PARENT_ORIGIN) return;
  const post = (message) => window.parent.postMessage({ source: 'dolby-inspector', v: 1, ...message }, PARENT_ORIGIN);
  let frame = 0;
  const reportHeight = () => {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      post({ type: 'height', height: Math.ceil(document.documentElement.scrollHeight) });
    });
  };
  const observer = new ResizeObserver(reportHeight);
  observer.observe(document.documentElement);
  const onMessage = (event) => {
    const data = event.data;
    if (event.origin !== PARENT_ORIGIN || event.source !== window.parent ||
        !data || data.source !== 'psporidis-portfolio' || data.v !== 1) return;
    if (data.type === 'pause') pauseFromHost();
    if (data.type === 'resume') resumeFromHost();
  };
  window.addEventListener('message', onMessage);
  window.addEventListener('pagehide', (event) => {
    if (event.persisted) return;
    observer.disconnect();
    cancelAnimationFrame(frame);
    window.removeEventListener('message', onMessage);
  }, { once: true });
  post({ type: 'ready' });
  reportHeight();
}
