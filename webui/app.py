#!/usr/bin/env python3
"""
JD WebUI - schlanke Web-Oberfläche für JDownloader (lokale API, Port 3128).

Funktionen: URLs/DLC hinzufügen, Start/Halt/Stop, Fortschritt und aktuelle
Tätigkeit (Download/Entpacken) anzeigen, einzelne Pakete starten/anhalten.
Login über eine Anmeldeseite mit WEBUI_USER/WEBUI_PASS, danach bleibt der Browser per
Cookie angemeldet (SESSION_DAYS). Basic Auth funktioniert weiter, z. B. für Skripte.

Zusätzlich ein Click'n'Load-Empfänger auf eigenem Port (CNL_PORT, Standard 9666):
dort gibt es nur die CNL-Endpunkte, ohne Login (Download-Seiten können sich nicht
anmelden), optional beschränkt auf CNL_ALLOWED. CNL_ACTION legt fest, ob die Pakete
im Linkgrabber bleiben, übernommen oder auch gestartet werden.
"""

import base64
import io
import hashlib
import hmac
import html
import json
import logging
import os
import re
import threading
import time
import urllib.parse
import zipfile
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, Response, jsonify, redirect, request, send_from_directory

import cnl
import jdapi
import mover

WEBUI_USER = os.environ.get("WEBUI_USER", "")
WEBUI_PASS = os.environ.get("WEBUI_PASS", "")
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "5"))
# So lange wartet die WebUI nach dem Hinzufügen, bis JD die Links gesammelt hat
CONFIRM_TIMEOUT = float(os.environ.get("CONFIRM_TIMEOUT", "90"))
# So lange bleibt der Browser nach "Angemeldet bleiben" angemeldet
SESSION_DAYS = max(1, int(os.environ.get("SESSION_DAYS", "365")))
SESSION_COOKIE = "jdwebui_session"
# Kategorien im Linkgrabber: Name=Ordner im JDownloader-Container (siehe Volumes im Stack)
CATEGORIES_SPEC = os.environ.get(
    "CATEGORIES", "Anime=/medien/Anime;Filme=/medien/Filme;Serien=/medien/Serien;Spiele=/spiele")
# Ohne Kategorie übernommene Pakete landen hier (mit Unterordner pro Paket), leer = JD-Standardordner
FALLBACK_DIR = os.environ.get("FALLBACK_DIR", "/fertig").strip().rstrip("/")
# Zwischenablage (z. B. SSD): Pakete werden erst nach STAGING_DIR/<Paket> geladen, JD entpackt
# direkt ins Ziel (Kategorie / FALLBACK_DIR), danach verschiebt die WebUI den Rest dorthin und
# räumt den Ordner auf. Leer = wie früher direkt ins Ziel laden.
STAGING_DIR = os.environ.get("STAGING_DIR", "").strip().rstrip("/")
# So oft (Sekunden) prüft die WebUI, ob Pakete in der Zwischenablage fertig sind
POST_INTERVAL = max(5.0, float(os.environ.get("POST_INTERVAL", "20")))
# Diese Dateien werden beim Verschieben gelöscht statt mitgenommen (Endungen, mit , getrennt)
STAGING_JUNK = tuple(e.strip().lower() for e in
                     os.environ.get("STAGING_JUNK", ".sfv,.par2,.md5,.sha1,.nfo,.url").split(",")
                     if e.strip())

# Click'n'Load: Port im Container (gunicorn bindet ihn, siehe Dockerfile)
CNL_PORT = int(os.environ.get("CNL_PORT", "9666"))
# Was nach Click'n'Load mit den Paketen passiert:
#   grabber = im Linkgrabber lassen (in JD prüfen, Ordner wählen, selbst starten)
#   queue   = in die Download-Liste übernehmen, aber nicht starten
#   start   = übernehmen und Downloads starten
CNL_ACTIONS = {"grabber": "bleiben im Linkgrabber", "queue": "kommen in die Download-Liste",
               "start": "kommen in die Download-Liste und starten"}
CNL_ACTION = os.environ.get("CNL_ACTION", "grabber").strip().lower()
# Für die herunterladbare Erweiterung: unter welchem Port/welcher Adresse der CNL-Empfänger
# von den PCs aus erreichbar ist (Port-Mapping im Stack). Leer = Hostname der WebUI + Port.
CNL_PUBLIC_PORT = int(os.environ.get("CNL_PUBLIC_PORT") or CNL_PORT)
CNL_PUBLIC_URL = os.environ.get("CNL_PUBLIC_URL", "").strip().rstrip("/")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("jd-webui")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
# Erweiterung: im Image /app/extension, lokal im Repo ../extension
EXTENSION_DIR = os.environ.get("EXTENSION_DIR") or next(
    (d for d in (os.path.join(os.path.dirname(os.path.abspath(__file__)), "extension"),
                 os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "extension"))
     if os.path.isfile(os.path.join(d, "manifest.json"))), "")
app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

_pool = ThreadPoolExecutor(max_workers=12)

if not WEBUI_USER:
    log.warning("WEBUI_USER ist leer - die WebUI ist OHNE Login erreichbar!")

try:
    CNL_ALLOWED = cnl.parse_networks(os.environ.get("CNL_ALLOWED", ""))
except ValueError as e:
    # Lieber gar nichts annehmen als versehentlich alles
    log.error("CNL_ALLOWED ungültig (%s) - Click'n'Load nimmt keine Anfragen an!", e)
    CNL_ALLOWED = None
if CNL_ALLOWED == []:
    log.warning("CNL_ALLOWED ist leer - Click'n'Load (Port %d) nimmt Links von JEDER Adresse an.", CNL_PORT)
elif CNL_ALLOWED:
    log.info("Click'n'Load erlaubt für: %s", ", ".join(str(n) for n in CNL_ALLOWED))

if CNL_ACTION not in CNL_ACTIONS:
    log.warning("CNL_ACTION=%r unbekannt (grabber, queue, start) - nehme grabber.", CNL_ACTION)
    CNL_ACTION = "grabber"
log.info("Click'n'Load: Pakete %s (CNL_ACTION=%s)", CNL_ACTIONS[CNL_ACTION], CNL_ACTION)

def _parse_categories(spec):
    cats = {}
    for part in re.split(r"[;,\n]+", spec or ""):
        name, sep, path = part.partition("=")
        name, path = name.strip(), path.strip().rstrip("/")
        if not sep or not name or not path.startswith("/"):
            if part.strip():
                log.warning("CATEGORIES: Eintrag %r ignoriert (Format Name=/pfad)", part.strip())
            continue
        cats[name] = path
    return cats


CATEGORIES = _parse_categories(CATEGORIES_SPEC)
CATEGORY_LIST = [{"name": k, "path": v} for k, v in CATEGORIES.items()]
if FALLBACK_DIR and not FALLBACK_DIR.startswith("/"):
    log.warning("FALLBACK_DIR=%r ignoriert (muss mit / beginnen)", FALLBACK_DIR)
    FALLBACK_DIR = ""
log.info("Ohne Kategorie: %s", f"{FALLBACK_DIR}/<Paket>" if FALLBACK_DIR else "JD-Standardordner")
log.info("Kategorien: %s", ", ".join(f"{k} -> {v}" for k, v in CATEGORIES.items()) or "keine")

