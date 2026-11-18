export function registerPwaServiceWorker(): void {
  if (typeof window === 'undefined' || !('serviceWorker' in navigator)) {
    return;
  }
  if (import.meta.env.MODE === 'test') {
    return;
  }
  window.addEventListener('load', () => {
    void navigator.serviceWorker.register('/sw.js').catch(() => {
      // Service worker registration failure should not block app use
    });
  });
}
