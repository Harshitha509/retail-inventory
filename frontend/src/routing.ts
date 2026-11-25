import { useSyncExternalStore, type MouseEvent } from 'react';

export const ROUTES = {
  overview: '/overview',
  inventory: '/inventory',
  stockout: '/stockout-intelligence',
  replenishment: '/replenishment',
  analytics: '/analytics',
  operations: '/data-operations',
  users: '/users',
  audit: '/audit-logs',
  profile: '/profile',
} as const;

const subscribe = (notify: () => void) => {
  window.addEventListener('popstate', notify);
  return () => window.removeEventListener('popstate', notify);
};
const getLocation = () => `${window.location.pathname}${window.location.search}${window.location.hash}`;

export function useBrowserLocation(): string {
  return useSyncExternalStore(subscribe, getLocation, () => ROUTES.overview);
}

export function navigate(to: string, replace = false): void {
  if (replace) window.history.replaceState({}, '', to);
  else window.history.pushState({}, '', to);
  window.dispatchEvent(new PopStateEvent('popstate'));
}

export function routePath(location: string): string {
  const pathname = new URL(location, window.location.origin).pathname.replace(/\/+$/, '') || '/';
  return pathname === '/' ? ROUTES.overview : pathname;
}

export function routeLink(event: MouseEvent<HTMLAnchorElement>, href: string): void {
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  navigate(href);
}
