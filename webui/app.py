#!/usr/bin/env python3
"""
JD WebUI - schlanke Web-Oberfläche für JDownloader (lokale API, Port 3128).

Funktionen: URLs/DLC hinzufügen, Start/Halt/Stop, Fortschritt und aktuelle
Tätigkeit (Download/Entpacken) anzeigen. Basic Auth über WEBUI_USER/WEBUI_PASS.
"""

import base64
import hmac
import logging
import os
import re
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, jsonify, request, send_from_directory

import jdapi

WEBUI_USER = os.environ.get("WEBUI_USER", "")
WEBUI_PASS = os.environ.get("WEBUI_PASS", "")
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "5"))
# So lange wartet die WebUI nach dem Hinzufügen, bis JD die Links gesammelt hat
CONFIRM_TIMEOUT = float(os.environ.get("CONFIRM_TIMEOUT", "90"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("jd-webui")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

_pool = ThreadPoolExecutor(max_workers=12)

if not WEBUI_USER:
    log.warning("WEBUI_USER ist leer - die WebUI ist OHNE Login erreichbar!")


# ── Basic Auth ────────────────────────────────────────────────────────────────

def _auth_ok():
    auth = request.authorization
    if not auth or auth.type != "basic":
        return False
    user_ok = hmac.compare_digest((auth.username or "").encode(), WEBUI_USER.encode())
    pass_ok = hmac.compare_digest((auth.password or "").encode(), WEBUI_PASS.encode())
    return user_ok and pass_ok


@app.before_request
def require_login():
    if not WEBUI_USER or request.path == "/healthz":
        return None
    if _auth_ok():
        return None
    return ("Login erforderlich", 401,
            {"WWW-Authenticate": 'Basic realm="JDownloader WebUI", charset="UTF-8"'})


@app.after_request
def no_cache_api(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def fail(msg, status=200):
    return jsonify({"ok": False, "error": msg}), status


# ── Hinweise an das Frontend (Ergebnis von Hintergrund-Jobs) ──────────────────

_notices = deque(maxlen=20)
_notice_id = 0
_notice_lock = threading.Lock()


def notice(kind, msg):
    global _notice_id
    with _notice_lock:
        _notice_id += 1
        _notices.append({"id": _notice_id, "type": kind, "msg": msg})
    log.info("%s: %s", kind, msg)


# ── Übersicht (ein Aufruf liefert alles fürs Frontend) ────────────────────────

_PCT_RE = re.compile(r"(\d{1,3}(?:[.,]\d+)?)\s*%")
_cache = {"t": 0.0, "data": None}
_cache_lock = threading.Lock()
CACHE_SECONDS = 1.0


def _pct_from_text(text):
    m = _PCT_RE.search(text or "")
    if not m:
        return None
    try:
        return max(0.0, min(100.0, float(m.group(1).replace(",", "."))))
    except ValueError:
        return None


def _archive_for(links, queue):
    """Sucht das Archiv aus der Entpack-Warteschlange, das zu diesen Links gehört."""
    names = {l.get("name") for l in links if l.get("name")}
    if not names:
        return None
    for a in queue:
        files = set((a.get("states") or {}).keys())
        if files & names:
            return a
        aname = a.get("archiveName") or ""
        if aname and any(n.startswith(aname) for n in names):
            return a
    return None


def _package_view(p, links, queue):
    total = p.get("bytesTotal") or 0
    loaded = p.get("bytesLoaded") or 0
    status_text = p.get("status") or ""
    ex_states = [str(l.get("extractionStatus") or "") for l in links]
    files = len(links) or p.get("childCount") or 0
    files_done = sum(1 for l in links if l.get("finished"))
    downloading = bool(p.get("running")) or any(l.get("running") for l in links)
    finished = bool(p.get("finished")) or (files > 0 and files_done == files)
    # Der Entpack-Status der eigenen Links ist eindeutig. Die Warteschlange (Zuordnung
    # über Datei-/Archivnamen, die doppelt vorkommen können) dient nur für "wartet".
    extracting = "RUNNING" in ex_states
    archive = _archive_for(links, queue) if finished and not any(ex_states) else None

    dl_pct = min(100.0, loaded / total * 100) if total > 0 else (100.0 if finished else 0.0)
    pct, mode = dl_pct, "download"

    if downloading:
        phase, label = "download", "Download + Entpacken" if extracting else "Download"
    elif extracting:
        phase, label, mode = "extract", "Entpacken", "extract"
        pct = _pct_from_text(status_text)
    elif archive:
        phase, label, mode, pct = "extract", "Entpacken wartet", "extract", None
    elif any(s.startswith("ERR") for s in ex_states):
        phase, label = "error", "Entpack-Fehler"
    elif finished:
        phase = "done"
        label = "Fertig · entpackt" if "SUCCESSFUL" in ex_states else "Fertig"
    elif p.get("enabled") is False:
        phase, label = "waiting", "Deaktiviert"
    else:
        phase, label = "waiting", "Wartet"

    return {
        "uuid": p.get("uuid"),
        "name": p.get("name") or "Paket",
        "phase": phase,
        "label": label,
        "mode": mode,
        "pct": None if pct is None else round(pct, 1),
        "bytesLoaded": loaded,
        "bytesTotal": total,
        "speed": p.get("speed") or 0,
        "eta": p.get("eta") if (p.get("eta") or -1) > 0 else None,
        "files": files,
        "filesDone": files_done,
        "status": status_text,
        "saveTo": p.get("saveTo") or "",
    }


def _activity(state, links, queue, packages):
    parts = []
    running = [l for l in links if l.get("running")]
    if running:
        n = len(running)
        parts.append(f"Lädt {n} Datei{'en' if n != 1 else ''}")
    for a in queue:
        if a.get("controllerStatus") == "RUNNING":
            parts.append(f"Entpackt »{a.get('archiveName') or 'Archiv'}«")
    queued = [a for a in queue if a.get("controllerStatus") != "RUNNING"]
    if queued:
        n = len(queued)
        parts.append(f"{n} Archiv wartet aufs Entpacken" if n == 1
                     else f"{n} Archive warten aufs Entpacken")

    waiting = sum(1 for p in packages if p["phase"] == "waiting")
    if state == "PAUSE":
        parts.insert(0, "Pausiert")
    if not parts:
        if state == "RUNNING":
            parts.append("Läuft – wartet (Status der Pakete beachten)")
        elif waiting:
            parts.append("Bereit – 1 Paket wartet auf Start" if waiting == 1
                         else f"Bereit – {waiting} Pakete warten auf Start")
        else:
            parts.append("Nichts zu tun")
    return " · ".join(parts)


def build_overview():
    jobs = {
        "state": jdapi.get_state,
        "speed": jdapi.get_speed,
        "packages": jdapi.query_packages,
        "links": jdapi.query_links,
        "extraction": jdapi.extraction_queue,
        "grabber": jdapi.grabber_packages,
    }
    futures = {k: _pool.submit(fn) for k, fn in jobs.items()}
    res = {k: f.result() for k, f in futures.items()}

    with _notice_lock:
        notices = list(_notices)

    if not res["state"]["ok"]:
        return {"ok": True, "connected": False, "error": res["state"]["error"], "notices": notices}

    state = str(res["state"]["data"] or "").upper()
    links = jdapi.as_list(res["links"])
    queue = jdapi.as_list(res["extraction"])
    grabber = jdapi.as_list(res["grabber"])

    links_by_pkg = defaultdict(list)
    for l in links:
        links_by_pkg[l.get("packageUUID")].append(l)
    packages = [_package_view(p, links_by_pkg.get(p.get("uuid"), []), queue)
                for p in jdapi.as_list(res["packages"])]

    loaded = sum(p["bytesLoaded"] for p in packages)
    total = sum(p["bytesTotal"] for p in packages)
    # Linkgrabber/Entpack-Queue sind optional - nur Fehler der Kernabfragen anzeigen
    errors = [res[k]["error"] for k in ("packages", "links", "speed") if not res[k]["ok"]]

    return {
        "ok": True,
        "connected": True,
        "state": state,
        "speed": res["speed"]["data"] if res["speed"]["ok"] else 0,
        "activity": _activity(state, links, queue, packages),
        "total": {"loaded": loaded, "size": total,
                  "pct": round(loaded / total * 100, 1) if total else 0},
        "packages": packages,
        "grabber": {"packages": len(grabber),
                    "links": sum(g.get("childCount") or 0 for g in grabber)},
        "warning": errors[0] if errors else None,
        "notices": notices,
    }


def invalidate():
    with _cache_lock:
        _cache["t"] = 0.0


@app.get("/api/overview")
def overview():
    with _cache_lock:
        if _cache["data"] is not None and time.monotonic() - _cache["t"] < CACHE_SECONDS:
            return jsonify(_cache["data"])
        data = build_overview()
        _cache.update(t=time.monotonic(), data=data)
        return jsonify(data)


# ── Steuerung ─────────────────────────────────────────────────────────────────

@app.post("/api/control/<action>")
def control(action):
    if action == "start":
        r = jdapi.start()
    elif action == "stop":
        r = jdapi.stop()
    elif action == "pause":
        state = jdapi.get_state()
        if not state["ok"]:
            return jsonify(state)
        # Umschalten: ist JD pausiert, wird fortgesetzt
        r = jdapi.pause(str(state["data"]).upper() != "PAUSE")
    else:
        return fail("Unbekannte Aktion", 400)
    invalidate()
    log.info("Steuerung: %s -> %s", action, "ok" if r["ok"] else r["error"])
    return jsonify(r)


# ── Hinzufügen ────────────────────────────────────────────────────────────────

def _grabber_uuids():
    return {p.get("uuid") for p in jdapi.as_list(jdapi.grabber_packages())}


# Alles, was kurz nacheinander hinzugefügt wird (z. B. mehrere DLCs), landet in einem
# gemeinsamen "Batch". Ein Worker wartet, bis JD fertig gesammelt hat, und schiebt alle
# neuen Linkgrabber-Pakete in die Download-Liste. Einzelne Pakete lassen sich nicht
# zuverlässig einem Upload zuordnen - daher eine gemeinsame Meldung pro Batch.
_batch = None
_batch_lock = threading.Lock()
SETTLE_SECONDS = 3.0


def _queue_confirm(before, autostart, label):
    global _batch
    with _batch_lock:
        if _batch is None:
            _batch = {"before": before, "autostart": autostart, "labels": [label],
                      "last_add": time.monotonic()}
            threading.Thread(target=_confirm_worker, daemon=True).start()
        else:
            _batch["autostart"] = _batch["autostart"] or autostart
            _batch["labels"].append(label)
            _batch["last_add"] = time.monotonic()


def _confirm_worker():
    global _batch
    moved, quiet, error = set(), 0, None
    while True:
        time.sleep(1.5)
        with _batch_lock:
            before, last_add = _batch["before"], _batch["last_add"]
        collecting = jdapi.is_collecting()
        fresh = _grabber_uuids() - before - moved
        if fresh and collecting is not True:
            r = jdapi.grabber_to_downloads(fresh)
            if not r["ok"]:
                error = r["error"]
            else:
                moved |= fresh
                quiet = 0
                invalidate()
                continue
        idle = time.monotonic() - last_add
        if error is None and idle < CONFIRM_TIMEOUT:
            if not moved or collecting is True or idle < SETTLE_SECONDS:
                continue
            quiet += 1
            if quiet < 2:
                continue
        # Beenden - unter dem Lock prüfen, dass nicht gerade noch etwas dazukam
        with _batch_lock:
            if error is None and _batch["last_add"] != last_add:
                quiet = 0
                continue
            batch, _batch = _batch, None
            break

    labels = batch["labels"]
    label = ", ".join(labels[:3]) + (f" (+{len(labels) - 3})" if len(labels) > 3 else "")
    invalidate()
    if error:
        notice("err", f"{label}: Übernahme in die Downloads fehlgeschlagen – {error}")
        return
    if not moved:
        notice("warn", f"{label}: Keine neuen Pakete gefunden (Links offline oder Datei ungültig?).")
        return
    if batch["autostart"]:
        jdapi.start()
    n = len(moved)
    notice("ok", f"{label}: {n} Paket{'e' if n != 1 else ''} übernommen"
                 + (" und gestartet" if batch["autostart"] else ""))


@app.post("/api/add/url")
def add_url():
    data = request.get_json(silent=True) or {}
    links = str(data.get("links") or "").strip()
    if not links:
        return fail("Bitte mindestens eine URL eingeben.", 400)
    autostart = bool(data.get("autostart", True))
    package_name = str(data.get("packageName") or "").strip()
    password = str(data.get("password") or "").strip()

    before = _grabber_uuids()
    r = jdapi.add_links(links, package_name, autostart=False, extract_password=password)
    if not r["ok"]:
        return jsonify(r)
    count = len([l for l in links.splitlines() if l.strip()])
    log.info("URL hinzugefügt (%d Zeile(n))", count)
    _queue_confirm(before, autostart, package_name or "Links")
    invalidate()
    return jsonify({"ok": True, "message": "An JDownloader übergeben – Links werden geprüft …"})


CONTAINER_TYPES = {".dlc": "DLC", ".ccf": "CCF", ".rsdf": "RSDF"}


@app.post("/api/add/dlc")
def add_dlc():
    f = request.files.get("file")
    if not f or not f.filename:
        return fail("Keine Datei hochgeladen.", 400)
    ext = os.path.splitext(f.filename.lower())[1]
    ctype = CONTAINER_TYPES.get(ext)
    if not ctype:
        return fail("Nur .dlc, .ccf oder .rsdf Dateien.", 400)
    content = f.read()
    if not content:
        return fail("Die Datei ist leer.", 400)
    autostart = request.form.get("autostart", "true").lower() != "false"

    before = _grabber_uuids()
    r = jdapi.add_container(ctype, base64.b64encode(content).decode("ascii"))
    if not r["ok"]:
        return jsonify(r)
    log.info("Container hinzugefügt: %s (%d Bytes)", f.filename, len(content))
    _queue_confirm(before, autostart, f.filename)
    invalidate()
    return jsonify({"ok": True, "message": f"{f.filename} an JDownloader übergeben – wird entschlüsselt …"})


@app.errorhandler(413)
def too_large(_e):
    return fail(f"Datei zu groß (max. {MAX_UPLOAD_MB} MB).", 413)


@app.post("/api/linkgrabber/confirm_all")
def confirm_all():
    data = request.get_json(silent=True) or {}
    uuids = _grabber_uuids()
    if not uuids:
        return fail("Im Linkgrabber wartet nichts.")
    r = jdapi.grabber_to_downloads(uuids)
    if r["ok"] and data.get("autostart"):
        jdapi.start()
    invalidate()
    return jsonify(r)


# ── Aufräumen ─────────────────────────────────────────────────────────────────

@app.post("/api/downloads/remove")
def remove_package():
    data = request.get_json(silent=True) or {}
    try:
        uuid = int(data.get("packageId"))
    except (TypeError, ValueError):
        return fail("Ungültige Paket-ID.", 400)
    r = jdapi.remove_packages([uuid])
    invalidate()
    return jsonify(r)


@app.post("/api/downloads/cleanup")
def cleanup():
    r = jdapi.cleanup_finished()
    invalidate()
    return jsonify(r)


# ── Sonstiges ─────────────────────────────────────────────────────────────────

@app.get("/healthz")
def healthz():
    return "ok"


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


if __name__ == "__main__":
    # Nur für lokale Tests - im Container läuft gunicorn (siehe Dockerfile)
    app.run(host="0.0.0.0", port=int(os.environ.get("WEB_PORT", "8080")), threaded=True)
