"""
Minimaler Client für die lokale JDownloader-API ("Deprecated API", Standard-Port 3128).

Die API spricht dieselben Namespaces wie My.JDownloader (downloadsV2, linkgrabberv2,
downloadcontroller, extraction ...), nur unverschlüsselt per HTTP-GET. Parameter werden
positional übergeben: ?name1=wert1&name2=wert2 - der Name ist egal, JD wertet die
Reihenfolge der Werte aus. Listen/Objekte werden als JSON-Wert übergeben.
"""

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request

JD_API_URL = os.environ.get("JD_API_URL", "http://jdownloader:3128").rstrip("/")
JD_TIMEOUT = float(os.environ.get("JD_TIMEOUT", "6"))
# Optional, nur falls die JD-API selbst per Basic Auth geschützt ist
JD_API_USER = os.environ.get("JD_API_USER", "")
JD_API_PASS = os.environ.get("JD_API_PASS", "")


def _get(url):
    """Führt einen GET aus und liefert {"ok": True, "data": ...} oder {"ok": False, "error": ...}."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    if JD_API_USER:
        creds = base64.b64encode(f"{JD_API_USER}:{JD_API_PASS}".encode()).decode()
        req.add_header("Authorization", f"Basic {creds}")
    try:
        with urllib.request.urlopen(req, timeout=JD_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"ok": False, "error": f"JDownloader meldet HTTP {e.code}: {body[:200]}"}
    except urllib.error.URLError as e:
        reason = str(e.reason).lower()
        if "timed out" in reason:
            return {"ok": False, "error": f"JDownloader antwortet nicht (Timeout {JD_TIMEOUT:g}s)"}
        return {"ok": False, "error": f"Keine Verbindung zu JDownloader ({JD_API_URL}): {e.reason}"}
    except TimeoutError:
        return {"ok": False, "error": f"JDownloader antwortet nicht (Timeout {JD_TIMEOUT:g}s)"}
    except Exception as e:  # noqa: BLE001 - Fehler soll im UI landen, nicht den Request killen
        return {"ok": False, "error": f"Fehler beim JD-Aufruf: {e}"}

    if not body.strip():
        return {"ok": True, "data": None}
    try:
        data = json.loads(body)
    except ValueError:
        return {"ok": True, "data": body.strip().strip('"')}
    if isinstance(data, dict) and "data" in data:
        return {"ok": True, "data": data["data"]}
    return {"ok": True, "data": data}


def _encode(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def call(path, *args):
    """Ruft eine JD-Methode auf. args werden in Reihenfolge als Parameter übergeben."""
    url = f"{JD_API_URL}{path}"
    if args:
        qs = "&".join(f"p{i}={urllib.parse.quote(_encode(v), safe='')}" for i, v in enumerate(args))
        url = f"{url}?{qs}"
    return _get(url)


def as_list(result):
    """Liefert result['data'] als Liste (leer bei Fehler oder unerwartetem Typ)."""
    if result.get("ok") and isinstance(result.get("data"), list):
        return result["data"]
    return []


# ── Download-Liste ────────────────────────────────────────────────────────────

PACKAGE_QUERY = {
    "bytesLoaded": True, "bytesTotal": True, "childCount": True, "enabled": True,
    "eta": True, "finished": True, "running": True, "speed": True, "status": True,
    "saveTo": True, "maxResults": -1, "startAt": 0,
}

LINK_QUERY = {
    "bytesLoaded": True, "bytesTotal": True, "enabled": True, "eta": True,
    "extractionStatus": True, "finished": True, "running": True, "skipped": True,
    "speed": True, "status": True, "host": True, "maxResults": -1, "startAt": 0,
}


def query_packages():
    return call("/downloadsV2/queryPackages", PACKAGE_QUERY)


def query_links():
    return call("/downloadsV2/queryLinks", LINK_QUERY)


def remove_packages(package_ids):
    return call("/downloadsV2/removeLinks", [], list(package_ids))


def cleanup_finished():
    return call("/downloadsV2/cleanup", [], [], "DELETE_FINISHED", "REMOVE_LINKS_ONLY", "ALL")


# ── Download-Controller ───────────────────────────────────────────────────────

def get_state():
    return call("/downloadcontroller/getCurrentState")


def get_speed():
    return call("/downloadcontroller/getSpeedInBps")


def start():
    return call("/downloadcontroller/start")


def stop():
    return call("/downloadcontroller/stop")


def pause(value):
    return call("/downloadcontroller/pause", bool(value))


# ── Entpacken ─────────────────────────────────────────────────────────────────

def extraction_queue():
    return call("/extraction/getQueue")


# ── Linkgrabber ───────────────────────────────────────────────────────────────

def add_links(links, package_name="", autostart=False, extract_password=""):
    return call("/linkgrabberv2/addLinks", {
        "links": links,
        "packageName": package_name,
        "autostart": bool(autostart),
        "extractPassword": extract_password,
        "downloadPassword": "",
        "deepDecrypt": False,
    })


def add_container(container_type, content_b64):
    return call("/linkgrabberv2/addContainer", container_type, content_b64)


def is_collecting():
    """True/False, oder None wenn JD die Methode nicht kennt."""
    r = call("/linkgrabberv2/isCollecting")
    if r["ok"] and isinstance(r["data"], bool):
        return r["data"]
    return None


def grabber_packages():
    r = call("/linkgrabberv2/queryPackages", {"childCount": True, "maxResults": -1, "startAt": 0})
    if r["ok"]:
        return r
    # Ältere JD-Versionen kennen nur den alten Namespace
    return call("/linkcollector/queryPackages", {"maxResults": -1, "startAt": 0})


def grabber_to_downloads(package_ids):
    r = call("/linkgrabberv2/moveToDownloadlist", [], list(package_ids))
    if r["ok"]:
        return r
    return call("/linkcollector/startDownloads", [], list(package_ids))
