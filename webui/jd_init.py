#!/usr/bin/env python3
"""
Init-Schritt vor dem JDownloader-Start (Service "jd-init" im Stack).

Schaltet in der JD-Config die lokale "Deprecated API" ein und erlaubt Zugriffe
von außerhalb des Containers (nicht nur localhost), damit die WebUI über das
Docker-Netz auf http://jdownloader:3128 zugreifen kann.

Läuft idempotent und beendet sich IMMER mit Exit 0 - ein Fehler hier darf den
JDownloader-Start nicht blockieren. Probleme werden nur geloggt.
"""

import json
import os
import sys
import time

CFG_DIR = os.environ.get("JD_CFG_DIR", "/jdconfig/cfg")
CFG_FILE = os.path.join(CFG_DIR, "org.jdownloader.api.RemoteAPIConfig.json")
API_PORT = 3128

# Werte, die immer erzwungen werden
FORCED = {
    "deprecatedapienabled": True,
    "deprecatedapilocalhostonly": False,
}


def log(msg):
    print(f"[jd-init] {msg}", flush=True)


def load_config():
    """Liest die bestehende Config. Liefert {} wenn sie fehlt oder kaputt ist (mit Backup)."""
    if not os.path.exists(CFG_FILE):
        log(f"{CFG_FILE} existiert noch nicht - wird neu angelegt.")
        return {}
    try:
        with open(CFG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        raise ValueError("kein JSON-Objekt")
    except (OSError, ValueError) as e:
        backup = f"{CFG_FILE}.broken-{int(time.time())}"
        log(f"WARNUNG: {CFG_FILE} nicht lesbar ({e}) - Backup nach {backup}, schreibe neu.")
        try:
            os.replace(CFG_FILE, backup)
        except OSError as e2:
            log(f"WARNUNG: Backup fehlgeschlagen ({e2}).")
        return {}


def write_config(data):
    os.makedirs(CFG_DIR, exist_ok=True)
    tmp = f"{CFG_FILE}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, CFG_FILE)


def main():
    data = load_config()
    changed = []

    for key, value in FORCED.items():
        if data.get(key) != value:
            changed.append(f"{key}: {data.get(key)!r} -> {value!r}")
            data[key] = value

    port = data.get("deprecatedapiport")
    if port is None:
        data["deprecatedapiport"] = API_PORT
        changed.append(f"deprecatedapiport: None -> {API_PORT}")
    elif port != API_PORT:
        log(f"HINWEIS: deprecatedapiport ist {port}, die WebUI erwartet {API_PORT} "
            f"(JD_API_URL im Stack anpassen).")

    if not changed:
        log("Lokale JD-API ist bereits freigeschaltet - nichts zu tun.")
        return

    write_config(data)
    for line in changed:
        log(f"gesetzt: {line}")
    log("Lokale JD-API freigeschaltet.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001 - niemals den JD-Start blockieren
        log(f"WARNUNG: Konnte die JD-Config nicht anpassen: {e}")
        log("Fallback: in der JD-GUI unter Einstellungen > Erweiterte Einstellungen nach "
            "'deprecated' filtern und die API manuell freischalten (siehe README).")
    sys.exit(0)