CNL_PATHS = {"/jdcheck.js", "/crossdomain.xml", "/flash", "/flash/",
             "/flash/add", "/flash/addcrypted", "/flash/addcrypted2"}


# ── Login (Cookie) und Basic Auth ─────────────────────────────────────────────
# Das Cookie enthält nur Ablaufzeit + Signatur. Der Schlüssel hängt an Benutzer und
# Passwort: Wird das Passwort geändert, sind alle bestehenden Anmeldungen ungültig.
# Er ist über Neustarts/Neubau des Containers stabil, man bleibt also angemeldet.

_SESSION_KEY = hashlib.sha256(f"jd-webui-session|{WEBUI_USER}|{WEBUI_PASS}".encode()).digest()


def _session_sig(exp):
    return hmac.new(_SESSION_KEY, f"{WEBUI_USER}|{exp}".encode(), hashlib.sha256).hexdigest()


def _session_token(exp):
    return f"{exp}.{_session_sig(exp)}"


def _session_ok():
    exp, _, sig = (request.cookies.get(SESSION_COOKIE) or "").partition(".")
    if not exp.isdigit() or int(exp) < time.time():
        return False
    return hmac.compare_digest(sig.encode(), _session_sig(exp).encode())


def _creds_ok(user, password):
    user_ok = hmac.compare_digest((user or "").encode(), WEBUI_USER.encode())
    pass_ok = hmac.compare_digest((password or "").encode(), WEBUI_PASS.encode())
    return user_ok and pass_ok


def _auth_ok():
    auth = request.authorization
    if not auth or auth.type != "basic":
        return False
    return _creds_ok(auth.username, auth.password)


def _on_cnl_port():
    # gunicorn/werkzeug setzen SERVER_PORT auf den Port des Listeners, nicht aus dem Host-Header
    return request.environ.get("SERVER_PORT") == str(CNL_PORT)


@app.before_request
def require_login():
    if _on_cnl_port():
        # CNL-Port: nur die CNL-Endpunkte, ohne Login, optional nach Absender gefiltert
        if request.path not in CNL_PATHS:
            return ("Nicht gefunden", 404)
        if CNL_ALLOWED is None or not cnl.ip_allowed(request.remote_addr, CNL_ALLOWED):
            log.warning("Click'n'Load von %s abgelehnt (nicht in CNL_ALLOWED)", request.remote_addr)
            return ("Nicht erlaubt", 403)
        if request.method == "OPTIONS":
            return Response(status=204)
        return None
    if request.path in CNL_PATHS:
        return ("Nicht gefunden", 404)
    if not WEBUI_USER or request.path in ("/healthz", "/login") or request.path.startswith("/static/"):
        return None
    if _session_ok() or _auth_ok():
        return None
    if request.path.startswith("/api/"):
        # Kein WWW-Authenticate: sonst öffnet der Browser wieder sein Passwortfenster
        return jsonify({"ok": False, "error": "Nicht angemeldet", "login": True}), 401
    # Relativ weiterleiten, damit es auch hinter einem Proxy mit Pfad-Präfix passt
    return redirect("../" * (request.path.count("/") - 1) + "login", code=303)


