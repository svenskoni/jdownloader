// Gemeinsame Hilfsfunktionen für background.js und popup.js

const CNL_DEFAULTS = { server: "", enabled: true };
const CNL_LOCAL_HOSTS = ["127.0.0.1", "localhost", "[::1]"];

// Ohne Zugriff auf alle Websites (Einstellung "Bei Klick" o. ä.) lässt der Browser die
// Erweiterung Anfragen, die eine Download-Seite an 127.0.0.1:9666 schickt, weder umleiten
// noch mitlesen. Dann kommt nichts bei der WebUI an.
const CNL_SITE_ACCESS = { origins: ["<all_urls>"] };

async function hasSiteAccess() {
  try {
    return await chrome.permissions.contains(CNL_SITE_ACCESS);
  } catch {
    return false;
  }
}

// "192.168.1.10:9666" -> "http://192.168.1.10:9666". Wirft Error mit Klartext bei Unsinn.
function normalizeServer(input) {
  let s = String(input || "").trim();
  if (!s) return "";
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(s)) s = "http://" + s;
  let u;
  try {
    u = new URL(s);
  } catch {
    throw new Error("Das ist keine gültige Adresse.");
  }
  if (u.protocol !== "http:" && u.protocol !== "https:") {
    throw new Error("Nur http:// oder https:// möglich.");
  }
  if (u.username || u.password) {
    throw new Error("Bitte ohne Benutzername/Passwort in der Adresse.");
  }
  const port = u.port || (u.protocol === "https:" ? "443" : "80");
  if (CNL_LOCAL_HOSTS.includes(u.hostname) && port === "9666") {
    throw new Error("127.0.0.1:9666 ist genau die Adresse, die umgeleitet wird. Hier gehört die Adresse des Servers hin.");
  }
  return (u.origin + u.pathname).replace(/\/+$/, "");
}

// Aus der WebUI heruntergeladene Erweiterung: config.json enthält die Server-Adresse.
// Sie gilt, solange im Browser noch nichts gespeichert ist (frische Installation).
async function packagedConfig() {
  try {
    const r = await fetch(chrome.runtime.getURL("config.json"), { cache: "no-store" });
    return r.ok ? await r.json() : {};
  } catch {
    return {};
  }
}

async function loadConfig() {
  const c = await chrome.storage.sync.get({ ...CNL_DEFAULTS, server: null });
  let raw = c.server;
  if (raw === null || raw === undefined) raw = (await packagedConfig()).server || "";
  let server = "";
  try {
    server = normalizeServer(raw);
  } catch {
    server = "";
  }
  return { server, enabled: c.enabled !== false };
}

function hostLabel(value) {
  try {
    return new URL(value).hostname.replace(/^www\./, "") || "unbekannte Seite";
  } catch {
    return "unbekannte Seite";
  }
}
