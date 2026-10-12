#!/usr/bin/env python3
"""
Fake-JDownloader für lokale Tests der WebUI (nicht Teil des Images).

Simuliert die lokale JD-API auf Port 3128: Download-Fortschritt, Pause,
Entpack-Warteschlange und Linkgrabber (addLinks/addContainer).

    python dev/mock_jd.py            # lauscht auf 127.0.0.1:3128
"""

import base64
import itertools
import json
import os
import random
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("MOCK_PORT", "3128"))
MB = 1024 * 1024
DL_SPEED = int(os.environ.get("MOCK_SPEED_MB", "6")) * MB   # pro laufendem Link
PAUSE_SPEED = 10 * 1024
EXTRACT_RATE = 9.0         # Prozent pro Sekunde
MAX_PARALLEL = 2

_ids = itertools.count(1_700_000_000_000)
lock = threading.Lock()
state = {"controller": "STOPPED", "last": time.monotonic()}
packages = []          # Download-Liste
grabber = []           # Linkgrabber
collect_until = [0.0]  # isCollecting == True bis zu diesem Zeitpunkt
pending = []           # (fällig_ab, [pakete], job_id) - kommt nach dem "Sammeln" in den Linkgrabber
# Wie ältere JD-Versionen: kein Job-Filter bei queryLinks, keine Crawler-Jobs, kein movetoNewPackage
OLD_JD = os.environ.get("MOCK_OLD_JD") == "1"


def mk_link(name, size, loaded=0, finished=False, extraction=None):
    return {"uuid": next(_ids), "name": name, "size": size, "loaded": loaded,
            "finished": finished, "running": False, "extractionStatus": extraction,
            "host": "example.com"}


def mk_package(name, links, extract=None, archive=None):
    return {"uuid": next(_ids), "name": name, "links": links, "enabled": True,
            "extract": extract, "extractPct": 0.0, "archive": archive or name,
            "saveTo": "/output", "forced": False}


def seed():
    packages.append(mk_package("Ubuntu 24.04 ISO", [
        mk_link("ubuntu-24.04-desktop-amd64.iso", 900 * MB, loaded=270 * MB),
        mk_link("SHA256SUMS", 1024),
    ]))
    packages.append(mk_package("Serie.S01.German", [
        mk_link(f"Serie.S01.German.part{i}.rar", 200 * MB, loaded=200 * MB, finished=True)
        for i in range(1, 4)
    ], extract="queued", archive="Serie.S01.German"))
    packages.append(mk_package("Doku.2025.1080p", [
        mk_link("Doku.2025.1080p.zip", 150 * MB, loaded=150 * MB, finished=True,
                extraction="SUCCESSFUL"),
    ], extract="done"))


def is_archive(pkg):
    return any(l["name"].endswith((".rar", ".zip", ".7z")) for l in pkg["links"])


def tick():
    now = time.monotonic()
    dt = now - state["last"]
    state["last"] = now

    # Linkgrabber füllen, wenn "Sammeln" vorbei ist
    for item in list(pending):
        if now >= item[0]:
            for pkg in item[1]:
                for l in pkg["links"]:
                    l["job"] = item[2]
                grabber.append(pkg)
            pending.remove(item)

    # Downloads (erzwungene Pakete laufen auch bei gestopptem Controller, ohne Slot-Limit)
    speed = DL_SPEED if state["controller"] == "RUNNING" else PAUSE_SPEED
    active = state["controller"] in ("RUNNING", "PAUSE")
    slots = MAX_PARALLEL
    for pkg in packages:
        for l in pkg["links"]:
            l["running"] = False
            if l["finished"] or not pkg["enabled"]:
                continue
            if pkg.get("forced"):
                l["running"] = True
                l["loaded"] = min(l["size"], l["loaded"] + DL_SPEED * dt)
            elif active and slots > 0:
                slots -= 1
                l["running"] = True
                l["loaded"] = min(l["size"], l["loaded"] + speed * dt)
            if l["loaded"] >= l["size"]:
                l["finished"], l["running"] = True, False
        if pkg.get("forced") and all(l["finished"] for l in pkg["links"]):
            pkg["forced"] = False
        if pkg["extract"] is None and all(l["finished"] for l in pkg["links"]):
            pkg["extract"] = "queued" if is_archive(pkg) else "done"

    # Entpacken (immer nur ein Archiv gleichzeitig, unabhängig vom Controller)
    running = [p for p in packages if p["extract"] == "running"]
    if not running:
        queued = [p for p in packages if p["extract"] == "queued"]
        if queued:
            queued[0]["extract"] = "running"
            running = queued[:1]
    for pkg in running:
        for l in pkg["links"]:
            l["extractionStatus"] = "RUNNING"
        pkg["extractPct"] = min(100.0, pkg["extractPct"] + EXTRACT_RATE * dt)
        if pkg["extractPct"] >= 100:
            pkg["extract"] = "done"
            for l in pkg["links"]:
                l["extractionStatus"] = "SUCCESSFUL"

    if state["controller"] == "RUNNING" and not any(
            not l["finished"] for p in packages for l in p["links"] if p["enabled"]):
        state["controller"] = "IDLE"