@app.after_request
def no_cache_api(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    if _on_cnl_port():
        # Wie JDownloader selbst: Download-Seiten (fremde Origin) dürfen den Port ansprechen
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Private-Network"] = "true"
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


# Letzte Click'n'Load-Anfrage (Anzeige in der Oberfläche, hilft beim Einrichten)
_cnl_last = {}
_cnl_lock = threading.Lock()


def _cnl_record(source, links, ok, msg=""):
    with _cnl_lock:
        _cnl_last.clear()
        _cnl_last.update(t=int(time.time() * 1000), source=source, links=links, ok=ok, msg=msg)


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


_BAD_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def _folder_name(name):
    """Paketname als Ordnername: ohne Pfadzeichen/Anführungszeichen, nicht zu lang."""
    n = re.sub(r"\s+", " ", _BAD_PATH_CHARS.sub(" ", name or "")).strip(" .")
    n = n[:150].strip(" .")
    return "" if n.lower() == "null" else n


def _folder_path(text):
    """Eigener Unterordner, auch verschachtelt ("Serie/Staffel 1"). Kein "..", keine
    absoluten Pfade: jeder Teil wird wie ein Ordnername bereinigt."""
    parts = [_folder_name(p) for p in re.split(r"[/\\]+", text or "")]
    return "/".join(p for p in parts if p)


def _under(path, base):
    p = (path or "").rstrip("/")
    return p == base or p.startswith(base + "/")


def _category_of(save_to):
    """Kategorie zu einem Ordner. Ordner in der Zwischenablage zählen mit ihrem Ziel."""
    save_to = _resolve(save_to)
    for name, path in sorted(CATEGORIES.items(), key=lambda kv: -len(kv[1])):
        if _under(save_to, path):
            return name
    return None


def _category_dir(category, package_name, folder=None):
    """Ordner der Kategorie + Unterordner. Immer mit Unterordner: ohne eigenen Namen
    der Paketname, damit nichts lose im Kategorie-Ordner landet."""
    base = CATEGORIES[category]
    sub = _folder_path(folder) or _folder_name(package_name)
    return f"{base}/{sub}" if sub else base


def _fallback_dir(package_name):
    sub = _folder_name(package_name)
    return f"{FALLBACK_DIR}/{sub}" if sub else FALLBACK_DIR


def _unsorted(save_to):
    """Weder Kategorie noch schon im Ordner für Pakete ohne Kategorie."""
    save_to = _resolve(save_to)
    return _category_of(save_to) is None and not (FALLBACK_DIR and _under(save_to, FALLBACK_DIR))


# ── Zwischenablage (SSD) ──────────────────────────────────────────────────────
# Mit STAGING_DIR bekommt jedes Paket bei der Übernahme den Download-Ordner
# STAGING_DIR/<Paket>. Das eigentliche Ziel (Kategorie-Ordner oder FALLBACK_DIR/<Paket>)
# merkt sich die WebUI in einer kleinen JSON-Datei in STAGING_DIR (übersteht Neustarts).
# Ein Hintergrund-Job setzt für die Archive des Pakets "Entpacken nach" = Ziel, damit JD
# direkt auf die HDD entpackt. Ist alles fertig, verschiebt er die übrigen Dateien (z. B.
# eine einzelne .mkv) ins Ziel, löscht Archivreste/Junk und den Ordner auf der SSD und
# nimmt das Paket aus der Download-Liste (nur aus der Liste).

_targets = {}               # Zwischenordner -> {"target": Ziel, "t": Zeitstempel}
_targets_lock = threading.Lock()


def _norm(path):
    return (path or "").strip().rstrip("/")


def _staging_on():
    return bool(STAGING_DIR)


def _staged(save_to):
    """Liegt der Ordner in der Zwischenablage (Unterordner, nicht der Ordner selbst)?"""
    st = _norm(save_to)
    return _staging_on() and st != STAGING_DIR and _under(st, STAGING_DIR)


def _targets_file():
    return os.path.join(STAGING_DIR, ".jd-webui-ziele.json")


def _targets_load():
    try:
        with open(_targets_file(), encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return
    except (OSError, ValueError) as e:
        log.warning("Zwischenablage: %s nicht lesbar (%s)", _targets_file(), e)
        return
    if not isinstance(data, dict):
        log.warning("Zwischenablage: %s hat ein unerwartetes Format - ignoriert", _targets_file())
        return
    with _targets_lock:
        for k, v in data.items():
            if isinstance(v, dict) and isinstance(v.get("target"), str):
                _targets[_norm(k)] = {"target": _norm(v["target"]), "t": float(v.get("t") or time.time())}


def _targets_save_locked():
    tmp = _targets_file() + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(_targets, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, _targets_file())
    except OSError as e:
        log.warning("Zwischenablage: Ziele nicht gespeichert (%s)", e)


def _target_put(stage, target):
    with _targets_lock:
        _targets[_norm(stage)] = {"target": _norm(target), "t": time.time()}
        _targets_save_locked()


def _target_drop(stage):
    with _targets_lock:
        if _targets.pop(_norm(stage), None) is not None:
            _targets_save_locked()


def _target_of(stage):
    with _targets_lock:
        e = _targets.get(_norm(stage))
    return e["target"] if e else None


def _resolve(save_to):
    """Ordner eines Pakets, wie ihn der Benutzer gewählt hat: bei Paketen in der
    Zwischenablage deren Ziel, sonst der Ordner selbst."""
    st = _norm(save_to)
    if _staged(st):
        return _target_of(st) or st
    return st


def _reserve_stage(name, target, current=None):
    """Zwischenordner für ein Paket festlegen und das Ziel dazu merken. Liegt das Paket
    schon in der Zwischenablage, bleibt sein Ordner (nur das Ziel ändert sich). Sonst
    Paketname, bei Kollision mit Zähler: zwei Pakete teilen sich nie einen Ordner."""
    cur, target = _norm(current), _norm(target)
    with _targets_lock:
        if _staged(cur) and cur in _targets:
            stage = cur
        else:
            base = _folder_name(name) or "Paket"
            stage, i = f"{STAGING_DIR}/{base}", 1
            while stage in _targets or os.path.lexists(stage):
                i += 1
                stage = f"{STAGING_DIR}/{base} ({i})"
        _targets[stage] = {"target": target, "t": time.time()}
        _targets_save_locked()
    return stage


def _set_dir(target, pkg):
    """Ordner eines Linkgrabber-Pakets setzen. Mit Zwischenablage lädt JD nach
    STAGING_DIR/<Paket>, das Ziel merkt sich die WebUI. Liefert das JD-Ergebnis."""
    target = _norm(target)
    if not _staging_on() or not target or _under(target, STAGING_DIR):
        return jdapi.grabber_set_directory(target, [pkg.get("uuid")])
    current = _norm(pkg.get("saveTo"))
    stage = _reserve_stage(pkg.get("name"), target, current)
    if current != stage:
        r = jdapi.grabber_set_directory(stage, [pkg.get("uuid")])
        if not r["ok"]:
            if not _staged(current):
                _target_drop(stage)
            return r
    return {"ok": True, "data": None}


def _apply_dirs(pkgs, category=None):
    """Ordner vor der Übernahme setzen: mit Kategorie deren Ordner, sonst bekommen
    unsortierte Pakete den Ordner für "ohne Kategorie". Liefert Fehlertext oder None."""
    for g in pkgs:
        if category:
            target = _category_dir(category, g.get("name"))
        elif FALLBACK_DIR and _unsorted(g.get("saveTo")):
            target = _fallback_dir(g.get("name"))
        elif _staging_on() and _norm(g.get("saveTo")) and not _under(g.get("saveTo"), STAGING_DIR):
            target = _norm(g.get("saveTo"))   # Ordner steht schon (z. B. in JD gewählt): über die SSD
        else:
            continue
        r = _set_dir(target, g)
        if not r["ok"]:
            return r["error"]
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
        if _staged(p.get("saveTo")):
            if _post_state["moving"] == p.get("uuid"):
                phase, label, mode, pct = "extract", "Verschiebt ins Ziel", "extract", None
            elif _ready_state(links, queue) == "wait":
                label = "Fertig · wartet aufs Entpacken"
            else:
                label = "Fertig · wird gleich verschoben"
    elif p.get("enabled") is False:
        phase, label = "waiting", "Angehalten"
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
        "saveTo": _resolve(p.get("saveTo")),
        "category": _category_of(p.get("saveTo")),
        "enabled": p.get("enabled") is not False,
    }


def _grabber_view(g):
    return {
        "uuid": g.get("uuid"),
        "name": g.get("name") or "Paket",
        "links": g.get("childCount") or 0,
        "bytes": g.get("bytesTotal") or 0,
        "online": g.get("onlineCount") or 0,
        "offline": g.get("offlineCount") or 0,
        "unknown": (g.get("unknownCount") or 0) + (g.get("tempUnknownCount") or 0),
        "hosts": g.get("hosts") or [],
        "saveTo": _resolve(g.get("saveTo")),
        **_grabber_target(g),
    }


def _grabber_target(g):
    """Kategorie, Unterordner (fürs Eingabefeld) und wohin das Paket bei Übernahme geht."""
    st, name = _resolve(g.get("saveTo")), g.get("name")
    cat = _category_of(st)
    if cat:
        return {"category": cat, "folder": st[len(CATEGORIES[cat]) + 1:], "target": st}
    target = _fallback_dir(name) if FALLBACK_DIR and _unsorted(st) else st
    return {"category": None, "folder": _folder_name(name), "target": target}


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
    with _cnl_lock:
        cnl_info = {"last": dict(_cnl_last) if _cnl_last else None}

    if not res["state"]["ok"]:
        return {"ok": True, "connected": False, "error": res["state"]["error"],
                "notices": notices, "cnl": cnl_info, "categories": CATEGORY_LIST, "fallbackDir": FALLBACK_DIR, "intake": _intake_pending(),
                "auth": bool(WEBUI_USER)}

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
                    "links": sum(g.get("childCount") or 0 for g in grabber),
                    "list": [_grabber_view(g) for g in grabber]},
        "categories": CATEGORY_LIST, "fallbackDir": FALLBACK_DIR, "intake": _intake_pending(),
        "auth": bool(WEBUI_USER),
        "warning": errors[0] if errors else None,
        "notices": notices,
        "cnl": cnl_info,
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

def _grabber_map():
    return {p.get("uuid"): p for p in jdapi.as_list(jdapi.grabber_packages())}


def _category_arg(value):
    """Kategorie aus dem Formular: leer = keine, unbekannt = Fehler (None, Fehlertext)."""
    value = str(value or "").strip()
    if value and value not in CATEGORIES:
        return None, "Unbekannte Kategorie."
    return value or None, None


# ── Eingang: alles aus einem Hinzufügen-Vorgang wird EIN Paket ────────────────
# JD verteilt die Links einer DLC / eines Click'n'Load gern auf viele Pakete (eins pro
# Datei). Jeder Vorgang (DLC-Upload, URL-Formular, CNL) wird deshalb gemerkt: Sobald JD
# fertig gesammelt hat, kommen alle seine Links in ein Paket mit dem Namen der DLC bzw.
# des CNL-Pakets und dem passenden Ordner (Kategorie oder FALLBACK_DIR/<Name>).
# Zuordnung über die Job-ID von JD (queryLinks mit jobUUIDs); kann JD das nicht, über
# den Vergleich mit dem Stand vor dem Hinzufügen.
SETTLE_SECONDS = 3.0
_intake = deque()
_intake_lock = threading.Lock()
_intake_state = {"busy": False, "current": None}
_claimed = set()      # Link-IDs, die schon einem Vorgang zugeordnet wurden (nur Fallback)


