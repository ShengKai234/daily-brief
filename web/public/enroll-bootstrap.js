/* global window, URLSearchParams */
(function () {
  var hash = window.location.hash;
  if (!hash || hash === '#') {
    return;
  }
  var raw = hash.charAt(0) === '#' ? hash.slice(1) : hash;
  var params = new URLSearchParams(raw);
  var token = params.get('t') || params.get('token');
  if (token && token.trim()) {
    window.__DAILY_BRIEF_ENROLL_TOKEN__ = token.trim();
    window.history.replaceState(
      window.history.state,
      '',
      window.location.pathname + window.location.search,
    );
  }
})();
