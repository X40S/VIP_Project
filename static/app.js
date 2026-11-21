"use strict";
/* Ядро: хелперы, справочники, модальное окно, роутинг. Страницы регистрируются в window.pages. */
const $ = (s, r = document) => r.querySelector(s);
const app = $("#app");
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
window.pages = {};

const PRIORITY = { critical: ["Критичный", "b-red"], high: ["Высокий", "b-orange"], medium: ["Средний", "b-amber"], low: ["Низкий", "b-green"] };
const SEVERITY = { critical: ["Critical", "b-red"], high: ["High", "b-orange"], medium: ["Medium", "b-amber"], low: ["Low", "b-gray"] };
const CATEGORY = { rg_safety: ["RG / Safety", "b-red"], operational: ["Операционное", "b-amber"], communication: ["Коммуникация", "b-purple"] };
const STATE = { neutral: "нейтрально", irritation: "раздражение", dissatisfaction: "недовольство", disappointment: "разочарование",
  detachment: "отстранённость", positive: "позитив", formal_agreement: "формальное согласие", resolved: "решено" };
const FLAGS = { SELF_EXCLUDED: "Самоисключение", LIMIT_SET: "Лимит", PAYMENT_FAILURES: "Сбои платежей", DEPOSIT_DROP: "Падение депозитов",
  DEPOSIT_FREQUENCY_SPIKE: "Рост частоты депозитов", PENDING_WITHDRAWAL: "Ожидает вывод", INACTIVE: "Неактивен", BONUS_HEAVY: "Много бонусов" };
const RG_SIGNAL = { self_exclusion_request: "Запрос самоисключения", loss_of_control: "Потеря контроля", serious_harm: "Серьёзный вред",
  self_harm_risk: "Риск самоповреждения", ambiguous_distress: "Тревожные признаки" };
const HANDLED = { yes: "обработан", partly: "частично", no: "без реакции", not_applicable: "" };
const KIND = { protective: "Защита", service: "Сервис", commercial: "Коммерция", coaching: "Разбор" };
const QUALITY = { tone: "Тон", empathy: "Эмпатия", engagement: "Вовлечённость", personalization: "Персонализация",
  problem_understanding: "Понимание проблемы", completeness: "Полнота ответа", follow_up: "Follow-up", next_steps_clarity: "Ясность шагов" };
const SOURCE = { gemini: "Gemini", anthropic: "Anthropic", claude_code: "Claude Code" };
const ERR_KIND = { auth: "Неверный ключ", config: "Не настроено", rate_limit: "Лимит запросов", quota_daily: "Суточный лимит исчерпан",
  billing: "Нет средств", unavailable: "Сервис недоступен", bad_response: "Некорректный ответ", refused: "Отказ модели" };

/* иконки каналов */
const ICONS = {
  telegram: '<svg class="ch" viewBox="0 0 24 24"><circle cx="12" cy="12" r="12" fill="#27A7E7"/><path d="M5.4 11.8l11.9-4.6c.55-.2 1 .13.83.95l-2 9.5c-.15.67-.55.83-1.1.52l-3-2.2-1.45 1.4c-.16.16-.3.3-.6.3l.22-3.05 5.55-5c.24-.22-.05-.33-.37-.13l-6.85 4.3-2.95-.92c-.64-.2-.65-.64.14-.95z" fill="#fff"/></svg>',
  whatsapp: '<svg class="ch" viewBox="0 0 24 24"><circle cx="12" cy="12" r="12" fill="#25D366"/><path d="M12 5.2a6.8 6.8 0 0 0-5.8 10.3L5.3 18.7l3.3-.9A6.8 6.8 0 1 0 12 5.2z" fill="none" stroke="#fff" stroke-width="1.5"/><path d="M9.7 9.2c.2-.4.4-.4.6-.4h.4c.1 0 .3 0 .4.3l.6 1.4c0 .2 0 .3-.1.4l-.4.5c-.1.1-.1.2 0 .4.4.7 1 1.2 1.7 1.6.2.1.3.1.4 0l.5-.6c.1-.2.3-.2.5-.1l1.3.6c.2.1.3.2.3.3 0 .5-.4 1.2-1 1.4-.5.2-1.1.2-2-.2-1.5-.6-2.8-1.9-3.4-3.3-.3-.8-.2-1.4.2-1.9z" fill="#fff"/></svg>',
  email: '<svg class="ch" viewBox="0 0 24 24"><circle cx="12" cy="12" r="12" fill="#6f7488"/><path d="M6 8h12v8H6z" fill="none" stroke="#fff" stroke-width="1.4"/><path d="M6.4 8.6l5.6 4.4 5.6-4.4" fill="none" stroke="#fff" stroke-width="1.4"/></svg>',
};
const chIcon = (c) => ICONS[c] || "";

/* форматирование */
const api = async (url, opts) => {
  const o = opts && opts.body !== undefined ? { ...opts, headers: { "Content-Type": "application/json" }, body: JSON.stringify(opts.body) } : opts;
  const r = await fetch(url, o);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = data.detail || {};
    const e = new Error(typeof d === "string" ? d : d.message || "Ошибка");
    e.kind = d.kind; e.status = r.status; throw e;
  }
  return data;
};
const toast = (msg, err) => {
  const t = $("#toast"); t.textContent = msg; t.className = "toast" + (err ? " err" : ""); t.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => (t.hidden = true), 6000);
};
const fmtT = (s) => { const d = new Date(s); return d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" }) + " " + d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }); };
const fmtMin = (m) => (m == null ? "" : m < 60 ? `${Math.round(m)} мин` : m < 1440 ? `${Math.floor(m / 60)} ч ${Math.round(m % 60)} мин` : `${Math.floor(m / 1440)} д ${Math.floor((m % 1440) / 60)} ч`);
const badge = (pair, extra = "") => `<span class="badge ${pair[1]}">${esc(pair[0])}${extra}</span>`;
const money = (v) => (v == null ? "-" : "$" + Number(v).toLocaleString("ru-RU"));
const pct = (v) => (v == null ? "-" : (v > 0 ? "+" : "") + v + "%");
const shortRef = (m) => (m.includes("#") ? "#" + m.split("#")[1] : m);
const heat = (v) => (v == null ? "var(--card2)" : v >= 80 ? "rgba(62,194,143,.28)" : v >= 60 ? "rgba(224,160,48,.25)" : "rgba(239,91,96,.28)");
const fmtDate = (s) => (s ? new Date(s).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "2-digit" }) + " " + new Date(s).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }) : "");

/* модальное окно */
function modal(html) {
  const bg = document.createElement("div");
  bg.className = "modal-bg";
  bg.innerHTML = `<div class="modal">${html}</div>`;
  bg.addEventListener("mousedown", (e) => { if (e.target === bg) bg.remove(); });
  document.body.appendChild(bg);
  return { el: bg.firstChild, close: () => bg.remove() };
}

/* роутинг */
window.onLeave = null;   // страница может задать функцию очистки (таймеры, графики)
let lastPage = null;
function route() {
  const h = location.hash || "#/";
  const page = h.startsWith("#/rules") ? "rules" : h.startsWith("#/settings") ? "settings" : "chats";
  for (const n of ["chats", "rules", "settings"]) $("#nav-" + n).classList.toggle("active", n === page);
  if (page !== lastPage && window.onLeave) { window.onLeave(); window.onLeave = null; }
  lastPage = page;
  const m = h.match(/^#\/dialog\/(\w+)/);
  window.pages[page](m ? m[1] : null);
}
window.addEventListener("hashchange", route);
window.addEventListener("load", route);
