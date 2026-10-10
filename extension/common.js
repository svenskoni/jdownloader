// Gemeinsame Hilfsfunktionen für background.js und popup.js

const CNL_DEFAULTS = { server: "", enabled: true };
const CNL_LOCAL_HOSTS = ["127.0.0.1", "localhost", "[::1]"];

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

async function loadConfig() {
  const c = await chrome.storage.sync.get(CNL_DEFAULTS);
  let server = "";
  try {
    server = normalizeServer(c.server);
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
