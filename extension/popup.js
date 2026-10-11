"use strict";

const $ = (s) => document.querySelector(s);

function setStatus(kind, text) {
  const el = $("#status");
  el.hidden = !text;
  el.className = `status ${kind}`;
  el.textContent = text;
}

function renderLast(last) {
  const el = $("#last");
  if (!last) {
    el.textContent = "Noch nichts weitergeleitet.";
    el.style.color = "";
    return;
  }
  const t = new Date(last.t).toLocaleString("de-DE", {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  });
  el.textContent = `${t} · ${last.source}: ${last.msg}`;
  el.style.color = last.ok ? "" : "var(--err)";
}

async function testConnection(server) {
  if (!server) {
    setStatus("err", "Bitte zuerst die Adresse eintragen.");
    return;
  }
  setStatus("", "Teste …");
  let r, text;
  try {
    r = await fetch(`${server}/jdcheck.js`, {
      cache: "no-store", credentials: "omit", signal: AbortSignal.timeout(8000),
    });
    text = await r.text();
  } catch {
    setStatus("err", `Nicht erreichbar: ${server}. Adresse, Port und Firewall prüfen.`);
    return;
  }
  if (r.ok && text.includes("jdownloader=true")) {
    const v = (text.match(/version='(\d+)'/) || [])[1];
    const msg = `Verbunden. WebUI und JDownloader erreichbar${v ? ` (Version ${v})` : ""}.`;
    if (await hasSiteAccess()) setStatus("ok", msg);
    else setStatus("warn", `${msg} Es fehlt aber noch der Website-Zugriff (siehe oben).`);
  } else if (r.status === 503 || text.includes("jdownloader=false")) {
    setStatus("warn", "WebUI erreichbar, aber JDownloader antwortet nicht.");
  } else if (r.status === 403) {
    setStatus("err", "Die WebUI lehnt diesen PC ab. Seine IP in CNL_ALLOWED eintragen.");
  } else if (r.status === 401) {
    setStatus("err", "Das ist die Oberfläche (mit Login). Hier gehört der Click'n'Load-Port hin, Standard 9666.");
  } else if (r.status === 404) {
    setStatus("err", "Unter dieser Adresse läuft kein Click'n'Load-Empfänger. Port 9666 statt 8080?");
  } else {
    setStatus("err", `Unerwartete Antwort: HTTP ${r.status}`);
  }
}

async function renderAccess() {
  $("#access").hidden = await hasSiteAccess();
}

$("#grant").addEventListener("click", async () => {
  let granted = false;
  try {
    granted = await chrome.permissions.request(CNL_SITE_ACCESS);
  } catch {
    granted = false;
  }
  await renderAccess();
  if (granted) {
    setStatus("ok", "Website-Zugriff erlaubt.");
  } else {
    setStatus("err", "Nicht erlaubt. Alternativ in den Details der Erweiterung bei „Websitezugriff“ „Auf allen Websites“ wählen.");
  }
});

async function init() {
  await renderAccess();
  const cfg = await chrome.storage.sync.get(CNL_DEFAULTS);
  $("#server").value = cfg.server || "";
  $("#enabled").checked = cfg.enabled !== false;
  const { last } = await chrome.storage.local.get("last");
  renderLast(last);
  if (!cfg.server) $("#server").focus();
}

$("#form").addEventListener("submit", async (e) => {
  e.preventDefault();
  let server;
  try {
    server = normalizeServer($("#server").value);
  } catch (err) {
    setStatus("err", err.message);
    return;
  }
  $("#server").value = server;
  await chrome.storage.sync.set({ server, enabled: $("#enabled").checked });
  if (!server) {
    setStatus("warn", "Gespeichert. Ohne Adresse ist die Weiterleitung aus.");
    return;
  }
  await testConnection(server);
});

$("#enabled").addEventListener("change", async () => {
  await chrome.storage.sync.set({ enabled: $("#enabled").checked });
  setStatus("", $("#enabled").checked ? "Weiterleitung aktiv." : "Weiterleitung aus.");
});

$("#test").addEventListener("click", async () => {
  try {
    await testConnection(normalizeServer($("#server").value));
  } catch (err) {
    setStatus("err", err.message);
  }
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.last) renderLast(changes.last.newValue);
});

init();
