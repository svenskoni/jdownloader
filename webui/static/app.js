"use strict";

const $ = (s) => document.querySelector(s);
const POLL_MS = 2000;
const STATE_LABEL = {
  RUNNING: "Läuft", PAUSE: "Pausiert", STOPPED: "Gestoppt", STOPPING: "Stoppt …", IDLE: "Leerlauf",
};

// ── Formatierung ─────────────────────────────────────────────────────────────
const nf = (v, digits) => v.toLocaleString("de-DE", { maximumFractionDigits: digits });

function fmtBytes(n) {
  n = Number(n) || 0;
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1000 && i < units.length - 1) { n /= 1024; i++; }
  return `${nf(n, i ? 1 : 0)} ${units[i]}`;
}
const fmtSpeed = (n) => (Number(n) > 0 ? `${fmtBytes(n)}/s` : "–");

function fmtEta(s) {
  s = Math.round(Number(s) || 0);
  if (s <= 0) return "";
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  if (h) return `${h} h ${m} min`;
  if (m) return `${m} min`;
  return `${s} s`;
}

function setText(el, txt) {
  if (el.textContent !== txt) el.textContent = txt;
}

// ── Toasts ───────────────────────────────────────────────────────────────────
function toast(msg, type = "") {
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  $("#toasts").append(el);
  setTimeout(() => el.remove(), type === "err" ? 7000 : 4000);
}

// ── API ──────────────────────────────────────────────────────────────────────
// Basis-URL ohne Zugangsdaten: wurde die Seite als http://user:pass@host/ geöffnet,
// lehnt fetch() relative URLs sonst ab.
const BASE = (() => {
  const u = new URL(".", document.baseURI);
  u.username = "";
  u.password = "";
  return u.href;
})();

async function api(path, opts = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 15000);
  try {
    const res = await fetch(BASE + path, { ...opts, signal: ctrl.signal });
    if (res.status === 401) {
      // Anmeldung abgelaufen oder abgemeldet: zur Anmeldeseite
      location.href = BASE + "login";
      return new Promise(() => {});
    }
    try {
      return await res.json();
    } catch {
      return { ok: false, error: `HTTP ${res.status}` };
    }
  } catch (e) {
    return { ok: false, error: e.name === "AbortError" ? "Zeitüberschreitung" : "WebUI nicht erreichbar" };
  } finally {
    clearTimeout(timer);
  }
}

const post = (path, body) => api(path, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body || {}),
});

// ── Rendering ────────────────────────────────────────────────────────────────
const rows = new Map();          // uuid -> Element
let rowOrder = "";
let lastNotice = null;           // null = erster Abruf, alte Hinweise nicht anzeigen

function renderControls(state, connected) {
  const start = $("#btnStart"), pause = $("#btnPause"), stop = $("#btnStop");
  const active = state === "RUNNING" || state === "PAUSE";
  start.disabled = !connected || active || state === "STOPPING";
  pause.disabled = !connected || !active;
  stop.disabled = !connected || !active;
  setText(pause, state === "PAUSE" ? "▶ Fortsetzen" : "⏸ Halt");
}

function renderHeader(d) {
  if (d.auth !== undefined) $("#logoutForm").hidden = !d.auth;
  const badge = $("#stateBadge");
  if (!d.connected) {
    badge.className = "badge s-offline";
    setText(badge, "Keine Verbindung");
    setText($("#speed"), "–");
    renderControls(null, false);
    document.title = "JD WebUI";
    return;
  }
  badge.className = `badge s-${d.state || "unknown"}`;
  setText(badge, STATE_LABEL[d.state] || d.state || "?");
  setText($("#speed"), fmtSpeed(d.speed));
  renderControls(d.state, true);
  document.title = d.speed > 0 ? `${fmtSpeed(d.speed)} · JD WebUI` : "JD WebUI";
}

function renderNow(d) {
  setText($("#activity"), d.connected ? d.activity : `Keine Verbindung: ${d.error || "unbekannter Fehler"}`);
  const t = d.total || { loaded: 0, size: 0, pct: 0 };
  $("#totalBar").style.width = `${t.pct || 0}%`;
  setText($("#totalTxt"), t.size
    ? `Gesamt: ${fmtBytes(t.loaded)} von ${fmtBytes(t.size)} · ${nf(t.pct, 0)} %`
    : "");

  const warn = $("#warn");
  warn.hidden = !d.warning;
  setText(warn, d.warning || "");
}