def pkg_view(p):
    links = p["links"]
    loaded = sum(int(l["loaded"]) for l in links)
    total = sum(l["size"] for l in links)
    running = any(l["running"] for l in links)
    speed = sum(DL_SPEED if (state["controller"] == "RUNNING" or p.get("forced")) else PAUSE_SPEED
                for l in links if l["running"])
    finished = all(l["finished"] for l in links)
    status = ""
    if p["extract"] == "running":
        status = f"Extracting {p['extractPct']:.0f}%"
    elif p["extract"] == "queued":
        status = "Wait for extraction"
    elif p["extract"] == "done" and finished:
        status = "Extraction OK" if is_archive(p) else "Finished"
    return {
        "uuid": p["uuid"], "name": p["name"], "bytesLoaded": loaded, "bytesTotal": total,
        "childCount": len(links), "enabled": p["enabled"], "finished": finished,
        "running": running, "speed": speed,
        "eta": int((total - loaded) / speed) if speed else -1,
        "status": status, "saveTo": p.get("saveTo") or "/output",
    }


def link_view(p, l):
    v = {"uuid": l["uuid"], "packageUUID": p["uuid"], "name": l["name"],
         "bytesLoaded": int(l["loaded"]), "bytesTotal": l["size"], "enabled": True,
         "finished": l["finished"], "running": l["running"], "host": l["host"],
         "speed": DL_SPEED if l["running"] else 0, "skipped": False,
         "status": "Finished" if l["finished"] else ""}
    if l["extractionStatus"]:
        v["extractionStatus"] = l["extractionStatus"]
    return v


def parse_args(query):
    """Positionale Parameter wie bei JD: ?p0=..&p1=.. oder ?{json}."""
    args = []
    for part in query.split("&"):
        if not part:
            continue
        raw = part.split("=", 1)[1] if "=" in part else part
        raw = urllib.parse.unquote(raw)
        try:
            args.append(json.loads(raw))
        except ValueError:
            args.append(raw)
    return args


