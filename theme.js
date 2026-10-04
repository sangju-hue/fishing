// Apply the saved theme before styles load to avoid a flash of the wrong mode.
(() => {
  const media = window.matchMedia('(prefers-color-scheme: dark)');
  let choice = 'system';
  try { choice = localStorage.getItem('bada-theme') || 'system'; } catch (_) {}
  if (!['dark', 'white', 'system'].includes(choice)) choice = 'system';
  function apply(value) {
    choice = value;
    const mode = value === 'system' ? (media.matches ? 'dark' : 'white') : value;
    document.documentElement.dataset.theme = mode;
    document.documentElement.dataset.themeChoice = choice;
    document.documentElement.style.colorScheme = mode === 'dark' ? 'dark' : 'light';
    document.querySelectorAll('[data-theme]').forEach(button => {
      if (button.tagName === 'BUTTON') button.setAttribute('aria-pressed', String(button.dataset.theme === choice));
    });
  }
  window.badaTheme = { set(value) {
    if (!['dark', 'white', 'system'].includes(value)) return;
    try { localStorage.setItem('bada-theme', value); } catch (_) {}
    apply(value);
  }, refresh() { apply(choice); } };
  media.addEventListener('change', () => { if (choice === 'system') apply(choice); });
  apply(choice);
})();