const ICON_PLAY = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4.5 2.6v10.8a.6.6 0 0 0 .9.5l8.6-5.4a.6.6 0 0 0 0-1L5.4 2.1a.6.6 0 0 0-.9.5z"/></svg>';
const ICON_PAUSE = '<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="3.5" y="2.5" width="3" height="11" rx="1"/><rect x="9.5" y="2.5" width="3" height="11" rx="1"/></svg>';

function createRow(uuid) {
  const el = document.createElement("div");
  el.innerHTML = `
    <div class="pkg-top">
      <span class="badge"></span>
      <span class="pkg-name"></span>
      <span class="pkg-actions">
        <button class="btn icon act act-start" type="button" aria-label="Starten">${ICON_PLAY}</button>
        <button class="btn icon act act-pause" type="button" aria-label="Anhalten"
          title="Anhalten – nur dieses Paket (wird deaktiviert, Teilstücke bleiben)">${ICON_PAUSE}</button>
        <button class="btn icon act-remove" type="button" title="Aus der Liste entfernen (Dateien bleiben erhalten)">✕</button>
      </span>
    </div>
    <div class="pbar"><i></i></div>
    <div class="pkg-meta"></div>
    <div class="pkg-status"></div>`;
  const name = () => el.querySelector(".pkg-name").textContent;
  el.querySelector(".act-start").addEventListener("click", (e) => packageAction(uuid, "start", e.currentTarget));
  el.querySelector(".act-pause").addEventListener("click", (e) => packageAction(uuid, "pause", e.currentTarget));
  el.querySelector(".act-remove").addEventListener("click", () => removePackage(uuid, name()));
  return el;
}

function updateRow(el, p) {
  el.className = `pkg p-${p.phase}`;
  setText(el.querySelector(".badge"), p.label);
  const name = el.querySelector(".pkg-name");
  setText(name, p.name);
  name.title = p.saveTo ? `${p.name}\n${p.saveTo}` : p.name;

  // ▶ für wartende/angehaltene Pakete, ⏸ solange es lädt oder noch laden würde
  const start = el.querySelector(".act-start"), pause = el.querySelector(".act-pause");
  start.hidden = p.phase !== "waiting";
  pause.hidden = !p.enabled || !(p.phase === "download" || p.phase === "waiting");
  start.title = p.enabled ? "Jetzt starten – nur dieses Paket" : "Fortsetzen – nur dieses Paket";
  el.classList.toggle("disabled", !p.enabled);

  const bar = el.querySelector(".pbar");
  bar.classList.toggle("indeterminate", p.pct === null);
  bar.querySelector("i").style.width = `${p.pct ?? 0}%`;

  const meta = [];
  if (p.mode === "extract") {
    meta.push(p.pct !== null ? `Entpacken ${nf(p.pct, 0)} %` : p.label);
  } else {
    meta.push(`${nf(p.pct, 0)} %`);
    meta.push(`${fmtBytes(p.bytesLoaded)} / ${fmtBytes(p.bytesTotal)}`);
    if (p.speed > 0) meta.push(fmtSpeed(p.speed));
    if (p.eta) meta.push(`noch ${fmtEta(p.eta)}`);
  }
  meta.push(`${p.filesDone}/${p.files} Dateien`);
  if (p.category) meta.push(p.category);
  setText(el.querySelector(".pkg-meta"), meta.join(" · "));

  const st = el.querySelector(".pkg-status");
  const showStatus = p.status && p.status !== p.label;
  st.hidden = !showStatus;
  setText(st, showStatus ? `JD: ${p.status}` : "");
}

