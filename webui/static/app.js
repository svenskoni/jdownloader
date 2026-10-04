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

  const g = d.grabber || { packages: 0, links: 0 };
  $("#grabberBanner").hidden = !g.packages;
  setText($("#grabberTxt"),
    `${g.packages} Paket${g.packages !== 1 ? "e" : ""} (${g.links} Link${g.links !== 1 ? "s" : ""}) `
    + `${g.packages !== 1 ? "warten" : "wartet"} im Linkgrabber auf Übernahme.`);

  const warn = $("#warn");
  warn.hidden = !d.warning;
  setText(warn, d.warning || "");
}

function createRow(uuid) {
  const el = document.createElement("div");
  el.innerHTML = `
    <div class="pkg-top">
      <span class="badge"></span>
      <span class="pkg-name"></span>
      <button class="btn icon" type="button" title="Aus der Liste entfernen (Dateien bleiben erhalten)">✕</button>
    </div>
    <div class="pbar"><i></i></div>
    <div class="pkg-meta"></div>
    <div class="pkg-status"></div>`;
  el.querySelector("button").addEventListener("click", () =>
    removePackage(uuid, el.querySelector(".pkg-name").textContent));
  return el;
}

function updateRow(el, p) {
  el.className = `pkg p-${p.phase}`;
  setText(el.querySelector(".badge"), p.label);
  const name = el.querySelector(".pkg-name");
  setText(name, p.name);
  name.title = p.saveTo ? `${p.name}\n${p.saveTo}` : p.name;

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

function renderNotices(d) {
  const list = d.notices || [];
  const maxId = list.reduce((m, n) => Math.max(m, n.id), 0);
  if (lastNotice !== null) {
    for (const n of list) if (n.id > lastNotice) toast(n.msg, n.type);
  }
  lastNotice = Math.max(lastNotice ?? 0, maxId);
}

function render(d) {
  if (d.ok === false) d = { connected: false, error: d.error, notices: [] };
  renderHeader(d);
  renderNow(d);
  renderPackages(d);
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

$("#btnConfirmAll").addEventListener("click", async () => {
  const r = await post("api/linkgrabber/confirm_all", { autostart: $("#autostart").checked });
  toast(r.ok ? "In die Downloads übernommen" : r.error, r.ok ? "ok" : "err");
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
