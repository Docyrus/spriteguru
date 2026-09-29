// A tiny hash router: the studio is served from `/` (and from file paths in a packaged build),
// so hash routes need no server rewrites.

import { useSyncExternalStore } from 'react';

export interface Route {
  screen: string;
  param: string | null;
  query: URLSearchParams;
}

function parse(hash: string): Route {
  const raw = hash.replace(/^#\/?/, '');
  const [pathPart, queryPart = ''] = raw.split('?');
  const segs = (pathPart ?? '').split('/').filter(Boolean).map(decodeURIComponent);
  // the project gallery is home: a bare launch URL (no hash) always lands there
  return { screen: segs[0] ?? 'projects', param: segs[1] ?? null, query: new URLSearchParams(queryPart) };
}

let current = parse(window.location.hash);
const listeners = new Set<() => void>();

function sync() {
  current = parse(window.location.hash);
  listeners.forEach((l) => l());
}

window.addEventListener('hashchange', sync);

function subscribe(l: () => void) {
  listeners.add(l);
  return () => listeners.delete(l);
}

export function useRoute(): Route {
  return useSyncExternalStore(subscribe, () => current);
}

export function href(screen: string, param?: string | null, query?: Record<string, string | null | undefined>): string {
  let h = `#/${screen}`;
  if (param) h += `/${encodeURIComponent(param)}`;
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(query ?? {})) if (v) q.set(k, v);
  const qs = q.toString();
  return qs ? `${h}?${qs}` : h;
}

export function navigate(screen: string, param?: string | null, query?: Record<string, string | null | undefined>) {
  window.location.hash = href(screen, param, query);
}

/** Like `navigate`, but replaces the history entry: for automatic corrections the Back button should skip. */
export function redirect(screen: string, param?: string | null, query?: Record<string, string | null | undefined>) {
  const h = href(screen, param, query);
  if (h === window.location.hash) return;
  window.history.replaceState(window.history.state, '', h);
  sync();
}
