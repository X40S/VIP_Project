"use strict";
/* Страница «Правила анализа»: просмотр, редактирование, версии. */
(() => {
  const rs = { data: null, draft: [], dirty: false };

  window.pages.rules = async () => {
    app.innerHTML = '<div class="muted">Загрузка...</div>';
    try { rs.data = await api("/api/rules"); } catch (e) { app.innerHTML = `<div class="status-msg err">${esc(e.message)}</div>`; return; }
    rs.draft = JSON.parse(JSON.stringify(rs.data.sections)); rs.dirty = false;
    render();
  };

  const markDirty = () => { rs.dirty = true; const b = $("#save"); if (b) b.disabled = false; };
  const autosize = (t) => { t.style.height = "auto"; t.style.height = Math.max(90, t.scrollHeight + 2) + "px"; };

  function render() {
    const d = rs.data;
    app.innerHTML = `<div class="topbar"><div class="row"><h1>Правила анализа</h1>${badge([`${d.version} · ${fmtDate(d.saved_at)}`, "b-purple"])}</div>
      <div class="row"><button class="btn primary" id="save" ${rs.dirty ? "" : "disabled"}>Сохранить</button></div></div>
      <div id="secs">${rs.draft.map(sectionHtml).join("")}</div>
      <button class="addbtn" id="add" title="Добавить правило">+</button>
      <div class="card"><h3>Версии</h3>${d.versions.map((v) => `<div class="row" style="padding:5px 0;border-top:1px solid var(--line)">
        <b>v${v.version}</b><span class="muted small grow">${fmtDate(v.saved_at)}${v.note ? " · " + esc(v.note) : ""}</span>
        ${"v" + v.version === d.version ? badge(["Текущая", "b-green"]) : `<button class="btn small" data-restore="${v.version}">Восстановить</button>`}</div>`).join("")}</div>`;

    document.querySelectorAll("#secs textarea").forEach(autosize);
    $("#secs").addEventListener("input", (e) => {
      const card = e.target.closest("[data-i]"); if (!card) return;
      const s = rs.draft[+card.dataset.i];
      if (e.target.matches("textarea")) { s.body = e.target.value; autosize(e.target); }
      else if (e.target.matches("input[type=text]")) s.title = e.target.value;
      markDirty();
    });
    $("#secs").addEventListener("click", (e) => {
      const card = e.target.closest("[data-i]"); if (!card) return;
      const i = +card.dataset.i;
      if (e.target.closest("[data-del]")) { rs.draft.splice(i, 1); markDirty(); render(); }
      if (e.target.matches("[data-toggle]")) { rs.draft[i].enabled = e.target.checked; markDirty(); card.classList.toggle("off", !e.target.checked); }
    });
    $("#add").addEventListener("click", () => {
      rs.draft.push({ id: "", title: "Новое правило", body: "", custom: true, enabled: true });
      markDirty(); render();
      const ts = document.querySelectorAll("#secs input[type=text]"); ts[ts.length - 1].focus(); ts[ts.length - 1].select();
    });
    $("#save").addEventListener("click", save);
    document.querySelectorAll("[data-restore]").forEach((b) => b.addEventListener("click", () => confirmRestore(+b.dataset.restore)));
  }

  function sectionHtml(s, i) {
    return `<div class="card rule ${s.enabled ? "" : "off"}" data-i="${i}">
      <div class="rh">${badge([s.custom ? "Своё" : "Встроенное", s.custom ? "b-purple" : "b-gray"])}
        <input type="text" value="${esc(s.title)}" maxlength="80" class="grow">
        ${s.custom ? `<label class="row small"><input type="checkbox" data-toggle ${s.enabled ? "checked" : ""}> Включено</label>` : ""}
        <button class="xbtn" data-del title="Удалить" aria-label="Удалить">&#10005;</button></div>
      <textarea spellcheck="false">${esc(s.body)}</textarea></div>`;
  }

  async function save() {
    const b = $("#save"); b.disabled = true;
    try {
      const before = rs.data.version;
      rs.data = await api("/api/rules", { method: "PUT", body: { sections: rs.draft } });
      toast(rs.data.version === before ? "Изменений нет" : `Сохранено: ${rs.data.version}`);
    } catch (e) { b.disabled = false; return toast(e.message, true); }
    rs.draft = JSON.parse(JSON.stringify(rs.data.sections)); rs.dirty = false; render();
  }

  function confirmRestore(v) {
    const m = modal(`<h3>Восстановить v${v}?</h3><div class="muted" style="margin-bottom:12px">Содержимое станет новой версией. ${rs.dirty ? "Несохранённые правки будут потеряны." : ""}</div>
      <div class="row"><button class="btn primary" id="ok">Восстановить</button><button class="btn" id="no">Отмена</button></div>`);
    $("#no", m.el).addEventListener("click", m.close);
    $("#ok", m.el).addEventListener("click", async () => {
      m.close();
      try { rs.data = await api("/api/rules/restore", { method: "POST", body: { version: v } }); toast(`Создана ${rs.data.version}`); }
      catch (e) { return toast(e.message, true); }
      rs.draft = JSON.parse(JSON.stringify(rs.data.sections)); rs.dirty = false; render();
    });
  }
})();
