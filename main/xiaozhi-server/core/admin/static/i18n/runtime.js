/* Translate application-owned copy only. Conversation text, SSIDs and user input are never converted. */
window.UII18n = (() => {
  const key = 'thinksmart.uiLocale';
  const LOCALES = ['zh-Hans', 'zh-Hant', 'en'];
  // zh-* with Hant/TW/HK/MO → Traditional; other zh → Simplified; everything else (including en-HK) → English
  const normalize = value => !/^zh(?:-|$)/i.test(value || '') ? 'en'
    : /^zh-(?:Hant|TW|HK|MO)(?:-|$)/i.test(value) ? 'zh-Hant' : 'zh-Hans';
  let saved;
  try { saved = localStorage.getItem(key); } catch (_) { /* Private browsing may disable storage. */ }
  const requested = new URLSearchParams(location.search).get('lang');
  // The admin page has no kiosk: remembered value, then ?lang, then the browser (spec §6.2, last paragraph)
  const locale = LOCALES.includes(saved) ? saved
    : LOCALES.includes(requested) ? requested : normalize(navigator.language);
  if (!LOCALES.includes(saved) && requested === locale) {
    try { localStorage.setItem(key, locale); } catch (_) { /* The URL still supplies this visit's locale. */ }
  }
  const catalogs = { 'zh-Hant': window.UI_ZH_HANT, 'zh-Hans': window.UI_ZH_HANS, en: window.UI_EN };
  // Never switch to a locale whose dictionary is missing or empty (e.g. en.js failed to load or parse)
  const available = l => l.startsWith('zh-') || Object.keys(catalogs[l] || {}).length > 0;
  const applied = available(locale) ? locale : 'zh-Hans';
  const catalog = catalogs[applied] || {};
  const t = text => Object.prototype.hasOwnProperty.call(catalog, text) ? catalog[text] : text;
  // {msg_key, msg_params}: translate the template, then fill it. Only object parameters are translated;
  // plain strings are names, keywords or third-party text and are shown exactly as given.
  const param = value => (value && typeof value === 'object' ? text(value) : value === null || value === undefined ? '' : String(value));
  const msg = obj => {
    if (!obj || typeof obj !== 'object' || typeof obj.msg_key !== 'string') return null;
    const params = obj.msg_params || {};
    return t(obj.msg_key).replace(/\{(\w+)\}/g, (whole, name) =>
      Object.prototype.hasOwnProperty.call(params, name) ? param(params[name]) : whole);
  };
  const text = value => {
    if (value === null || value === undefined) return '';
    if (typeof value === 'string') return t(value);
    const m = msg(value);
    return m === null ? String(value) : m;
  };
  function init() {
    document.documentElement.lang = applied;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        return node.parentElement.closest('script,style,textarea,[data-no-i18n]') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
      },
    });
    let node;
    while ((node = walker.nextNode())) {
      const txt = node.textContent.trim();
      if (txt) node.textContent = node.textContent.replace(txt, t(txt));
    }
    document.title = t(document.title);
    document.querySelectorAll('[title],[aria-label],[placeholder],[data-kb-enter]').forEach(el => {
      for (const attr of ['title', 'aria-label', 'placeholder', 'data-kb-enter']) {
        if (el.hasAttribute(attr)) el.setAttribute(attr, t(el.getAttribute(attr)));
      }
    });
    document.querySelectorAll('[data-language-picker]').forEach(select => {
      for (const option of select.options) option.disabled = !available(option.value);
      select.value = applied;
      select.addEventListener('change', () => {
        try { localStorage.setItem(key, select.value); } catch (_) { return; }
        location.reload();
      });
    });
  }
  return { t, msg, text, init, locale, applied, normalize };
})();
