/*
 * JD WebUI Click'n'Load - Hintergrund
 *
 * Download-Seiten schicken Click'n'Load an http://127.0.0.1:9666 (JDownloader auf dem PC).
 * Die Erweiterung macht daraus zwei Dinge:
 *  1. Die Seite bekommt ihre Antwort direkt aus der Erweiterung (rules.json leitet
 *     127.0.0.1:9666 auf Dateien in cnl/ um: jdcheck.js, "success" ...). So gibt es
 *     kein Mixed-Content-Problem, auch wenn der Server nur per http erreichbar ist.
 *  2. Die eigentlichen Daten (Formular der Seite) liest webRequest mit und schickt
 *     sie von hier aus an den Click'n'Load-Port der WebUI.
 */

importScripts("common.js");

const RULESET = "cnl";
const FORWARD_PATHS = new Set(["/flash/add", "/flash/addcrypted", "/flash/addcrypted2"]);
const seen = new Set();   // requestIds, damit nichts doppelt geschickt wird

// ── Regeln ein/aus ─────────────────────────────────────────────────────────────

async function applyRules() {
  const cfg = await loadConfig();
  const active = cfg.enabled && !!cfg.server;
  const access = await hasSiteAccess();
  try {
    await chrome.declarativeNetRequest.updateEnabledRulesets(
      active ? { enableRulesetIds: [RULESET] } : { disableRulesetIds: [RULESET] });
  } catch (e) {
    console.error("[CNL] Regeln konnten nicht umgeschaltet werden:", e);
  }
  let text = "", color = "#6b7280", title = "JD WebUI Click'n'Load";
  if (!active) {
    text = "AUS";
    title += " (aus)";
  } else if (!access) {
    text = "!";
    color = "#dc2626";
    title += ": Website-Zugriff fehlt, Popup öffnen";
  }
  await chrome.action.setBadgeBackgroundColor({ color });
  await chrome.action.setBadgeText({ text });
  await chrome.action.setTitle({ title });
}

chrome.runtime.onInstalled.addListener(applyRules);
chrome.runtime.onStartup.addListener(applyRules);
chrome.permissions.onAdded.addListener(applyRules);
chrome.permissions.onRemoved.addListener(applyRules);
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "sync" && ("server" in changes || "enabled" in changes)) applyRules();
});

// ── Ergebnis merken und anzeigen ───────────────────────────────────────────────

async function report(ok, source, msg) {
  await chrome.storage.local.set({ last: { t: Date.now(), ok, source, msg } });
  try {
    await chrome.notifications.create("cnl", {
      type: "basic",
      iconUrl: "icons/128.png",
      title: ok ? "Click'n'Load übergeben" : "Click'n'Load fehlgeschlagen",
      message: `${source}: ${msg}`,
      priority: ok ? 0 : 2,
    });
  } catch (e) {
    console.warn("[CNL] Benachrichtigung nicht möglich:", e);
  }
}

// ── Weiterleiten ───────────────────────────────────────────────────────────────

function bodyOf(requestBody) {
  if (!requestBody) return "";
  if (requestBody.formData) {
    const p = new URLSearchParams();
    for (const [k, values] of Object.entries(requestBody.formData)) {
      for (const v of values) p.append(k, v);
    }
    return p.toString();
  }
  if (requestBody.raw) {
    const dec = new TextDecoder();
    return requestBody.raw.map((part) => (part.bytes ? dec.decode(part.bytes) : "")).join("");
  }
  return "";
}

async function forward(details) {
  const cfg = await loadConfig();
  if (!cfg.enabled || !cfg.server) return;   // aus: Anfrage geht ganz normal an 127.0.0.1

  const url = new URL(details.url);
  const isPost = details.method === "POST";
  let body = isPost ? bodyOf(details.requestBody) : "";
  const params = new URLSearchParams(isPost ? body : url.search);
  const page = details.initiator && details.initiator !== "null" ? details.initiator : "";
  if (isPost && !params.get("source") && page) {
    body += (body ? "&" : "") + "source=" + encodeURIComponent(page);
  }
  const source = hostLabel(params.get("source") || page);
  const target = cfg.server + url.pathname + url.search;

  let r, text;
  try {
    r = await fetch(target, {
      method: isPost ? "POST" : "GET",
      headers: isPost ? { "Content-Type": "application/x-www-form-urlencoded" } : undefined,
      body: isPost ? body : undefined,
      credentials: "omit",
      cache: "no-store",
      signal: AbortSignal.timeout(20000),
    });
    text = (await r.text()).trim();
  } catch (e) {
    console.warn("[CNL] Server nicht erreichbar:", target, e);
    await report(false, source, `WebUI nicht erreichbar (${cfg.server})`);
    return;
  }
  if (r.ok && text.startsWith("success")) {
    await report(true, source, "an die JD WebUI übergeben");
  } else if (r.status === 403) {
    await report(false, source, "von der WebUI abgelehnt (IP dieses PCs nicht in CNL_ALLOWED)");
  } else if (r.status === 404) {
    await report(false, source, "kein Click'n'Load-Empfänger unter dieser Adresse (Port 9666?)");
  } else {
    await report(false, source, text.replace(/^failed\s*/i, "") || `HTTP ${r.status}`);
  }
}

// Muss synchron beim Start des Service Workers registriert werden (MV3)
chrome.webRequest.onBeforeRequest.addListener(
  (details) => {
    if (seen.has(details.requestId)) return;
    if (details.initiator && details.initiator.startsWith(`chrome-extension://${chrome.runtime.id}`)) return;
    const path = new URL(details.url).pathname.replace(/\/+$/, "");
    if (!FORWARD_PATHS.has(path)) return;
    if (details.method !== "POST" && !new URL(details.url).search) return;
    seen.add(details.requestId);
    setTimeout(() => seen.delete(details.requestId), 60000);
    forward(details).catch((e) => console.error("[CNL] Fehler beim Weiterleiten:", e));
  },
  { urls: ["http://127.0.0.1:9666/*", "http://localhost:9666/*"] },
  ["requestBody"]
);
