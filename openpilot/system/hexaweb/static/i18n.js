// LizPilot texts in pt, en and es. The pages are written in Portuguese; /i18n/<lang>.json maps each Portuguese text to the
// translated one (the same files the comma screen uses). A text missing from the catalog stays in Portuguese.
// The language is the phone's own, unless the person picked one in the panel.
(function () {
  "use strict";
  const LANGS = ["pt", "en", "es"];
  const LOCALE = { pt: "pt-BR", en: "en-US", es: "es-ES" };
  const KEY = "lpLang";

  function fromCode(code) {
    const c = String(code || "").toLowerCase();
    return c.startsWith("pt") ? "pt" : c.startsWith("es") ? "es" : c.startsWith("en") ? "en" : null;
  }

  function detect() {
    let saved = null;
    try { saved = localStorage.getItem(KEY); } catch (e) {}
    if (LANGS.includes(saved)) return saved;
    for (const code of (navigator.languages || [navigator.language])) {
      const l = fromCode(code);
      if (l) return l;
    }
    return "pt";
  }

  const lang = detect();
  let table = {}, templates = [];
  const escape = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

  function build(t) {
    table = t && typeof t === "object" ? t : {};
    templates = [];
    for (const [src, dst] of Object.entries(table)) {
      if (!/\{\w+\}/.test(src)) continue;
      const names = [];
      const body = src.split(/(\{\w+\})/).map((part) => {
        const m = part.match(/^\{(\w+)\}$/);
        if (m) { names.push(m[1]); return "([\\s\\S]+?)"; }
        return escape(part);
      }).join("");
      templates.push([new RegExp("^" + body + "$"), names, dst]);
    }
  }

  function t(s) {
    if (!s || lang === "pt") return s;
    if (Object.prototype.hasOwnProperty.call(table, s)) return table[s];
    for (const [re, names, dst] of templates) {
      const m = s.match(re);
      if (m) return dst.replace(/\{(\w+)\}/g, (all, n) => { const i = names.indexOf(n); return i >= 0 ? m[i + 1] : all; });
    }
    return s;
  }

  function tf(s, vars) {
    return t(s).replace(/\{(\w+)\}/g, (all, n) => (vars && Object.prototype.hasOwnProperty.call(vars, n) ? String(vars[n]) : all));
  }

  // <x data-t>texto</x> · data-t-ph="..." for placeholders · data-t-aria="..." for aria-label
  function apply(root) {
    (root || document).querySelectorAll("[data-t]").forEach((el) => {
      if (!el.dataset.t) el.dataset.t = el.textContent.trim();
      el.textContent = t(el.dataset.t);
    });
    (root || document).querySelectorAll("[data-t-ph]").forEach((el) => { el.placeholder = t(el.dataset.tPh); });
    (root || document).querySelectorAll("[data-t-aria]").forEach((el) => { el.setAttribute("aria-label", t(el.dataset.tAria)); });
  }

  function setLang(l) {
    if (!LANGS.includes(l) || l === lang) return;
    try { localStorage.setItem(KEY, l); } catch (e) {}
    location.reload();
  }

  document.documentElement.lang = LOCALE[lang];
  const ready = lang === "pt" ? Promise.resolve() : fetch(`/i18n/${lang}.json`).then((r) => (r.ok ? r.json() : {})).then(build).catch(() => build({}));

  window.LP = { lang, LANGS, locale: LOCALE[lang], t, tf, apply, ready, setLang };
})();