function renderPackages(d) {
  const pkgs = d.connected ? d.packages || [] : [];
  setText($("#pkgCount"), pkgs.length ? `(${pkgs.length})` : "");
  $("#empty").hidden = !d.connected || pkgs.length > 0;
  $("#btnCleanup").disabled = !pkgs.some((p) => p.phase === "done");

  const seen = new Set();
  for (const p of pkgs) {
    let el = rows.get(p.uuid);
    if (!el) { el = createRow(p.uuid); rows.set(p.uuid, el); }
    updateRow(el, p);
    seen.add(p.uuid);
  }
  for (const [uuid, el] of rows) {
    if (!seen.has(uuid)) { el.remove(); rows.delete(uuid); }
  }
  // Nur bei geänderter Reihenfolge neu einhängen (sonst flackern Hover/Fokus)
  const order = pkgs.map((p) => p.uuid).join(",");
  if (order !== rowOrder) {
    const box = $("#packages");
    for (const p of pkgs) box.append(rows.get(p.uuid));
    rowOrder = order;
  }
}

// ── Linkgrabber ──────────────────────────────────────────────────────────────
const grabRows = new Map();      // uuid -> Element
let grabOrder = "";
let categories = [];             // [{name, path}]

function renderCategories(d) {
  const list = d.categories || [];
  if (JSON.stringify(list) === JSON.stringify(categories)) return;
  categories = list;
  const sel = $("#addCat"), keep = sel.value;
  sel.length = 1;
  for (const c of list) sel.add(new Option(c.name, c.name));
  sel.value = list.some((c) => c.name === keep) ? keep : "";
  sel.hidden = !list.length;
  for (const [uuid, el] of grabRows) { el.remove(); grabRows.delete(uuid); }   // Chips neu aufbauen
  grabOrder = "";
}

function createGrabRow(uuid) {
  const el = document.createElement("div");
  el.className = "pkg grab";
  el.innerHTML = `
    <div class="pkg-top">
      <span class="pkg-name"></span>
      <span class="pkg-actions">
        <button class="btn sm primary act-confirm" type="button">Übernehmen</button>
        <button class="btn icon act-remove" type="button" title="Aus dem Linkgrabber entfernen">✕</button>
      </span>
    </div>
    <div class="pkg-meta"></div>
    <div class="chips" role="group" aria-label="Kategorie"></div>
    <div class="target small"><span class="target-path"></span>
      <button class="linkbtn act-folder" type="button" hidden>Ordner ändern</button></div>`;
  const chips = el.querySelector(".chips");
  for (const c of categories) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip";
    b.dataset.cat = c.name;
    b.textContent = c.name;
    b.title = `Ordner: ${c.path}`;
    b.addEventListener("click", () => setCategory(uuid, c.name, el));
    chips.append(b);
  }
  chips.hidden = !categories.length;
  el.querySelector(".act-confirm").addEventListener("click", (e) => confirmGrab(uuid, e.currentTarget));
  el.querySelector(".act-remove").addEventListener("click", () =>
    removeGrab(uuid, el.querySelector(".pkg-name").textContent));
  el.querySelector(".act-folder").addEventListener("click", () => changeFolder(uuid, el));
  return el;
}

function updateGrabRow(el, g) {
  el.dataset.category = g.category || "";
  el.dataset.saveTo = g.saveTo || "";
  setText(el.querySelector(".pkg-name"), g.name);
  el.querySelector(".pkg-name").title = g.name;
  const meta = [`${g.links} Link${g.links !== 1 ? "s" : ""}`];
  if (g.bytes) meta.push(fmtBytes(g.bytes));
  if (g.offline) meta.push(`${g.offline} offline`);
  if (g.unknown) meta.push(`${g.unknown} ungeprüft`);
  if (g.hosts.length) meta.push(g.hosts.slice(0, 3).join(", ") + (g.hosts.length > 3 ? " …" : ""));
  setText(el.querySelector(".pkg-meta"), meta.join(" · "));
  for (const b of el.querySelectorAll(".chip")) {
    const on = b.dataset.cat === g.category;
    b.classList.toggle("active", on);
    b.setAttribute("aria-pressed", on ? "true" : "false");
  }
  setText(el.querySelector(".target-path"), g.saveTo ? `Ziel: ${g.saveTo}` : "");
  el.querySelector(".act-folder").hidden = !g.category;
}

