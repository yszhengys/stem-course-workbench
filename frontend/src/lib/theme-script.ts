// This script runs before React hydration to prevent theme flash.
// Preference order: on-prefs cookie (single source of truth, see
// lib/stores/prefs-cookie.ts) → legacy localStorage (one-time migration
// window) → system.
export const themeScript = `
(function() {
  try {
    var cookieTheme = null;
    try {
      var m = document.cookie.match(/(?:^|; )on-prefs=([^;]*)/);
      if (m) {
        var parsed = JSON.parse(decodeURIComponent(m[1]));
        cookieTheme = parsed && parsed.theme && parsed.theme.theme;
      }
    } catch (e) { /* ignore malformed cookie */ }

    var legacyTheme = null;
    try {
      legacyTheme = JSON.parse(localStorage.getItem('theme-storage') || '{}').state?.theme;
    } catch (e) { /* ignore */ }

    var theme = cookieTheme || legacyTheme || 'system';
    var systemPrefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    var effectiveTheme = theme === 'system' ? (systemPrefersDark ? 'dark' : 'light') : theme;

    document.documentElement.classList.remove('light', 'dark');
    document.documentElement.classList.add(effectiveTheme);
    document.documentElement.setAttribute('data-theme', effectiveTheme);
  } catch (e) {
    // Fallback to light theme
    document.documentElement.classList.add('light');
    document.documentElement.setAttribute('data-theme', 'light');
  }
})();
`