def _grabber_link_list(job_ids=None):
    return jdapi.as_list(jdapi.grabber_links(job_ids))


def _intake_pending():
    with _intake_lock:
        return len(_intake) + (1 if _intake_state["current"] else 0)


def _intake_add(add_fn, label, name=None, category=None, mode="grabber", from_file=False):
    """Führt den JD-Aufruf add_fn() aus und reiht den Vorgang ein. Liefert das JD-Ergebnis."""
    before = {l.get("uuid") for l in _grabber_link_list()}
    r = add_fn()
    if not r["ok"]:
        return r
    data = r.get("data")
    job_id = data.get("id") if isinstance(data, dict) and isinstance(data.get("id"), int) else None
    job = {"label": label, "name": (name or "").strip(), "category": category, "mode": mode,
           "from_file": from_file, "job_id": job_id, "before": before, "t": time.monotonic()}
    with _intake_lock:
        _intake.append(job)
        start = not _intake_state["busy"]
        _intake_state["busy"] = True
    if start:
        threading.Thread(target=_intake_worker, daemon=True).start()
    return r


def _intake_worker():
    while True:
        with _intake_lock:
            if not _intake:
                _intake_state.update(busy=False, current=None)
                return
            job = _intake.popleft()
            _intake_state["current"] = job
        try:
            _process_job(job)
        except Exception:  # noqa: BLE001 - Worker darf nicht sterben
            log.exception("Fehler beim Verarbeiten von %s", job["label"])
            notice("err", f"{job['label']}: interner Fehler beim Zusammenfassen")
        with _intake_lock:
            _intake_state["current"] = None
        invalidate()


def _job_links(job):
    """Links dieses Vorgangs. Mit Job-Filter von JD exakt, sonst alles Neue seit dem
    Hinzufügen (nur eindeutig, solange nichts anderes gleichzeitig hinzukommt)."""
    if job["job_id"] is not None:
        r = jdapi.grabber_links([job["job_id"]])
        mine = jdapi.as_list(r)
        # Ignoriert JD den Filter, kämen auch alte Links zurück -> dann Vergleich
        if r["ok"] and not any(l.get("uuid") in job["before"] for l in mine):
            if mine:
                return mine
            with _intake_lock:
                alone = not _intake
            if not alone:
                return []   # weitere Vorgänge warten - der Vergleich wäre nicht eindeutig
    all_links = _grabber_link_list()
    _claimed.intersection_update(l.get("uuid") for l in all_links)
    return [l for l in all_links
            if l.get("uuid") not in job["before"] and l.get("uuid") not in _claimed]


def _job_crawling(job):
    if job["job_id"] is not None:
        r = jdapi.crawler_jobs([job["job_id"]])
        if r["ok"] and isinstance(r["data"], list):
            return any(j.get("crawling") for j in r["data"] if isinstance(j, dict))
    return jdapi.is_collecting()


_SEP = " ._-"


def _common_name(names):
    """Gemeinsamer Anfang der Paketnamen, an einer Trennstelle abgeschnitten."""
    names = [n for n in names if n]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    pre = os.path.commonprefix(names)
    cut_mid_word = pre and pre[-1].isalnum() and any(len(n) > len(pre) and n[len(pre)].isalnum() for n in names)
    if cut_mid_word:
        m = re.match(r"^(.*[ ._-])", pre)
        pre = m.group(1) if m else ""
    pre = pre.strip(_SEP)
    return pre if len(pre) >= 3 else names[0]


def _looks_like_id(stem):
    """DLC-Namen wie "C3A52F7E1B" (filecrypt) sagen nichts - dann lieber den Dateinamen."""
    return bool(re.fullmatch(r"[A-Z0-9]{8,}|[a-f0-9]{8,}", stem)) and len(re.findall(r"\d", stem)) >= 2


def _job_name(job, pkg_names):
    derived = _common_name(pkg_names)
    name = job["name"]
    if name and job["from_file"] and _looks_like_id(name) and len(derived) >= 3:
        name = derived
    name = (name or derived or job["label"]).strip().lstrip("\"'").strip()
    return "Paket" if not name or name.lower() == "null" else name[:200]


def _process_job(job):
    # 1. Warten, bis JD alle Links dieses Vorgangs gesammelt hat
    deadline = job["t"] + CONFIRM_TIMEOUT
    last, links, seen, empty = None, [], False, 0
    while True:
        time.sleep(1.5)
        links = _job_links(job)
        ids = frozenset(l.get("uuid") for l in links)
        settled = time.monotonic() - job["t"] >= SETTLE_SECONDS
        crawling = _job_crawling(job)
        if ids:
            seen = True
            if ids == last and settled and crawling is not True:
                break
        elif seen:
            break               # schon übernommen oder entfernt (z. B. von Hand)
        elif settled and crawling is not True:
            empty += 1
            if empty >= 2:
                break           # JD ist fertig, aber nichts kam an
        last = ids
        if time.monotonic() > deadline:
            break
    if not links:
        if seen:
            log.info("%s: Links schon übernommen/entfernt - nichts zusammenzufassen", job["label"])
        else:
            notice("warn", f"{job['label']}: Keine Links gefunden (offline oder Datei ungültig?).")
        return
    link_ids = [l.get("uuid") for l in links]
    _claimed.update(link_ids)

    # 2. Name und Ordner
    pkgs = _grabber_map()
    pkg_ids = list(dict.fromkeys(l.get("packageUUID") for l in links))
    involved = [pkgs[p] for p in pkg_ids if p in pkgs]
    name = _job_name(job, [p.get("name") for p in involved])
    single = len(involved) == 1 and involved[0].get("childCount") == len(link_ids)
    category = job["category"]
    # Hat jemand in der Zwischenzeit schon eine Kategorie gewählt, gilt die
    chosen = next((p for p in involved if _category_of(p.get("saveTo"))), None)
    if category:
        target = _category_dir(category, name)
    elif chosen:
        category = _category_of(chosen.get("saveTo"))
        target = _resolve(chosen.get("saveTo")) if single else _category_dir(category, name)
    elif FALLBACK_DIR:
        target = _fallback_dir(name)
    else:
        target = next((_resolve(p.get("saveTo")) for p in involved if p.get("saveTo")), "")

    # 3. Ein Paket: nur Name/Ordner angleichen (Paket-ID bleibt). Mehrere: zusammenfassen.
    final_pkgs = set(pkg_ids)
    if single:
        pkg = dict(involved[0])
        if pkg.get("name") != name:
            jdapi.grabber_rename(pkg.get("uuid"), name)
            pkg["name"] = name
        restage = _staging_on() and not _staged(pkg.get("saveTo")) and not _under(target, STAGING_DIR)
        if target and (_resolve(pkg.get("saveTo")) != _norm(target) or restage):
            r = _set_dir(target, pkg)
            if not r["ok"]:
                notice("err", f"{job['label']}: Ordner setzen fehlgeschlagen – {r['error']}")
                return
    else:
        # Mit Zwischenablage bekommt das neue Paket gleich seinen Ordner auf der SSD
        dest = target
        if target and _staging_on() and not _under(target, STAGING_DIR):
            dest = _reserve_stage(name, target)
        r = jdapi.grabber_new_package(link_ids, name, dest)
        if not r["ok"] and dest != target:
            _target_drop(dest)
        if r["ok"]:
            now = {l.get("uuid"): l.get("packageUUID") for l in _grabber_link_list()}
            final_pkgs = {now[i] for i in link_ids if i in now} or final_pkgs
            log.info("%s: %d Link(s) aus %d Paketen -> »%s« (%s)", job["label"], len(link_ids),
                     len(pkg_ids), name, target or "JD-Standard")
        else:
            # Älteres JD ohne movetoNewPackage: wenigstens alles in denselben Ordner
            log.warning("%s: Zusammenfassen nicht möglich (%s) - setze nur den Ordner", job["label"], r["error"])
            if target:
                for p in pkg_ids:
                    _set_dir(target, pkgs.get(p) or {"uuid": p, "name": name})
    invalidate()

    # 4. Im Linkgrabber lassen oder übernehmen
    n = len(link_ids)
    what = f"{n} Link{'s' if n != 1 else ''} als »{name}«"
    if job["mode"] == "grabber":
        notice("ok", f"{job['label']}: {what} im Linkgrabber")
        return
    r = jdapi.grabber_to_downloads(final_pkgs)
    if not r["ok"]:
        notice("err", f"{job['label']}: Übernahme in die Downloads fehlgeschlagen – {r['error']}")
        return
    _post_wakeup()
    if job["mode"] == "start":
        jdapi.start()
    notice("ok", f"{job['label']}: {what} übernommen"
                 + (f" ({category})" if category else "")
                 + (" und gestartet" if job["mode"] == "start" else ""))