function renderGrabber(d) {
  const g = (d.connected && d.grabber) || { packages: 0, links: 0, list: [] };
  const list = g.list || [];
  $("#grabberCard").hidden = !list.length;
  setText($("#grabCount"), list.length
    ? `(${list.length} Paket${list.length !== 1 ? "e" : ""}, ${g.links} Link${g.links !== 1 ? "s" : ""})` : "");
  $("#btnConfirmAll").hidden = list.length < 2;

  const seen = new Set();
  for (const item of list) {
    let el = grabRows.get(item.uuid);
    if (!el) { el = createGrabRow(item.uuid); grabRows.set(item.uuid, el); }
    updateGrabRow(el, item);
    seen.add(item.uuid);
  }
  for (const [uuid, el] of grabRows) {
    if (!seen.has(uuid)) { el.remove(); grabRows.delete(uuid); }
  }
  const order = list.map((x) => x.uuid).join(",");
  if (order !== grabOrder) {
    const box = $("#grabList");
    for (const x of list) box.append(grabRows.get(x.uuid));
    grabOrder = order;
  }
}

function renderNotices(d) {
  const list = d.notices || [];
  const maxId = list.reduce((m, n) => Math.max(m, n.id), 0);
  if (lastNotice !== null) {
    for (const n of list) if (n.id > lastNotice) toast(n.msg, n.type);
  }
  lastNotice = Math.max(lastNotice ?? 0, maxId);
}

function renderCnl(d) {
  const last = d.cnl && d.cnl.last;
  const el = $("#cnlInfo");
  el.classList.toggle("err", !!(last && !last.ok));
  if (!last) {
    setText(el, "Click'n'Load: bereit, noch nichts empfangen");
    return;
  }
  const t = new Date(last.t).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
  let txt = `Click'n'Load zuletzt ${t} · ${last.source}`;
  if (last.links) txt += ` · ${last.links} Link${last.links !== 1 ? "s" : ""}`;
  else if (last.msg === "DLC") txt += " · DLC";
  if (!last.ok) txt += ` · Fehler: ${last.msg}`;
  setText(el, txt);
}

function render(d) {
  if (d.ok === false) d = { connected: false, error: d.error, notices: [] };
  renderHeader(d);
  renderNow(d);
  renderCategories(d);
  renderGrabber(d);
  renderPackages(d);
  renderCnl(d);
  renderNotices(d);
}

// ── Polling ──────────────────────────────────────────────────────────────────
let pollTimer = null;
let busy = false;

async function refresh() {
  if (busy) return;
  busy = true;
  try {
    render(await api("api/overview"));
  } finally {
    busy = false;
  }
}

function schedule() {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    if (!document.hidden) await refresh();
    schedule();
  }, POLL_MS);
}

const refreshSoon = () => setTimeout(refresh, 300);

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refresh();
});

// ── Aktionen ─────────────────────────────────────────────────────────────────
async function control(action) {
  for (const id of ["#btnStart", "#btnPause", "#btnStop"]) $(id).disabled = true;
  const r = await post(`api/control/${action}`);
  if (r.ok === false) toast(r.error, "err");
  refreshSoon();
}
$("#btnStart").addEventListener("click", () => control("start"));
$("#btnPause").addEventListener("click", () => control("pause"));
$("#btnStop").addEventListener("click", () => control("stop"));

async function removePackage(uuid, name) {
  if (!confirm(`„${name}“ aus der Liste entfernen?\nBereits geladene Dateien bleiben erhalten.`)) return;
  const r = await post("api/downloads/remove", { packageId: uuid });
  toast(r.ok ? "Paket entfernt" : r.error, r.ok ? "ok" : "err");
  refreshSoon();
}

$("#btnCleanup").addEventListener("click", async () => {
  const r = await post("api/downloads/cleanup");
  toast(r.ok ? "Fertige Downloads entfernt" : r.error, r.ok ? "ok" : "err");
  refreshSoon();
});

async function packageAction(uuid, action, btn) {
  btn.disabled = true;
  const r = await post("api/downloads/package", { packageId: uuid, action });
  btn.disabled = false;
  if (r.ok === false) toast(r.error, "err");
  refreshSoon();
}

