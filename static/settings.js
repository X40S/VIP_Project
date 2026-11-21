"use strict";
/* Страница «Настройки»: одна панель, провайдер, ключ и модель друг под другом. */
(() => {
  let pub = null, cur = null;
  const loaded = {};   // provider -> ответ /api/settings/models
  const drafts = {};   // provider -> { key, model } несохранённые правки при переключении провайдера

  window.pages.settings = async () => {
    app.innerHTML = '<div class="muted">Загрузка...</div>';
    try { pub = await api("/api/settings"); } catch (e) { app.innerHTML = `<div class="status-msg err">${esc(e.message)}</div>`; return; }
    cur = pub.provider;
    render();
    loadModels(cur, false);
  };

  const msg = (ok, text) => { const el = $("#msg"); if (el) el.innerHTML = text ? `<div class="status-msg ${ok ? "ok" : "err"}">${esc(text)}</div>` : ""; };
  const priceTxt = (usd) => (usd == null ? "" : usd < 0.01 ? " · < $0.01 за диалог" : ` · ≈ $${usd.toFixed(2)} за диалог`);

  function render() {
    const p = pub.providers[cur];
    app.innerHTML = `<div class="topbar"><h1>Настройки</h1></div>
      <div class="card form" style="max-width:560px">
        <label for="prov" style="margin-top:0">Провайдер</label>
        <select id="prov">${Object.entries(pub.providers).map(([k, v]) => `<option value="${k}" ${k === cur ? "selected" : ""}>${esc(v.label)}</option>`).join("")}</select>
        <label for="key">API-ключ</label>
        <input type="password" id="key" autocomplete="off" value="${esc(drafts[cur]?.key ?? (p.has_key ? p.api_key_masked : ""))}">
        <label for="model">Модель</label>
        <div class="row"><select id="model" class="grow" style="width:auto"><option value="${esc(drafts[cur]?.model || p.model)}">${esc(drafts[cur]?.model || p.model)}</option></select>
          <button class="btn small" id="refresh">Обновить</button></div>
        <div class="note" id="mnote"></div>
        <label for="conc">Параллельных запросов</label>
        <input type="number" id="conc" min="1" max="6" value="${pub.concurrency}" style="max-width:120px">
        <div class="row" style="margin-top:16px"><button class="btn" id="test">Проверить</button><button class="btn primary" id="save">Сохранить</button><button class="btn primary" id="use">Использовать для анализа</button></div>
        <div id="msg"></div></div>`;
    $("#prov").addEventListener("change", (e) => switchTo(e.target.value));
    $("#save").addEventListener("click", save);
    $("#use").addEventListener("click", use);
    $("#test").addEventListener("click", test);
    $("#refresh").addEventListener("click", refresh);
    $("#model").addEventListener("change", paintUse);
    $("#key").addEventListener("input", paintUse);
    paintUse();
  }

  // кнопка неактивна, если выбранные провайдер и модель уже рабочие для анализа (и ключ не менялся)
  function paintUse() {
    const b = $("#use"); if (!b) return;
    const p = pub.providers[cur];
    const same = pub.provider === cur && p.model === $("#model").value && p.has_key && $("#key").value === p.api_key_masked;
    b.disabled = same;
    b.textContent = same ? "Эта модель выбрана для анализа" : "Использовать для анализа";
  }

  function switchTo(name) {
    drafts[cur] = { key: $("#key").value, model: $("#model").value };
    cur = name; render();
    if (loaded[cur]) fillModels(loaded[cur]); else loadModels(cur, false);
  }

  const body = () => ({ provider: cur, model: $("#model").value, api_key: $("#key").value, concurrency: Number($("#conc").value) });

  async function save() {
    try {
      pub = await api("/api/settings", { method: "POST", body: body() });
      delete drafts[cur];
      $("#key").value = pub.providers[cur].api_key_masked;
      msg(true, "Сохранено"); paintUse();
    } catch (e) { msg(false, e.message); }
  }

  // сохраняет выбранные настройки и делает этого провайдера и модель рабочими для анализа
  async function use() {
    try {
      pub = await api("/api/settings", { method: "POST", body: { ...body(), make_active: true } });
      delete drafts[cur];
      $("#key").value = pub.providers[cur].api_key_masked;
      msg(true, `Для анализа: ${pub.providers[cur].label} · ${pub.providers[cur].model}`); paintUse();
    } catch (e) { msg(false, e.message); }
  }

  async function test() {
    const b = $("#test"); b.disabled = true; msg(true, "Проверяю...");
    try {
      const r = await api("/api/settings/test", { method: "POST", body: body() });
      msg(r.ok, r.ok ? r.message : `${ERR_KIND[r.kind] || "Ошибка"}: ${r.message}`);
    } catch (e) { msg(false, e.message); }
    b.disabled = false;
  }

  async function refresh() {
    try { pub = await api("/api/settings", { method: "POST", body: body() }); delete drafts[cur]; }
    catch (e) { return msg(false, e.message); }
    $("#key").value = pub.providers[cur].api_key_masked;
    loadModels(cur, true);
  }

  async function loadModels(name, announce) {
    try {
      const r = await api(`/api/settings/models?provider=${name}`);
      loaded[name] = r;
      if (name === cur) { fillModels(r); if (announce) msg(true, `Доступно моделей: ${r.models.length}`); }
    } catch (e) {
      if (name === cur) { const n = $("#mnote"); if (n) n.textContent = e.kind === "config" && !pub.providers[name].has_key ? "Введите ключ и нажмите «Обновить»" : `${ERR_KIND[e.kind] || "Ошибка"}: ${e.message}`; }
    }
  }

  function fillModels(r) {
    const sel = $("#model"); if (!sel) return;
    const chosen = drafts[cur]?.model || sel.value || r.current;
    const list = [...r.models];
    let pick = chosen, note = r.static ? "Список без проверки ключа" : "";
    if (!list.includes(chosen)) {
      list.unshift(chosen);
      if (r.recommended && !r.static) { pick = r.recommended; note = `Модель «${chosen}» недоступна, выбрана «${r.recommended}». Нажмите «Сохранить».`; }
      else if (!r.static) note = `Модель «${chosen}» недоступна для этого ключа.`;
    }
    sel.innerHTML = list.map((m) => `<option value="${esc(m)}" ${m === pick ? "selected" : ""}>${esc(m)}${r.models.includes(m) || r.static ? "" : " (недоступна)"}${(r.unconfirmed || []).includes(m) ? " (не подтверждена ключом)" : ""}${priceTxt(r.prices[m])}${m === r.recommended ? " (рекомендуется)" : ""}</option>`).join("");
    const n = $("#mnote"); if (n) n.textContent = note;
    paintUse();
  }
})();