@app.post("/api/add/url")
def add_url():
    data = request.get_json(silent=True) or {}
    links = str(data.get("links") or "").strip()
    if not links:
        return fail("Bitte mindestens eine URL eingeben.", 400)
    autostart = bool(data.get("autostart", True))
    package_name = str(data.get("packageName") or "").strip()
    password = str(data.get("password") or "").strip()
    category, err = _category_arg(data.get("category"))
    if err:
        return fail(err, 400)

    r = _intake_add(lambda: jdapi.add_links(links, package_name, autostart=False, extract_password=password),
                    package_name or "Links", name=package_name, category=category,
                    mode="start" if autostart else "queue")
    if not r["ok"]:
        return jsonify(r)
    count = len([l for l in links.splitlines() if l.strip()])
    log.info("URL hinzugefügt (%d Zeile(n))%s", count, f" -> {category}" if category else "")
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
    category, err = _category_arg(request.form.get("category"))
    if err:
        return fail(err, 400)

    stem = os.path.splitext(os.path.basename(f.filename.replace("\\", "/")))[0]
    r = _intake_add(lambda: jdapi.add_container(ctype, base64.b64encode(content).decode("ascii")),
                    f.filename, name=stem, category=category, mode="start" if autostart else "queue",
                    from_file=True)
    if not r["ok"]:
        return jsonify(r)
    log.info("Container hinzugefügt: %s (%d Bytes)%s", f.filename, len(content),
             f" -> {category}" if category else "")
    invalidate()
    return jsonify({"ok": True, "message": f"{f.filename} an JDownloader übergeben – wird entschlüsselt …"})


@app.errorhandler(413)
def too_large(_e):
    return fail(f"Datei zu groß (max. {MAX_UPLOAD_MB} MB).", 413)


@app.post("/api/linkgrabber/confirm_all")
def confirm_all():
    data = request.get_json(silent=True) or {}
    pkgs = _grabber_map()
    if not pkgs:
        return fail("Im Linkgrabber wartet nichts.")
    # Pakete mit Kategorie behalten ihren Ordner, der Rest geht nach FALLBACK_DIR
    err = _apply_dirs(pkgs.values())
    if err:
        invalidate()
        return fail(err)
    r = jdapi.grabber_to_downloads(set(pkgs))
    if r["ok"] and data.get("autostart"):
        jdapi.start()
    _post_wakeup()
    invalidate()
    return jsonify(r)


def _package_id(data):
    try:
        return int(data.get("packageId"))
    except (TypeError, ValueError):
        return None


def _set_grabber_dir(pkg, data):
    """Ordner eines Linkgrabber-Pakets nach Kategorie (+ Unterordner) setzen.
    Kategorie leer/None = ohne Kategorie (FALLBACK_DIR). Liefert (Ziel, Fehlerantwort)."""
    category = data.get("category") or None
    if category is not None and category not in CATEGORIES:
        return None, fail("Unbekannte Kategorie.", 400)
    if category:
        target = _category_dir(category, pkg.get("name"), str(data.get("folder") or ""))
    elif FALLBACK_DIR:
        target = _fallback_dir(pkg.get("name"))
    else:
        return _resolve(pkg.get("saveTo")), None   # JD-Standardordner lassen
    r = _set_dir(target, pkg)
    if not r["ok"]:
        return None, jsonify(r)
    log.info("Linkgrabber: %s -> %s", pkg.get("name"), target)
    return target, None


@app.post("/api/linkgrabber/category")
def grabber_category():
    data = request.get_json(silent=True) or {}
    pid = _package_id(data)
    if pid is None:
        return fail("Ungültige Paket-ID.", 400)
    pkg = _grabber_map().get(pid)
    if not pkg:
        return fail("Das Paket ist nicht mehr im Linkgrabber.")
    target, err = _set_grabber_dir(pkg, data)
    invalidate()
    if err:
        return err
    return jsonify({"ok": True, "saveTo": target})


@app.post("/api/linkgrabber/confirm")
def grabber_confirm():
    data = request.get_json(silent=True) or {}
    pid = _package_id(data)
    if pid is None:
        return fail("Ungültige Paket-ID.", 400)
    pkg = _grabber_map().get(pid)
    if not pkg:
        return fail("Das Paket ist nicht mehr im Linkgrabber.")
    # Mit "category" im Request zählt die Auswahl der Oberfläche (inkl. Ordnername),
    # sonst bekommt ein unsortiertes Paket den Ordner für "ohne Kategorie"
    if "category" in data:
        _, err = _set_grabber_dir(pkg, data)
        if err:
            invalidate()
            return err
    else:
        e = _apply_dirs([pkg])
        if e:
            invalidate()
            return fail(e)
    r = jdapi.grabber_to_downloads([pid])
    if r["ok"] and data.get("autostart"):
        jdapi.start()
    _post_wakeup()
    invalidate()
    return jsonify(r)


@app.post("/api/linkgrabber/remove")
def grabber_remove():
    pid = _package_id(request.get_json(silent=True) or {})
    if pid is None:
        return fail("Ungültige Paket-ID.", 400)
    r = jdapi.grabber_remove([pid])
    invalidate()
    return jsonify(r)


# ── Click'n'Load (nur auf CNL_PORT erreichbar, siehe require_login) ───────────
# Antworten im Format von JDownloader: "success" bzw. "failed <grund>", immer HTTP 200.

