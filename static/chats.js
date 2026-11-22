"use strict";
/* Страница «Анализ чатов»: режимы A (AI) и B (импорт), список диалогов, карточка, сводка. */
(() => {
  const ls = {
    get: (k, d) => { try { return localStorage.getItem(k) || d; } catch (e) { return d; } },
    set: (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* без хранилища работаем штатно */ } },
  };
  const st = { items: [], sel: null, detail: null, chart: null, job: null, poll: null, search: "", meta: {}, data: null,
    mode: ls.get("mode", "ai"), up: { dialogs: null, players: null }, upCheck: null, imp: null, impCheck: null,
    settings: null, cost: null, open: ls.get("open", "") === "1", group: ls.get("group", "none") };
  st.sel = ls.get("sel", "") || null;
  const setOpen = (v) => { st.open = v; ls.set("open", v ? "1" : "0"); };
  // пока анализ не открыт (localStorage пуст), показываем пустой Dashboard, серверные результаты не затрагиваются
  const BLANK = { analysis_status: "missing", stale_reasons: [], priority: null, overall: null, violations: null, source: null, rg_signal: null };
  const RANK = { critical: 0, high: 1, medium: 2, low: 3 };
  const byId = (a, b) => a.dialog_id.localeCompare(b.dialog_id);
  const STATUS_L = { fresh: "Актуальный анализ", stale: "Анализ устарел", missing: "Нет анализа" };
  const GROUPS = {
    none: ["Без группировки", null],
    priority: ["По приоритету", (i) => [i.priority ? PRIORITY[i.priority]?.[0] || i.priority : "Не проанализирован", RANK[i.priority] ?? 4]],
    manager: ["По менеджеру", (i) => [i.manager, i.manager]],
    channel: ["По каналу", (i) => [i.channel, i.channel]],
    player: ["По игроку", (i) => [i.player_id, i.player_id]],
    status: ["По статусу анализа", (i) => [STATUS_L[i.analysis_status] || i.analysis_status, i.analysis_status]],
  };
  const sorted = () => [...st.items].sort((a, b) => (RANK[a.priority] ?? 4) - (RANK[b.priority] ?? 4) || byId(a, b));

  window.pages.chats = async (dialogId) => {
    const first = !$("#chats-root");
    if (first) {
      app.innerHTML = `<div id="chats-root"><div class="topbar"><h1>Анализ чатов</h1></div><div class="card" id="panel"></div>
        <div id="tabbody"></div></div>`;
      window.onLeave = () => { clearInterval(st.poll); destroyChart(); };
      await Promise.all([loadList(), loadData(), loadSettings(), loadCost()]);
      renderPanel();
      attachJob();
    }
    if (first || !$("#detail")) renderTab(false);
    if (!dialogId && st.items.length) dialogId = st.sel || sorted()[0].dialog_id;
    if (dialogId) await selectDialog(dialogId);
  };

  /* ---------- данные ---------- */
  async function loadList() {
    try { const r = await api("/api/dialogs"); st.items = st.open ? r.items : r.items.map((i) => ({ ...i, ...BLANK })); st.meta = r; }
    catch (e) { toast(e.message, true); }
  }
  const loadData = async () => { try { st.data = await api("/api/data"); } catch (e) { /* не критично */ } };
  const loadSettings = async () => { try { st.settings = await api("/api/settings"); } catch (e) { /* не критично */ } };
  // ориентировочная стоимость анализа выбранной моделью
  const costTxt = (c) => (!c ? "" : c.cost_usd != null ? (c.cost_usd < 0.01 ? "< $0.01" : `≈ $${c.cost_usd.toFixed(2)}`) : ({ gemini: "тариф Google", openai: "тариф OpenAI" }[c.provider] || ""));
  const costTitle = (c) => (!c ? "" : `${c.model}${c.count != null ? ", диалогов: " + c.count : ""}${c.cost_usd == null && c.note ? ". " + c.note : ""}`);
  async function loadCost() { try { st.cost = await api("/api/cost"); } catch (e) { st.cost = null; } paintRun(); }
  function paintRun() {
    const b = $("#run"); if (!b) return;
    const t = costTxt(st.cost);
    const c = st.cost;
    b.textContent = `Анализировать все${c && c.count > 0 && t ? " · " + t : ""}`;
    b.title = costTitle(st.cost);
    b.disabled = !!(st.data && !st.data.dialogs) || !!(st.job && st.job.status === "running");
  }

  /* ---------- панель режимов ---------- */
  const fileBox = (id, label, accept) => `<div class="file"><label>${label}<input type="file" id="f-${id}" accept="${accept}"></label>
    <span class="st" id="st-${id}"></span></div>`;

  function renderPanel() {
    const p = $("#panel"); if (!p) return;
    const d = st.data;
    const info = d ? (d.dialogs ? `${d.dialogs} диалогов · ${d.players} игроков` : "Данные не загружены") : "";
    const prov = st.settings ? st.settings.providers[st.settings.provider] : null;
    p.innerHTML = `<div class="row" style="justify-content:space-between"><div class="seg"><button data-mode="ai" class="${st.mode === "ai" ? "on" : ""}">AI-анализ</button>
        <button data-mode="import" class="${st.mode === "import" ? "on" : ""}">Импорт анализа</button></div>
        <div class="muted small">${esc(info)}</div></div>
      ${st.mode === "ai" ? `<div class="row" style="margin-top:12px">${fileBox("dialogs", "Выбрать файл диалогов", ".json,application/json")}${fileBox("players", "Выбрать файл игроков", ".csv,text/csv")}
        <button class="btn" id="upload" disabled>Загрузить данные</button></div>
        <div class="row" style="margin-top:12px"><span class="muted small">${prov ? esc(prov.label) + " · " + esc(prov.model) : ""}</span><div class="grow" id="jobbox"></div>
        <button class="btn primary" id="run">Анализировать</button></div>`
      : `<div class="row" style="margin-top:12px">${fileBox("import", "Выбрать файл анализа", ".json,application/json")}
        <label class="row small"><input type="checkbox" id="replace"> Заменить результаты API</label>
        <button class="btn primary" id="doimport" disabled>Импортировать</button></div>`}`;
    p.querySelectorAll("[data-mode]").forEach((b) => b.addEventListener("click", () => { st.mode = b.dataset.mode; ls.set("mode", st.mode); renderPanel(); renderJob(); }));
    if (st.mode === "ai") {
      $("#f-dialogs").addEventListener("change", (e) => pickUpload("dialogs", e.target.files[0]));
      $("#f-players").addEventListener("change", (e) => pickUpload("players", e.target.files[0]));
      $("#upload").addEventListener("click", applyUpload);
      $("#run").addEventListener("click", openRunModal);
      paintUploadStatus(); renderJob(); paintRun();
    } else {
      $("#f-import").addEventListener("change", (e) => pickImport(e.target.files[0]));
      $("#doimport").addEventListener("click", applyImport);
      paintImportStatus();
    }
  }

  const setSt = (id, ok, text) => { const el = $("#st-" + id); if (el) { el.className = "st " + (ok ? "ok" : "err"); el.textContent = text; } };

  /* режим A: загрузка файлов */
  async function pickUpload(key, file) {
    if (!file) return;
    st.up[key] = { name: file.name, text: await file.text() };
    await checkUpload();
  }
  async function checkUpload() {
    const body = { dialogs_text: st.up.dialogs?.text ?? null, players_text: st.up.players?.text ?? null };
    try { st.upCheck = await api("/api/data/check", { method: "POST", body }); } catch (e) { st.upCheck = { ok: false, error: e.message }; }
    paintUploadStatus();
  }
  function paintUploadStatus() {
    const r = st.upCheck, btn = $("#upload"); if (!btn) return;
    for (const k of ["dialogs", "players"]) {
      const f = st.up[k]; if (!f || !r) continue;
      const part = r[k];
      if (part && part.ok) setSt(k, true, `✓ ${f.name}: ${part.count}`);
      else if (part) setSt(k, false, part.error);
      else if (r.error) setSt(k, false, r.error);
    }
    for (const k of r?.missing || []) if (!st.up[k]) setSt(k, false, "нужен файл");
    if (r && r.error && !r.dialogs?.error && !r.players?.error) toast(r.error, true);
    btn.disabled = !(r && r.ok && (st.up.dialogs || st.up.players));
  }
  async function applyUpload() {
    try {
      const r = await api("/api/data/apply", { method: "POST", body: { dialogs_text: st.up.dialogs?.text ?? null, players_text: st.up.players?.text ?? null } });
      if (!r.ok) return toast(r.error || "Файлы не прошли проверку", true);
      toast("Данные загружены");
      st.up = { dialogs: null, players: null }; st.upCheck = null; st.sel = null; st.detail = null;
      await Promise.all([loadList(), loadData(), loadCost()]); renderPanel(); renderTab();
    } catch (e) { toast(e.message, true); }
  }
  /* режим B: импорт */
  async function pickImport(file) {
    if (!file) return;
    st.imp = { name: file.name, text: await file.text() };
    try { st.impCheck = await api("/api/import/check", { method: "POST", body: { text: st.imp.text } }); }
    catch (e) { st.impCheck = { ok: false, error: e.message }; }
    paintImportStatus();
  }
  function paintImportStatus() {
    const r = st.impCheck, btn = $("#doimport"); if (!btn || !r) return;
    if (r.ok) {
      const bits = [`✓ ${st.imp.name}: ${r.valid}/${r.total}`];
      if (r.skipped.length) bits.push(`пропущено ${r.skipped.length}`);
      if (r.rules_changed) bits.push(`правила файла ${r.rules_version}`);
      if (r.existing_api) bits.push(`с результатом API: ${r.existing_api}`);
      setSt("import", true, bits.join(" · "));
      $("#st-import").title = r.skipped.map((s) => `${s.dialog_id}: ${s.reason}`).join("\n");
    } else setSt("import", false, r.error || "Файл не прошёл проверку");
    btn.disabled = !r.ok;
  }
  async function applyImport() {
    try {
      const r = await api("/api/import", { method: "POST", body: { text: st.imp.text, replace_existing: $("#replace").checked } });
      if (!r.ok) return toast(r.error, true);
      toast(`Импортировано: ${r.imported}${r.kept.length ? `, не заменено (есть результат API): ${r.kept.length}` : ""}${r.skipped.length ? `, пропущено: ${r.skipped.length}` : ""}`);
      st.imp = null; st.impCheck = null; setOpen(true);
      await Promise.all([loadList(), loadCost()]); renderPanel(); renderTab(); if (st.sel) await selectDialog(st.sel);
      if (r.snapshot) toast(`Сохранено: results/${r.snapshot}`);
    } catch (e) { toast(e.message, true); }
  }

  /* ---------- запуск анализа (с подтверждением) ---------- */
  async function openRunModal() {
    const m = modal('<div class="muted">Считаю оценку...</div>');
    const render = async (incl) => {
      let est;
      try { est = await api("/api/analyze-estimate", { method: "POST", body: { force: incl } }); }
      catch (e) { m.el.innerHTML = `<div class="status-msg err">${esc(e.message)}</div>`; return; }
      const prov = st.settings?.providers[est.provider]?.label || est.provider;
      const cost = est.cost_usd != null ? `≈ $${est.cost_usd}` : esc(est.note || "");
      const done = est.analyzed_count || 0;
      m.el.innerHTML = `<h3>Запустить анализ</h3><div class="kv">
        <span>Провайдер</span><span>${esc(prov)}</span><span>Модель</span><span>${esc(est.model)}</span>
        <span>Диалогов к анализу</span><span>${est.count}</span>
        <span>Токены (приблизительно)</span><span>вход ${est.input_tokens.toLocaleString("ru-RU")} · выход ${est.output_tokens.toLocaleString("ru-RU")}</span>
        <span>Стоимость</span><span>${cost}</span></div>
        ${done > 0 ? `<div class="status-msg" style="margin-bottom:8px">Уже проанализировано диалогов: ${done}. Без галочки для них останутся прежние результаты.</div>
          <label class="row small" style="margin-bottom:10px"><input type="checkbox" id="incl" ${incl ? "checked" : ""}> Проанализировать заново уже проанализированные (${done})</label>` : ""}
        ${est.count && !est.has_key ? '<div class="status-msg err" style="margin-bottom:10px">API-ключ не задан. <a class="link" href="#/settings">Настройки</a></div>' : ""}
        <div class="row"><button class="btn primary" id="go" ${est.count ? (est.has_key ? "" : "disabled") : (done > 0 ? "" : "disabled")}>${est.count ? "Запустить" : "Показать результаты"}</button>
          <button class="btn" id="cancel">Отмена</button></div>`;
      $("#cancel", m.el).addEventListener("click", m.close);
      const cb = $("#incl", m.el); if (cb) cb.addEventListener("change", () => render(cb.checked));
      $("#go", m.el).addEventListener("click", async () => {
        m.close();
        try { st.job = await api("/api/analyze-all", { method: "POST", body: { force: incl } }); }
        catch (e) { return toast(e.message, true); }
        setOpen(true); await loadList(); renderTab();
        renderJob(); attachJob(true);
      });
    };
    render(false);
  }

  /* ---------- фоновая задача ---------- */
  function renderJob() {
    const box = $("#jobbox"); if (!box) return;
    const j = st.job;
    if (j && j.status === "running") {
      const pc = j.total ? Math.round((j.done / j.total) * 100) : 0;
      box.innerHTML = `<div class="progress"><div class="small"><b>Анализ: ${j.done}/${j.total}</b>${j.errors.length ? ` · ошибок: ${j.errors.length}` : ""}</div><div class="bar"><i style="width:${pc}%"></i></div></div>`;
    } else if (j && j.status === "aborted") {
      box.innerHTML = `<div class="small" style="color:var(--red)">${esc(j.errors[0]?.message || "Остановлено")}</div>`;
    } else if (j && j.status === "done" && j.total) {
      box.innerHTML = `<div class="small muted">Готово: ${j.done - j.errors.length}/${j.total}${j.errors.length ? `, ошибок: ${j.errors.length} (${esc(j.errors[0].message.slice(0, 90))})` : ""}</div>`;
    } else box.innerHTML = "";
    const run = $("#run"); if (run) run.disabled = !!(j && j.status === "running");
  }
  function attachJob(announce) {
    clearInterval(st.poll);
    const tick = async () => {
      let j;
      try { j = await api("/api/jobs/current"); } catch (e) { return clearInterval(st.poll); }
      if (j.status === "none") return clearInterval(st.poll);
      st.job = j; renderJob(); await loadList(); renderList();
      if (j.status !== "running") {
        clearInterval(st.poll);
        loadCost();
        if (st.sel) await selectDialog(st.sel);
        if (announce && j.status === "done") toast(`Анализ завершён: ${j.done - j.errors.length}/${j.total}${j.snapshot ? ". Сохранено: results/" + j.snapshot : ""}`, j.errors.length > 0);
      }
    };
    api("/api/jobs/current").then((j) => { if (j.status === "running") { st.job = j; renderJob(); st.poll = setInterval(tick, 1500); } else if (announce) tick(); }).catch(() => {});
  }

  /* ---------- вкладки ---------- */
  function renderTab() {
    const body = $("#tabbody"); if (!body) return;
    destroyChart();
    body.innerHTML = `<div class="layout"><div class="card list"><input type="text" id="search" placeholder="Поиск" value="${esc(st.search)}">
      <div class="ctl"><select id="group" title="Группировка">${Object.entries(GROUPS).map(([k, v]) => `<option value="${k}" ${k === st.group ? "selected" : ""}>${v[0]}</option>`).join("")}</select></div>
      <div id="list"></div></div><div id="detail"></div></div>`;
    $("#search").addEventListener("input", (e) => { st.search = e.target.value.toLowerCase(); renderList(); });
    $("#group").addEventListener("change", (e) => { st.group = e.target.value; ls.set("group", st.group); renderList(); });
    renderList();
    if (st.detail) renderDetail();
    if (!st.items.length) $("#detail").innerHTML = '<div class="banner">Загрузите dialogs.json и players.csv</div>';
  }

  function renderList() {
    const box = $("#list"); if (!box) return;
    const q = st.search;
    const rows = sorted().filter((i) => !q || `${i.player_id} ${i.dialog_id} ${i.manager}`.toLowerCase().includes(q));
    const item = (i) => `
      <div class="item ${i.dialog_id === st.sel ? "sel" : ""}" data-id="${i.dialog_id}">
        <span class="dot ${i.priority || ""}" title="${esc(PRIORITY[i.priority]?.[0] || "Не проанализирован")}"></span>
        <div><div class="t">${chIcon(i.channel)} ${esc(i.player_id)} <span class="m">${esc(i.dialog_id)}</span></div>
        <div class="m">${esc(i.manager)}${i.overall != null ? ` · оценка ${i.overall}` : ""}${i.analysis_status === "stale" ? ` · <span style="color:var(--amber)">${esc(i.stale_reasons.map((r) => r.label).join(", "))}</span>` : ""}${i.analysis_status === "missing" ? " · нет анализа" : ""}</div></div>
      </div>`;
    const gf = GROUPS[st.group]?.[1];
    if (!gf) box.innerHTML = rows.map(item).join("");
    else {
      const g = new Map();
      for (const i of rows) { const [label, key] = gf(i); if (!g.has(label)) g.set(label, { key, items: [] }); g.get(label).items.push(i); }
      const ordered = [...g.entries()].sort((x, y) => (typeof x[1].key === "number" ? x[1].key - y[1].key : String(x[1].key).localeCompare(String(y[1].key))));
      box.innerHTML = ordered.map(([label, v]) => `<div class="grp">${esc(label)} <span>${v.items.length}</span></div>${v.items.map(item).join("")}`).join("");
    }
    if (!rows.length && st.items.length) box.innerHTML = '<div class="muted small" style="padding:8px">Ничего не найдено</div>';
    box.querySelectorAll(".item").forEach((el) => el.addEventListener("click", () => { location.hash = "#/dialog/" + el.dataset.id; }));
  }

  async function selectDialog(id) {
    st.sel = id; ls.set("sel", id); renderList();
    try { st.detail = await api("/api/dialogs/" + id); } catch (e) { return toast(e.message, true); }
    if (!st.open) { st.detail.record = null; st.detail.freshness = { status: "missing", reasons: [] }; }
    renderDetail();
  }

  /* ---------- карточка диалога ---------- */
  function destroyChart() { if (st.chart) { st.chart.destroy(); st.chart = null; } }

  function renderDetail() {
    const el = $("#detail"); if (!el || !st.detail) return;
    const D = st.detail, d = D.dialog, p = D.player, rec = D.record, a = rec?.analysis;
    const ms = d.messages;
    const pPt = Object.fromEntries((a?.player_points || []).map((x) => [x.message_id, x]));
    const mPt = Object.fromEntries((a?.manager_points || []).map((x) => [x.message_id, x]));
    const link = (refs) => (refs || []).map((m) => `<a class="link" data-goto="${esc(m)}">${esc(shortRef(m))}</a>`).join("");

    const chips = [];
    if (a) {
      chips.push(`<span title="${esc(a.priority.reason)}">${badge(PRIORITY[a.priority.level], " приоритет")}</span>`);
      if (a.rg.signal !== "none") {
        const open = a.rg.handled !== "yes";
        chips.push(badge([`RG: ${RG_SIGNAL[a.rg.signal]}${HANDLED[a.rg.handled] ? " · " + HANDLED[a.rg.handled] : ""}`, open ? "b-red" : "b-amber"]));
      }
      for (const f of a.risk_flags || []) chips.push(badge([f === "chargeback_threat" ? "Угроза чарджбэка" : "Риск ухода", "b-orange"]));
      const src = rec.meta.source;
      chips.push(badge([`${SOURCE[src] || src}${rec.meta.model ? " · " + rec.meta.model : ""} · правила ${rec.meta.rules_version}`, "b-gray"]));
      for (const r of D.freshness.reasons || []) chips.push(badge([r.label, "b-amber"]));
    }
    for (const f of D.metrics.player.flags) chips.push(badge([FLAGS[f] || f, ["SELF_EXCLUDED", "PAYMENT_FAILURES", "DEPOSIT_DROP"].includes(f) ? "b-red" : "b-gray"]));

    const peak = a && a.player_points.length ? a.player_points.reduce((x, y) => (y.dissatisfaction > x.dissatisfaction ? y : x)) : null;
    const vsum = a ? ["critical", "high", "medium", "low"].map((s) => [s, a.violations.filter((v) => v.severity === s).length]).filter((x) => x[1]).map((x) => `${x[1]} ${x[0]}`).join(" · ") : "";
    const fr = D.metrics.dialog.first_response_minutes;

    el.innerHTML = `
      <div class="card head"><div class="row" style="justify-content:space-between;align-items:flex-start">
        <div><h2>${chIcon(d.channel)} ${esc(d.player_id)} <span class="muted" style="font-weight:400">${esc(d.dialog_id)}</span></h2>
          <div class="meta">${esc(p.brand || "")} ${p.geo ? "· " + esc(p.geo) : ""} · ${esc(d.manager)} · ${esc(d.channel)} · ${fmtT(ms[0].sent_at)} - ${fmtT(ms[ms.length - 1].sent_at)}</div></div>
        <button class="btn primary" id="reanalyze" title="${esc(costTitle(D.cost))}">${rec ? "Проанализировать заново" : "Проанализировать"}${costTxt(D.cost) ? " · " + costTxt(D.cost) : ""}</button></div>
        <div class="chips">${chips.join("")}</div>
        ${a && a.profile.summary ? `<div style="margin-top:10px">${esc(a.profile.summary)}</div>` : ""}</div>
      ${!rec ? '<div class="banner">Анализ диалога не выполнен</div>' : ""}
      ${a ? `<div class="kpis">
        <div class="kpi"><div class="l">Оценка менеджера</div><div class="v">${a.quality.overall}<span class="s"> /100</span></div></div>
        <div class="kpi"><div class="l">Пик недовольства</div><div class="v">${peak ? peak.dissatisfaction : "-"}<span class="s"> ${peak ? esc(STATE[peak.state]) : ""}</span></div></div>
        <div class="kpi"><div class="l">Нарушения</div><div class="v">${a.violations.length}</div><div class="s">${esc(vsum) || "нет"}</div></div>
        <div class="kpi"><div class="l">Первый ответ</div><div class="v">${fr != null ? esc(fmtMin(fr)) : "-"}</div></div></div>` : ""}
      <div class="cols"><div>
        ${a ? `<div class="card"><h3>Эмоциональная динамика</h3><div class="chartbox"><canvas id="chart"></canvas></div>
          <div class="note">Приблизительная оценка модели</div></div>
        <div class="card"><h3>Нарушения и ошибки</h3>${a.violations.length ? a.violations.map((v) => `<div class="viol">
          <div class="vt">${badge(SEVERITY[v.severity])} <b>${esc(v.title)}</b> ${badge(CATEGORY[v.category])} ${link(v.message_refs)}</div>
          <div class="e">${esc(v.explanation)}</div></div>`).join("") : '<div class="muted">Нарушений не обнаружено</div>'}</div>` : ""}
        <div class="card"><h3>Переписка</h3><div class="msgs">${ms.map((m) => {
          const pp = pPt[m.id], mp = mPt[m.id];
          return `<div class="msg ${m.from}" data-mid="${esc(m.id)}"><div class="mh"><b>${m.from === "player" ? "Игрок" : "Менеджер"}</b><span>${fmtT(m.sent_at)}</span><span>${esc(shortRef(m.id))}</span>
            ${m.response_minutes != null ? `<span>ответ через ${fmtMin(m.response_minutes)}</span>` : ""}
            ${pp ? badge([`${pp.dissatisfaction} · ${STATE[pp.state]}`, "b-gray"]) : ""}${mp ? badge([`вовлечённость ${mp.engagement}`, "b-purple"]) : ""}</div>
            <div>${esc(m.text)}</div></div>`; }).join("")}</div></div>
      </div><div>
        ${a ? `<div class="card"><h3>Качество коммуникации</h3><div class="qgrid">${Object.entries(QUALITY).filter(([k]) => a.quality[k] != null).map(([k, l]) => `
          <div class="q"><div class="r"><span>${l}</span><b>${a.quality[k]}</b></div><div class="bar"><i style="width:${a.quality[k]}%"></i></div></div>`).join("")}</div>
          <div class="muted" style="font-size:13px">${esc(a.quality.summary)}</div></div>
        <div class="card"><h3>Рекомендации</h3>${a.recommendations.length ? a.recommendations.map((r, i) => `<div class="rec ${r.blocked ? "blocked" : r.kind}">
          ${badge([r.blocked ? "Заблокировано" : KIND[r.kind] || r.kind, r.blocked ? "b-amber" : "b-gray"])} ${esc(r.text)} ${link(r.message_refs)}
          ${r.caution ? `<div class="small muted">${esc(r.caution)}</div>` : ""}</div>`).join("") : '<div class="muted">Нет</div>'}</div>` : ""}
        ${playerCard(D)}
      </div></div>`;

    $("#reanalyze").addEventListener("click", () => runOne(d.dialog_id));
    el.querySelectorAll("[data-goto]").forEach((n) => n.addEventListener("click", () => gotoMsg(n.dataset.goto)));
    if (a) drawChart(D, pPt, mPt);
  }

  function playerCard(D) {
    const p = D.player, m = D.metrics.player;
    const rows = [
      ["Депозиты 30 дн.", p.deposits_30d_usd != null ? `${money(p.deposits_30d_usd)} (${p.deposits_30d_count ?? "-"})` : null],
      ["Предыдущие 30 дн.", p.deposits_prev_30d_usd != null ? `${money(p.deposits_prev_30d_usd)} (${p.deposits_prev_30d_count ?? "-"})` : null],
      ["Изменение суммы", m.deposit_usd_change_pct != null ? pct(m.deposit_usd_change_pct) : null],
      ["Средний депозит", m.avg_deposit_usd != null ? money(m.avg_deposit_usd) : null],
      ["Неудачных депозитов за 7 дн.", p.failed_deposits_7d],
      ["Ожидающий вывод", p.pending_withdrawal_usd ? money(p.pending_withdrawal_usd) : null],
      ["Бонусы 30 дн.", p.bonuses_30d_usd != null ? `${money(p.bonuses_30d_usd)} (${p.bonuses_30d_count ?? "-"})` : null],
      ["Дней с последнего входа", m.days_since_login],
    ].filter((r) => r[1] != null);
    return `<div class="card"><h3>Данные игрока</h3><div class="kv">${rows.map((r) => `<span>${r[0]}</span><span>${esc(r[1])}</span>`).join("")}</div></div>`;
  }

  function gotoMsg(id) {
    const el = document.querySelector(`.msg[data-mid="${CSS.escape(id)}"]`);
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 1800);
  }

  function drawChart(D, pPt, mPt) {
    destroyChart();
    const cv = $("#chart"); if (!cv) return;
    if (typeof Chart === "undefined") { cv.parentElement.innerHTML = '<div class="muted">Chart.js не загрузился (нет доступа к CDN)</div>'; return; }
    const ms = D.dialog.messages;
    const pl = ms.map((m) => pPt[m.id]?.dissatisfaction ?? null);
    const mg = ms.map((m) => mPt[m.id]?.engagement ?? null);
    const peak = Math.max(...pl.filter((v) => v != null), -1);
    const grid = "rgba(138,143,164,.15)", tick = "#8a8fa4";
    st.chart = new Chart(cv, {
      type: "line",
      data: { labels: ms.map((m) => fmtT(m.sent_at)), datasets: [
        { label: "Недовольство игрока", data: pl, borderColor: "#ef5b60", backgroundColor: "rgba(239,91,96,.12)", fill: true, tension: .3, spanGaps: true,
          pointRadius: pl.map((v) => (v === peak && peak >= 0 ? 7 : 4)), pointBackgroundColor: "#15171f", pointBorderWidth: 2 },
        { label: "Вовлечённость менеджера", data: mg, borderColor: "#8b7cf6", backgroundColor: "rgba(139,124,246,.10)", fill: true, tension: .3, spanGaps: true,
          pointRadius: 4, pointBackgroundColor: "#15171f", pointBorderWidth: 2 } ] },
      options: { responsive: true, maintainAspectRatio: false, interaction: { mode: "nearest", intersect: true },
        scales: { y: { min: 0, max: 100, grid: { color: grid }, ticks: { color: tick } }, x: { grid: { color: grid }, ticks: { color: tick, maxRotation: 0, autoSkip: true } } },
        onClick: (_, els) => { if (els.length) gotoMsg(ms[els[0].index].id); },
        plugins: { legend: { position: "top", labels: { boxWidth: 10, color: "#e8e9ef" } }, tooltip: { callbacks: {
          title: (it) => ms[it[0].dataIndex].id,
          label: (c) => { const m = ms[c.dataIndex];
            if (c.datasetIndex === 0) { const x = pPt[m.id]; return [`Недовольство: ${x.dissatisfaction}/100`, STATE[x.state]]; }
            const x = mPt[m.id]; const l = [`Вовлечённость: ${x.engagement}/100`];
            if (m.response_minutes != null) l.push(`Ответ через ${fmtMin(m.response_minutes)}`); return l; } } } } },
    });
  }

  async function runOne(id) {
    const btn = $("#reanalyze"); btn.disabled = true; btn.innerHTML = '<span class="spin"></span>Анализ...';
    try { const r = await api("/api/analyze/" + id, { method: "POST" }); setOpen(true); toast(`Анализ завершён${r.snapshot ? ". Сохранено: results/" + r.snapshot : ""}`); }
    catch (e) {
      toast(`${ERR_KIND[e.kind] || "Ошибка"}: ${e.message}`, true);
      if (e.kind === "config" || e.kind === "auth") setTimeout(() => (location.hash = "#/settings"), 1500);
    }
    await loadList(); renderList(); loadCost(); await selectDialog(id);
  }
})();
