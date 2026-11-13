// Runs in <head> before first paint so a saved theme never flashes the wrong colours.
try {
  const t = localStorage.getItem("theme");
  if (t === "dark" || t === "light") document.documentElement.dataset.theme = t;
} catch (_) { /* private mode: follow the system theme */ }