def _cnl_text(body, status=200, mimetype="text/plain"):
    return Response(body, status=status, mimetype=mimetype)


def _cnl_label():
    return cnl.source_label(request.values.get("source"), request.headers.get("Referer"))


def _cnl_failed(label, msg, links=0):
    log.warning("Click'n'Load von %s (%s) fehlgeschlagen: %s", label, request.remote_addr, msg)
    notice("err", f"Click'n'Load ({label}): {msg}")
    _cnl_record(label, links, False, msg)
    invalidate()
    return _cnl_text(f"failed {msg}\r\n")


def _cnl_add_links(label, text):
    links = cnl.count_links(text)
    package = (request.values.get("package") or "").strip()
    password = cnl.first_password(request.values.get("passwords"))
    r = _intake_add(lambda: jdapi.add_links(text, package, autostart=False, extract_password=password),
                    f"Click'n'Load ({package or label})", name=package, mode=CNL_ACTION)
    if not r["ok"]:
        return _cnl_failed(label, r["error"], links)
    log.info("Click'n'Load von %s (%s): %d Link(s) an JD übergeben", label, request.remote_addr, links)
    _cnl_record(label, links, True)
    invalidate()
    return _cnl_text("success\r\n")


@app.get("/jdcheck.js")
def cnl_jdcheck():
    # Seiten prüfen damit, ob ein JDownloader lauscht. Ist JD nicht erreichbar, soll die
    # Seite das auch merken (wie bei einem PC ohne JD) und z. B. DLC anbieten.
    r = jdapi.call("/jd/version")
    if not r["ok"]:
        return _cnl_text("jdownloader=false;\r\n", status=503, mimetype="text/javascript")
    version = re.sub(r"\D", "", str(r["data"])) or "0"
    return _cnl_text(f"jdownloader=true;\r\nvar version='{version}';\r\n", mimetype="text/javascript")


@app.get("/crossdomain.xml")
def cnl_crossdomain():
    return _cnl_text('<?xml version="1.0"?>\r\n'
                     '<!DOCTYPE cross-domain-policy SYSTEM '
                     '"http://www.macromedia.com/xml/dtds/cross-domain-policy.dtd">\r\n'
                     '<cross-domain-policy>\r\n<allow-access-from domain="*" />\r\n'
                     '</cross-domain-policy>\r\n', mimetype="text/xml")


@app.route("/flash", methods=["GET", "POST"])
@app.route("/flash/", methods=["GET", "POST"])
def cnl_flash():
    return _cnl_text("JDownloader\r\n")


@app.route("/flash/add", methods=["GET", "POST"])
def cnl_add():
    label = _cnl_label()
    urls = request.values.get("urls") or ""
    if not cnl.count_links(urls):
        return _cnl_failed(label, "keine Links übermittelt")
    return _cnl_add_links(label, urls.replace("\r\n", "\n"))


@app.route("/flash/addcrypted2", methods=["GET", "POST"])
def cnl_addcrypted2():
    label = _cnl_label()
    try:
        key = cnl.parse_key(request.values.get("jk"), request.values.get("k"))
        text = cnl.decrypt(request.values.get("crypted") or "", key)
    except cnl.CnlError as e:
        return _cnl_failed(label, str(e))
    return _cnl_add_links(label, text)


@app.route("/flash/addcrypted", methods=["GET", "POST"])
def cnl_addcrypted():
    # Älteres CNL: "crypted" ist der Inhalt einer DLC-Datei
    label = _cnl_label()
    content = (request.values.get("crypted") or "").strip()
    if not content:
        return _cnl_failed(label, "keine Daten übermittelt (crypted fehlt)")
    r = _intake_add(lambda: jdapi.add_container("DLC", base64.b64encode(content.encode()).decode("ascii")),
                    f"Click'n'Load ({label})", mode=CNL_ACTION)
    if not r["ok"]:
        return _cnl_failed(label, r["error"])
    log.info("Click'n'Load (DLC) von %s (%s) an JD übergeben", label, request.remote_addr)
    _cnl_record(label, 0, True, "DLC")
    invalidate()
    return _cnl_text("success\r\n")


# ── Aufräumen ─────────────────────────────────────────────────────────────────

@app.post("/api/downloads/package")
def package_control():
    data = request.get_json(silent=True) or {}
    pid, action = _package_id(data), data.get("action")
    if pid is None:
        return fail("Ungültige Paket-ID.", 400)
    if action == "start":
        # Aktivieren und genau dieses Paket erzwungen starten (auch wenn der Rest steht)
        r = jdapi.set_enabled(True, [pid])
        if r["ok"]:
            r = jdapi.force_download([pid])
    elif action == "pause":
        # JD kennt kein Pausieren pro Paket: Deaktivieren hält es an, Teilstücke bleiben
        r = jdapi.set_enabled(False, [pid])
    else:
        return fail("Unbekannte Aktion", 400)
    invalidate()
    log.info("Paket %s: %s -> %s", pid, action, "ok" if r["ok"] else r["error"])
    return jsonify(r)


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
    if not _staging_on():
        r = jdapi.cleanup_finished()
        invalidate()
        return jsonify(r)
    # Pakete in der Zwischenablage nimmt der Hintergrund-Job erst nach dem Verschieben
    # aus der Liste - sonst blieben ihre Dateien auf der SSD liegen.
    res = {k: fn() for k, fn in (("packages", jdapi.query_packages), ("links", jdapi.query_links),
                                 ("queue", jdapi.extraction_queue))}
    if not res["packages"]["ok"]:
        return jsonify(res["packages"])
    links_by_pkg = defaultdict(list)
    for l in jdapi.as_list(res["links"]):
        links_by_pkg[l.get("packageUUID")].append(l)
    queue = jdapi.as_list(res["queue"])
    done = [p.get("uuid") for p in jdapi.as_list(res["packages"])
            if not _staged(p.get("saveTo"))
            and _package_view(p, links_by_pkg.get(p.get("uuid"), []), queue)["phase"] == "done"]
    r = jdapi.remove_packages(done) if done else {"ok": True, "data": None}
    invalidate()
    return jsonify(r)


# ── Zwischenablage: Hintergrund-Job ───────────────────────────────────────────

_ARCHIVE_RE = re.compile(r"\.(rar|zip|7z|tar|tgz|gz|bz2|xz|r\d{2}|z\d{2}|\d{3})$", re.IGNORECASE)
POST_SETTLE = 15.0          # so lange muss ein Paket fertig sein, bevor es verschoben wird
MOVE_RETRY = 600.0          # nach einem Fehler beim Verschieben erst so viel später erneut
PRUNE_AFTER = 2 * 86400     # gemerkte Ziele ohne Paket werden nach dieser Zeit vergessen

_post_wake = threading.Event()
_post_state = {"moving": None, "started": False, "archive_api": True, "last_prune": 0.0}
_post_lock = threading.Lock()
_archives_set = defaultdict(set)   # Paket-ID -> Archiv-IDs mit gesetztem Entpack-Ziel
_done_since = {}                   # Paket-ID -> seit wann (monotonic) fertig
_move_failed = {}                  # Paket-ID -> Zeitpunkt (monotonic) des letzten Fehlers
_warned = set()


