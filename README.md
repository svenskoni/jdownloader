# JDownloader-Stack mit eigener Web-Oberfläche

Portainer-Stack aus drei Teilen:

| Service | Aufgabe |
|---|---|
| `jdownloader` | `jlesage/jdownloader-2`, unverändert (GUI auf 5800 über deinen nginx-Proxy) |
| `jd-webui` | Schlanke Web-Oberfläche auf **http://&lt;server&gt;:8080** (plain HTTP, Login) |
| `jd-init` | Läuft vor jedem JD-Start kurz durch und schaltet die lokale JD-API frei, danach beendet er sich |

**Was die Oberfläche kann:**
- URLs hinzufügen (eine oder mehrere, optional mit Paketname und Archiv-Passwort)
- DLC/CCF/RSDF hochladen (Datei wählen oder per Drag & Drop)
- ▶ Start, ⏸ Halt/Fortsetzen, ⏹ Stop
- Anzeige **„Aktuell“**, also was JD gerade macht, z. B. „Lädt 2 Dateien · Entpackt »Film«“, dazu der Gesamtfortschritt
- Pro Paket: Phase (Download / Entpacken / Entpacken wartet / Fertig / Fehler), Fortschritt in %, Größe, Geschwindigkeit, Restzeit und der Statustext von JD
- Pakete aus der Liste entfernen und „Fertige entfernen“ (die Dateien bleiben dabei immer erhalten)

Neue Links und Container landen zuerst im Linkgrabber von JDownloader. Die WebUI übernimmt sie automatisch in die Download-Liste, sobald JD sie fertig geprüft hat. Bei „Sofort starten“ wird der Download auch gleich gestartet. Bleibt doch einmal etwas im Linkgrabber hängen, erscheint ein Hinweis mit dem Button **„Alle übernehmen“**.

---

## Einrichtung

### 1. Auf GitHub hochladen
Lade den **Inhalt** dieses Ordners in ein (gerne privates) GitHub-Repository hoch. `docker-compose.yml` muss dabei im Hauptverzeichnis des Repos liegen:

```
docker-compose.yml
README.md
webui/...
```

### 2. Alten JDownloader-Stack entfernen
Der neue Stack verwendet wieder den Containernamen `jdownloader`. Stoppe und entferne deshalb in Portainer den bisherigen Stack bzw. Container.
Deine Daten unter `/opt/jdownloader/config` und `/opt/jdownloader/downloads` bleiben erhalten.

### 3. Stack in Portainer anlegen
*Stacks → Add stack*
- **Name:** z. B. `jdownloader`
- **Build method:** *Repository*
- **Repository URL:** `https://github.com/<dein-user>/<repo>`
- **Repository reference:** `refs/heads/main`
- **Compose path:** `docker-compose.yml`
- **Authentication** (nur bei privatem Repo): dein GitHub-Benutzername und ein *Personal Access Token* mit Leserecht auf das Repo
- **Environment variables:**

| Variable | Bedeutung |
|---|---|
| `JD_USER` | Login für die JD-GUI (5800) **und** die neue WebUI |
| `JD_PASS` | Passwort dazu |
| `WEBUI_PORT` | optional, Standard `8080` |

Danach auf **Deploy the stack** klicken. Beim ersten Mal baut Portainer das WebUI-Image, das dauert etwa eine Minute.

### 4. Prüfen
1. *Containers → jd-init → Logs* sollte `Lokale JD-API freigeschaltet.` oder `… bereits freigeschaltet` zeigen. Dass `jd-init` danach als **exited** dasteht, ist normal.
2. Öffne `http://<server-ip>:8080` und melde dich mit `JD_USER`/`JD_PASS` an.
3. JD braucht nach dem Start ca. 30–60 s. Bis dahin zeigt die WebUI „Keine Verbindung“.

Optional kannst du die API direkt testen (Konsole des `jd-webui`-Containers in Portainer):
```sh
wget -qO- http://jdownloader:3128/jd/version
```

### Updates
Nach Änderungen im Repo: *Stack → Pull and redeploy*. Taucht die Änderung danach nicht auf, lösche unter *Images* das Image `jd-webui:local` und führe den Redeploy noch einmal aus.

---

## Fehlerbehebung

**WebUI zeigt dauerhaft „Keine Verbindung zu JDownloader“**
1. Schau in die Logs von `jd-init`.
2. Falls nötig, schalte die API manuell in der JD-GUI (Port 5800) frei:
   - *Einstellungen → Erweiterte Einstellungen*, Filter `deprecated`
   - `RemoteAPI: Deprecated Api Enabled` → **an**
   - `RemoteAPI: Deprecated Api Localhost Only` → **aus**
   - `RemoteAPI: Deprecated Api Port` → **3128**
3. Danach JD neu starten (Container `jdownloader` neu starten).

**Andere USER_ID/GROUP_ID als 1000**
Passe bei `jd-init` die Zeile `user: "1000:1000"` an, sonst darf das Init-Skript die JD-Config nicht schreiben.

**Port 8080 schon belegt**
Setze in Portainer die Variable `WEBUI_PORT`, z. B. auf `8090`.

---

## Sicherheit
- Die WebUI läuft absichtlich per **plain HTTP** und ist nur fürs Intranet gedacht. Gib Port 8080 im Router **nicht** frei.
- Login per Basic Auth mit denselben Zugangsdaten wie die JD-GUI.
- Die JD-API (Port 3128) hat keinen eigenen Login. Sie wird **nicht** nach außen veröffentlicht und ist nur aus den Docker-Netzen `jd-internal` und `proxy-network` erreichbar.

---

## Entwicklung (lokal ohne Docker)
```sh
cd webui
pip install flask
python dev/mock_jd.py                     # Fake-JDownloader auf Port 3128 (simuliert Download/Entpacken)
JD_API_URL=http://127.0.0.1:3128 WEBUI_USER=admin WEBUI_PASS=test WEB_PORT=8091 python app.py
```
Danach im Browser `http://127.0.0.1:8091` öffnen. Mit `MOCK_SPEED_MB=40` simuliert der Mock schnellere Downloads.

## Dateien
```
docker-compose.yml      Portainer-Stack
webui/Dockerfile        python:3.12-alpine + gunicorn, läuft als UID 1000
webui/app.py            Backend (Flask): Login, Übersicht, Steuerung, Hinzufügen
webui/jdapi.py          Client für die lokale JD-API
webui/jd_init.py        Init-Skript: schaltet die JD-API in der Config frei
webui/static/           Oberfläche (HTML/CSS/JS, keine externen Abhängigkeiten)
webui/dev/mock_jd.py    Fake-JDownloader für lokale Tests (nicht im Image)
```
