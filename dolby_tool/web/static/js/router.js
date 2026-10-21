/**
 * Hash router for the three views (#/inspect, #/compare, #/capture). Views
 * keep their state while hidden; switching uses a View Transition where the
 * browser supports it and the user hasn't asked for reduced motion.
 */

export function initRouter({ fallback, appName }) {
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const views = new Map(Array.from(document.querySelectorAll('[data-view]'), (view) => [view.dataset.view, view]));
  const links = document.querySelectorAll('[data-nav]');

  const routeName = () => {
    const name = location.hash.replace(/^#\//, '');
    return views.has(name) ? name : fallback;
  };
  let current = null;

  const render = () => {
    const name = routeName();
    current = name;
    for (const [key, view] of views) view.hidden = key !== name;
    for (const link of links) {
      if (link.dataset.nav === name) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    }
    document.title = `${views.get(name).querySelector('h1').textContent} · ${appName}`;
  };

  window.addEventListener('hashchange', () => {
    // In-page anchors such as the skip link (#main) are not routes.
    if (!location.hash.startsWith('#/') || routeName() === current) return;
    window.scrollTo({ top: 0 });
    if (document.startViewTransition && !reducedMotion.matches) document.startViewTransition(render);
    else render();
  });
  render();
}