def _post_wakeup():
    if _staging_on():
        _post_wake.set()


def _warn_once(key, kind, msg):
    if key not in _warned:
        _warned.add(key)
        notice(kind, msg)


def _ready_state(links, queue):
    """"ready" = alles geladen und entpackt, "error" = Entpack-Fehler, sonst "wait"."""
    active = [l for l in links if l.get("enabled") is not False]
    if not active or not all(l.get("finished") for l in active):
        return "wait"
    states = [str(l.get("extractionStatus") or "") for l in active]
    if any(s.startswith("ERR") for s in states):
        return "error"
    if "RUNNING" in states or _archive_for(active, queue):
        return "wait"
    # Archive ohne Entpack-Status: JD hat noch nicht angefangen (oder Auto-Entpacken ist aus)
    if any(_ARCHIVE_RE.search(l.get("name") or "") and str(l.get("extractionStatus") or "") != "SUCCESSFUL"
           for l in active):
        return "wait"
    return "ready"


def _set_archive_targets(pkg, target):
    """Für alle Archive des Pakets "Entpacken nach" = Ziel setzen (JD entpackt dann direkt
    auf die HDD) und die Archive nach dem Entpacken löschen lassen."""
    if not _post_state["archive_api"]:
        return
    uuid = pkg.get("uuid")
    r = jdapi.archive_info([uuid])
    if not r["ok"]:
        if "404" in r["error"] or "NOT_FOUND" in r["error"]:
            _post_state["archive_api"] = False
            log.warning("JD kennt extraction/getArchiveInfo nicht (%s) - Archive werden auf der SSD "
                        "entpackt und danach verschoben.", r["error"])
        return
    for a in jdapi.as_list(r):
        aid = a.get("archiveId") if isinstance(a, dict) else None
        if not aid or aid in _archives_set[uuid]:
            continue
        s = jdapi.set_archive_settings(aid, {"extractPath": target, "autoExtract": True,
                                             "removeFilesAfterExtraction": True})
        if s["ok"]:
            _archives_set[uuid].add(aid)
            log.info("Entpacken: »%s« -> %s", a.get("archiveName") or aid, target)
        else:
            log.warning("Entpack-Ziel für »%s« nicht gesetzt: %s", a.get("archiveName") or aid, s["error"])
            if "404" in s["error"] or "NOT_FOUND" in s["error"]:
                _post_state["archive_api"] = False
            return


def _move_package(pkg, links, stage, target):
    uuid, name = pkg.get("uuid"), pkg.get("name") or os.path.basename(stage)
    real_stage = os.path.realpath(stage)
    if not _under(real_stage, os.path.realpath(STAGING_DIR)) or real_stage == os.path.realpath(STAGING_DIR) \
            or _under(target, STAGING_DIR):
        _warn_once(("unsafe", stage), "err", f"»{name}«: {stage} -> {target} wird nicht verschoben (Pfad unzulässig).")
        return
    archives = {l.get("name") for l in links if _ARCHIVE_RE.search(l.get("name") or "")}
    _post_state["moving"] = uuid
    invalidate()
    try:
        moved, deleted = mover.move_tree(stage, target, drop_names=archives, junk_ext=STAGING_JUNK)
    except OSError as e:
        _move_failed[uuid] = time.monotonic()
        notice("err", f"»{name}«: Verschieben nach {target} fehlgeschlagen – {e}. Neuer Versuch in "
                      f"{int(MOVE_RETRY / 60)} min, die Dateien bleiben bis dahin auf der SSD.")
        return
    finally:
        _post_state["moving"] = None
        invalidate()
    r = jdapi.remove_packages([uuid])
    if not r["ok"]:
        log.warning("»%s«: verschoben, aber nicht aus der Liste entfernt: %s", name, r["error"])
    _target_drop(stage)
    for d in (_done_since, _move_failed, _archives_set):
        d.pop(uuid, None)
    notice("ok", f"»{name}« fertig in {target}"
                 + (f" ({moved} Datei{'en' if moved != 1 else ''} verschoben)" if moved else ""))
    log.info("»%s«: %d verschoben, %d gelöscht, %s aufgeräumt", name, moved, deleted, stage)


def _prune_targets(download_dirs):
    """Gemerkte Ziele vergessen, zu denen es kein Paket mehr gibt (z. B. von Hand entfernt)."""
    grabber = jdapi.grabber_packages()
    if not grabber["ok"]:
        return
    in_use = set(download_dirs) | {_norm(g.get("saveTo")) for g in jdapi.as_list(grabber)}
    now = time.time()
    with _targets_lock:
        stale = [k for k, v in _targets.items() if k not in in_use and now - v["t"] > PRUNE_AFTER]
        for k in stale:
            del _targets[k]
        if stale:
            _targets_save_locked()
    for k in stale:
        log.info("Zwischenablage: Ziel für %s vergessen (kein Paket mehr)", k)


def _post_run():
    res = {k: fn() for k, fn in (("packages", jdapi.query_packages), ("links", jdapi.query_links),
                                 ("queue", jdapi.extraction_queue))}
    if not res["packages"]["ok"] or not res["links"]["ok"]:
        return
    links_by_pkg = defaultdict(list)
    for l in jdapi.as_list(res["links"]):
        links_by_pkg[l.get("packageUUID")].append(l)
    queue = jdapi.as_list(res["queue"])
    packages = jdapi.as_list(res["packages"])
    staged_dirs = set()
    for p in packages:
        stage, uuid = _norm(p.get("saveTo")), p.get("uuid")
        if not _staged(stage):
            continue
        staged_dirs.add(stage)
        target = _target_of(stage)
        if not target:
            if not FALLBACK_DIR:
                _warn_once(("notarget", stage), "warn",
                           f"»{p.get('name')}« liegt in {stage}, ohne Ziel (FALLBACK_DIR leer) - bleibt dort.")
                continue
            target = _fallback_dir(os.path.basename(stage))   # z. B. direkt in JD hinzugefügt
            _target_put(stage, target)
        links = links_by_pkg.get(uuid, [])
        if not p.get("finished"):
            _set_archive_targets(p, target)
        state = _ready_state(links, queue)
        if state == "error":
            _warn_once(("exerr", uuid), "err",
                       f"»{p.get('name')}«: Entpacken fehlgeschlagen - bleibt in {stage} auf der SSD.")
            _done_since.pop(uuid, None)
            continue
        if state != "ready":
            _done_since.pop(uuid, None)
            continue
        now = time.monotonic()
        if now - _done_since.setdefault(uuid, now) < POST_SETTLE:
            continue    # erst beim nächsten Durchlauf: JD könnte gerade erst mit dem Entpacken anfangen
        if now - _move_failed.get(uuid, -MOVE_RETRY) < MOVE_RETRY:
            continue
        _move_package(p, links, stage, target)
    if time.time() - _post_state["last_prune"] > 3600:
        _post_state["last_prune"] = time.time()
        _prune_targets(staged_dirs)


def _post_loop():
    while True:
        _post_wake.wait(POST_INTERVAL)
        _post_wake.clear()
        try:
            with _post_lock:
                _post_run()
        except Exception:  # noqa: BLE001 - Job darf nicht sterben
            log.exception("Zwischenablage: Fehler im Hintergrund-Job")