def handle(path, args):
    if path == "/jd/version":
        return 48000
    if path == "/downloadcontroller/getCurrentState":
        return state["controller"]
    if path == "/downloadcontroller/getSpeedInBps":
        return sum(pkg_view(p)["speed"] for p in packages)
    if path == "/downloadcontroller/start":
        state["controller"] = "RUNNING"
        return True
    if path == "/downloadcontroller/stop":
        state["controller"] = "STOPPED"
        return True
    if path == "/downloadcontroller/pause":
        if state["controller"] in ("RUNNING", "PAUSE"):
            state["controller"] = "PAUSE" if args and args[0] is True else "RUNNING"
        return True
    if path == "/downloadsV2/queryPackages":
        return [pkg_view(p) for p in packages]
    if path == "/downloadsV2/queryLinks":
        return [link_view(p, l) for p in packages for l in p["links"]]
    if path == "/downloadsV2/setEnabled":
        ids = set(args[2] if len(args) > 2 else [])
        for p in packages:
            if p["uuid"] in ids:
                p["enabled"] = bool(args[0])
                if not p["enabled"]:
                    p["forced"] = False
        return None
    if path in ("/downloadsV2/forceDownload", "/downloadcontroller/forceDownload"):
        ids = set(args[1] if len(args) > 1 else [])
        for p in packages:
            if p["uuid"] in ids:
                p["forced"], p["enabled"] = True, True
        return True
    if path == "/downloadsV2/removeLinks":
        ids = set(args[1] if len(args) > 1 else [])
        packages[:] = [p for p in packages if p["uuid"] not in ids]
        return None
    if path == "/downloadsV2/cleanup":
        packages[:] = [p for p in packages
                       if not (all(l["finished"] for l in p["links"]) and p["extract"] == "done")]
        return None
    if path == "/extraction/getQueue":
        return [{"archiveId": str(p["uuid"]), "archiveName": p["archive"],
                 "controllerStatus": "RUNNING" if p["extract"] == "running" else "QUEUED",
                 "controllerId": p["uuid"],
                 "states": {l["name"]: "QUEUED" for l in p["links"]}}
                for p in packages if p["extract"] in ("queued", "running")]
    if path == "/linkgrabberv2/isCollecting":
        return time.monotonic() < collect_until[0]
    if path == "/linkgrabberv2/queryPackages":
        return [{"uuid": p["uuid"], "name": p["name"], "childCount": len(p["links"]),
                 "bytesTotal": sum(l["size"] for l in p["links"]), "saveTo": p.get("saveTo") or "/output",
                 "onlineCount": len(p["links"]), "offlineCount": 0, "unknownCount": 0,
                 "hosts": ["example.com"]}
                for p in grabber]
    if path == "/linkgrabberv2/setDownloadDirectory":
        directory, ids = args[0], set(args[1] if len(args) > 1 else [])
        if not isinstance(directory, str) or not directory.startswith("/"):
            raise ValueError(f"Ordner ungültig: {directory!r}")
        for p in grabber:
            if p["uuid"] in ids:
                p["saveTo"] = directory
        return None
    if path == "/linkgrabberv2/removeLinks":
        ids = set(args[1] if len(args) > 1 else [])
        grabber[:] = [p for p in grabber if p["uuid"] not in ids]
        return None
    if path == "/linkgrabberv2/addLinks":
        q = args[0] if args and isinstance(args[0], dict) else {}
        urls = [u.strip() for u in str(q.get("links", "")).splitlines() if u.strip()]
        fname = lambda u: u.rstrip("/").rsplit("/", 1)[-1] or "datei"
        links = [mk_link(fname(u), random.randint(40, 300) * MB) for u in urls]
        # Wie JD: mit Paketname ein Paket, sonst eigenes Paket pro Datei(-name)
        if q.get("packageName"):
            pkgs = [mk_package(q["packageName"], links)]
        else:
            pkgs = [mk_package(l["name"].rsplit(".", 1)[0], [l]) for l in links]
        job = next(_ids)
        collect_until[0] = time.monotonic() + 2.5
        pending.append((time.monotonic() + 2.0, pkgs, job))
        return None if OLD_JD else {"id": job}
    if path == "/linkgrabberv2/addContainer":
        if len(args) < 2:
            raise ValueError("type und content erwartet")
        base64.b64decode(str(args[1]), validate=True)   # wirft bei ungültigem base64
        # JD verteilt den Inhalt einer DLC gern auf mehrere Pakete
        pkgs = [mk_package(f"Container.Film.Teil{i}", [mk_link(f"Container.Film.Teil{i}.part{j}.rar", 60 * MB)
                                                         for j in (1, 2)], archive=f"Container.Film.Teil{i}")
                for i in (1, 2)]
        pkgs.append(mk_package("Container.Film.Bonus", [mk_link("Container.Film.Bonus.mkv", 80 * MB)]))
        job = next(_ids)
        collect_until[0] = time.monotonic() + 3.5
        pending.append((time.monotonic() + 3.0, pkgs, job))
        return None if OLD_JD else {"id": job}
    if path == "/linkgrabberv2/queryLinks":
        q = args[0] if args and isinstance(args[0], dict) else {}
        jobs = None if OLD_JD else (set(q["jobUUIDs"]) if q.get("jobUUIDs") else None)
        pk = set(q["packageUUIDs"]) if q.get("packageUUIDs") else None
        return [{"uuid": l["uuid"], "name": l["name"], "packageUUID": p["uuid"], "bytesTotal": l["size"],
                 "host": l["host"]}
                for p in grabber for l in p["links"]
                if (jobs is None or l.get("job") in jobs) and (pk is None or p["uuid"] in pk)]
    if path == "/linkgrabberv2/queryLinkCrawlerJobs":
        if OLD_JD:
            raise KeyError(path)
        q = args[0] if args and isinstance(args[0], dict) else {}
        ids = set(q.get("jobIds") or [])
        return [{"jobId": j, "crawling": True, "checking": False, "crawled": 0}
                for (_, _, j) in pending if j in ids]
    if path == "/linkgrabberv2/renamePackage":
        for p in grabber:
            if p["uuid"] == args[0]:
                p["name"] = args[1]
        return None
    if path == "/linkgrabberv2/movetoNewPackage":
        if OLD_JD:
            raise KeyError(path)
        link_ids, pkg_ids = set(args[0] or []), set(args[1] or [])
        name, directory = args[2], args[3] if len(args) > 3 else ""
        if not isinstance(name, str) or not name:
            raise ValueError(f"Name ungültig: {name!r}")
        moved = []
        for p in grabber:
            take = [l for l in p["links"] if l["uuid"] in link_ids or p["uuid"] in pkg_ids]
            p["links"] = [l for l in p["links"] if l not in take]
            moved += take
        grabber[:] = [p for p in grabber if p["links"]]
        new = mk_package(name, moved, archive=name)
        if directory:
            new["saveTo"] = directory
        grabber.append(new)
        return None
    if path == "/linkgrabberv2/moveToDownloadlist":
        ids = set(args[1] if len(args) > 1 else [])
        for p in [p for p in grabber if p["uuid"] in ids]:
            grabber.remove(p)
            packages.append(p)
        return None
    raise KeyError(path)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        with lock:
            tick()
            try:
                data = handle(parsed.path, parse_args(parsed.query))
                code, body = 200, {"data": data}
            except KeyError:
                code, body = 404, {"type": "API_COMMAND_NOT_FOUND", "src": "DEVICE"}
            except Exception as e:  # noqa: BLE001
                code, body = 500, {"type": "INTERNAL_SERVER_ERROR", "data": str(e)}
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        noisy = ("query", "getCurrentState", "getSpeedInBps", "getQueue", "isCollecting")
        if not any(n in self.path for n in noisy):
            print("[mock-jd]", self.path[:120], flush=True)


if __name__ == "__main__":
    seed()
    print(f"[mock-jd] lauscht auf http://127.0.0.1:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