// Linkgrabber
const store = {
  get(k, def) { try { const v = localStorage.getItem(k); return v === null ? def : v; } catch { return def; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* egal */ } },
};
$("#grabStart").checked = store.get("jdwebui.grabStart", "1") === "1";
$("#grabStart").addEventListener("change", () => store.set("jdwebui.grabStart", $("#grabStart").checked ? "1" : "0"));

async function setCategory(uuid, category, el, folder) {
  for (const b of el.querySelectorAll(".chip")) b.classList.toggle("active", b.dataset.cat === category);
  const body = { packageId: uuid, category };
  if (folder !== undefined) body.folder = folder;
  const r = await post("api/linkgrabber/category", body);
  if (r.ok) setText(el.querySelector(".target-path"), `Ziel: ${r.saveTo}`);
  else toast(r.error, "err");
  refreshSoon();
}

function changeFolder(uuid, el) {
  const cat = el.dataset.category;
  if (!cat) return;
  const base = (categories.find((c) => c.name === cat) || {}).path || "";
  const path = el.dataset.saveTo || "";
  const sub = base && path.startsWith(base + "/") ? path.slice(base.length + 1) : "";
  const v = prompt(`Unterordner in ${base}\n(leer = direkt dort, mit „/“ verschachtelt, z. B. „Serie/Staffel 1“)`, sub);
  if (v === null) return;
  setCategory(uuid, cat, el, v.trim());
}

async function confirmGrab(uuid, btn) {
  btn.disabled = true;
  const start = $("#grabStart").checked;
  const r = await post("api/linkgrabber/confirm", { packageId: uuid, autostart: start });
  btn.disabled = false;
  toast(r.ok ? (start ? "Übernommen und gestartet" : "In die Downloads übernommen") : r.error, r.ok ? "ok" : "err");
  refreshSoon();
}

async function removeGrab(uuid, name) {
  if (!confirm(`„${name}“ aus dem Linkgrabber entfernen?`)) return;
  const r = await post("api/linkgrabber/remove", { packageId: uuid });
  toast(r.ok ? "Aus dem Linkgrabber entfernt" : r.error, r.ok ? "ok" : "err");
  refreshSoon();
}

$("#btnConfirmAll").addEventListener("click", async () => {
  const start = $("#grabStart").checked;
  const r = await post("api/linkgrabber/confirm_all", { autostart: start });
  toast(r.ok ? (start ? "Alle übernommen und gestartet" : "In die Downloads übernommen") : r.error, r.ok ? "ok" : "err");
  refreshSoon();
});

$("#addForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const links = $("#urls").value.trim();
  if (!links) { toast("Bitte eine URL eingeben", "err"); return; }
  const btn = $("#btnAdd");
  btn.disabled = true;
  const r = await post("api/add/url", {
    links,
    packageName: $("#pkgName").value.trim(),
    password: $("#pwd").value.trim(),
    autostart: $("#autostart").checked,
    category: $("#addCat").value,
  });
  btn.disabled = false;
  if (r.ok) {
    toast(r.message, "ok");
    $("#urls").value = "";
    $("#pkgName").value = "";
    $("#pwd").value = "";
  } else {
    toast(r.error, "err");
  }
  refreshSoon();
});

// DLC-Upload (Klick, Tastatur, Drag & Drop)
const drop = $("#drop"), dlcInput = $("#dlcInput");

async function uploadFiles(files) {
  for (const f of files) {
    const fd = new FormData();
    fd.append("file", f);
    fd.append("autostart", $("#autostart").checked ? "true" : "false");
    fd.append("category", $("#addCat").value);
    const r = await api("api/add/dlc", { method: "POST", body: fd });
    toast(r.ok ? r.message : `${f.name}: ${r.error}`, r.ok ? "ok" : "err");
  }
  dlcInput.value = "";
  refreshSoon();
}

drop.addEventListener("click", () => dlcInput.click());
drop.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); dlcInput.click(); }
});
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", (e) => {
  e.preventDefault();
  drop.classList.remove("over");
  if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files);
});
dlcInput.addEventListener("change", () => {
  if (dlcInput.files.length) uploadFiles(dlcInput.files);
});

// ── Start ────────────────────────────────────────────────────────────────────
refresh().then(schedule);