def _staging_setup():
    """Prüft die Ordner und startet den Hintergrund-Job (einmal pro Prozess)."""
    global STAGING_DIR
    if not STAGING_DIR:
        return
    problem = None
    if not STAGING_DIR.startswith("/"):
        problem = "muss mit / beginnen"
    elif any(p and (_under(p, STAGING_DIR) or _under(STAGING_DIR, p))
             for p in [*CATEGORIES.values(), FALLBACK_DIR]):
        problem = "darf weder in einem Ziel-Ordner liegen noch eins enthalten"
    elif not os.path.isdir(STAGING_DIR) or not os.access(STAGING_DIR, os.W_OK):
        problem = "fehlt im Container oder ist nicht beschreibbar (Volume bei jd-webui?)"
    if problem:
        log.error("STAGING_DIR=%s %s - Zwischenablage AUS, Downloads gehen direkt ins Ziel.", STAGING_DIR, problem)
        STAGING_DIR = ""
        return
    for p in [*CATEGORIES.values(), FALLBACK_DIR]:
        if p and not os.path.isdir(p):
            log.warning("Zwischenablage: Ziel %s fehlt im Container - Volume bei jd-webui prüfen, "
                        "sonst klappt das Verschieben dorthin nicht.", p)
    _targets_load()
    log.info("Zwischenablage: Downloads nach %s/<Paket>, danach ins Ziel (Prüfung alle %gs, löscht %s)",
             STAGING_DIR, POST_INTERVAL, " ".join(STAGING_JUNK) or "nichts")
    if not _post_state["started"]:
        _post_state["started"] = True
        threading.Thread(target=_post_loop, name="zwischenablage", daemon=True).start()


_staging_setup()


# ── Sonstiges ─────────────────────────────────────────────────────────────────

LOGIN_PAGE = """<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Anmelden · JD WebUI</title>
  <link rel="stylesheet" href="static/style.css">
</head>
<body class="login-body">
  <form class="card login" method="post" action="login">
    <h1>JD WebUI</h1>
    {error}
    <label>Benutzer<input name="user" type="text" autocomplete="username" required autofocus value="{user}"></label>
    <label>Passwort<input name="password" type="password" autocomplete="current-password" required></label>
    <label class="check"><input name="remember" type="checkbox" checked> Angemeldet bleiben ({days} Tage)</label>
    <button class="btn primary" type="submit">Anmelden</button>
  </form>
</body>
</html>
"""


def _login_page(error="", user="", status=200):
    err = f'<p class="login-error">{html.escape(error)}</p>' if error else ""
    body = LOGIN_PAGE.format(error=err, user=html.escape(user, quote=True), days=SESSION_DAYS)
    return Response(body, status=status, mimetype="text/html")


@app.get("/login")
def login_form():
    if not WEBUI_USER or _session_ok():
        return redirect("./", code=303)
    return _login_page()


@app.post("/login")
def login_submit():
    user = request.form.get("user", "")
    if not _creds_ok(user, request.form.get("password", "")):
        log.warning("Fehlgeschlagene Anmeldung für %r von %s", user[:40], request.remote_addr)
        time.sleep(1.0)   # bremst Durchprobieren
        return _login_page("Benutzer oder Passwort falsch.", user, status=401)
    exp = int(time.time()) + SESSION_DAYS * 86400
    resp = redirect("./", code=303)
    resp.set_cookie(SESSION_COOKIE, _session_token(exp),
                    max_age=SESSION_DAYS * 86400 if request.form.get("remember") else None,
                    httponly=True, samesite="Lax", secure=request.is_secure, path="/")
    log.info("Anmeldung von %s", request.remote_addr)
    return resp


@app.post("/logout")
def logout():
    resp = redirect("login", code=303)
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@app.get("/healthz")
def healthz():
    return "ok"


# ── Erweiterung zum Herunterladen ─────────────────────────────────────────────
EXTENSION_FOLDER = "jd-webui-clicknload"
EXTENSION_README = """JD WebUI Click'n'Load - Browser-Erweiterung (Chrome, Brave, Edge)

1. Diesen Ordner an einen festen Ort legen, der nicht aufgeräumt wird
   (z. B. Dokumente\\Tools oder dein Nextcloud-Ordner). Der Browser lädt die
   Erweiterung bei jedem Start von dort - Ordner weg = Erweiterung weg.
2. chrome://extensions (Brave: brave://extensions, Edge: edge://extensions) öffnen,
   "Entwicklermodus" einschalten, "Entpackte Erweiterung laden", diesen Ordner wählen.
3. Erweiterung anheften und anklicken. Die Server-Adresse ist schon eingetragen:
   {server}
   "Verbindung testen" sollte "Verbunden" melden.
4. Zeigt das Popup "Website-Zugriff fehlt": auf "Zugriff erlauben" klicken
   (oder in den Details der Erweiterung "Websitezugriff: Auf allen Websites").

Neue Version: ZIP erneut aus der WebUI laden, den alten Ordner ersetzen und in
chrome://extensions bei der Erweiterung auf "Neu laden" klicken. Die Einstellungen bleiben.
"""


def _cnl_public_url():
    if CNL_PUBLIC_URL:
        return CNL_PUBLIC_URL
    host = urllib.parse.urlsplit("//" + (request.host or "")).hostname or "127.0.0.1"
    if ":" in host:
        host = f"[{host}]"   # IPv6
    return f"http://{host}:{CNL_PUBLIC_PORT}"


def _extension_files():
    for root, dirs, files in os.walk(EXTENSION_DIR):
        dirs[:] = sorted(d for d in dirs if not d.startswith((".", "_")))   # _metadata von Chrome
        for name in sorted(files):
            if name.startswith(".") or name == "config.json":
                continue
            path = os.path.join(root, name)
            yield path, os.path.relpath(path, EXTENSION_DIR).replace(os.sep, "/")


@app.get("/extension.zip")
def extension_zip():
    if not EXTENSION_DIR:
        return fail("Die Erweiterung ist in diesem Image nicht enthalten.", 404)
    server = _cnl_public_url()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, rel in _extension_files():
            z.write(path, f"{EXTENSION_FOLDER}/{rel}")
        z.writestr(f"{EXTENSION_FOLDER}/config.json", json.dumps({"server": server}, indent=2) + "\n")
        z.writestr(f"{EXTENSION_FOLDER}/LIESMICH.txt", EXTENSION_README.format(server=server).replace("\n", "\r\n"))
    log.info("Erweiterung heruntergeladen von %s (Server %s)", request.remote_addr, server)
    return Response(buf.getvalue(), mimetype="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{EXTENSION_FOLDER}.zip"',
        "Cache-Control": "no-store",
    })


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


if __name__ == "__main__":
    # Nur für lokale Tests - im Container läuft gunicorn (siehe Dockerfile)
    from werkzeug.serving import make_server

    host = os.environ.get("WEB_HOST", "127.0.0.1")
    cnl_server = make_server(host, CNL_PORT, app, threaded=True)
    threading.Thread(target=cnl_server.serve_forever, daemon=True).start()
    log.info("Click'n'Load lauscht auf http://%s:%d", host, CNL_PORT)
    app.run(host=host, port=int(os.environ.get("WEB_PORT", "8080")), threaded=True)
