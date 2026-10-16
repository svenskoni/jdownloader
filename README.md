# JDownloader-Stack mit eigener Web-Oberfläche

Portainer-Stack aus drei Teilen:

| Service | Aufgabe |
|---|---|
| `jdownloader` | `jlesage/jdownloader-2`, unverändert (GUI auf 5800 über deinen nginx-Proxy) |
| `jd-webui` | Schlanke Web-Oberfläche auf **http://&lt;server&gt;:8080** (plain HTTP, Login) und Click'n'Load-Empfänger auf **Port 9666** |
| `jd-init` | Läuft vor jedem JD-Start kurz durch und schaltet die lokale JD-API frei, danach beendet er sich |

**Was die Oberfläche kann:**
- URLs hinzufügen (eine oder mehrere, optional mit Paketname und Archiv-Passwort)
- DLC/CCF/RSDF hochladen (Datei wählen oder per Drag & Drop)
- ▶ Start, ⏸ Halt/Fortsetzen, ⏹ Stop für alles, dazu ▶/⏸ **pro Paket**
- Anzeige **„Aktuell“**, also was JD gerade macht, z. B. „Lädt 2 Dateien · Entpackt »Film«“, dazu der Gesamtfortschritt
- Pro Paket: Phase (Download / Entpacken / Entpacken wartet / Fertig / Fehler), Fortschritt in %, Größe, Geschwindigkeit, Restzeit und der Statustext von JD
- Pakete aus der Liste entfernen und „Fertige entfernen“ (die Dateien bleiben dabei immer erhalten)
- **Zwischenablage auf der SSD**: alles wird erst auf die SSD geladen, JD entpackt direkt ins Ziel auf der HDD, danach verschiebt die WebUI den Rest und räumt die SSD auf (siehe [Download-Ordner](#5-download-ordner-ssd--hdd))
- **Linkgrabber** mit **Kategorien** (Anime, Filme, Serien, Spiele): pro Paket Kategorie wählen und übernehmen – die fertigen Dateien landen im passenden Ordner (siehe [Kategorien](#6-kategorien))
- **Click'n'Load** von Download-Seiten annehmen (siehe [Click'n'Load](#clicknload))
- Anmeldeseite mit **„Angemeldet bleiben“** (Cookie, Standard 365 Tage)

**Ein Vorgang = ein Paket:** JD verteilt die Links einer DLC oder eines Click'n'Load gern auf viele Pakete (eins pro Datei). Die WebUI fasst alles aus einem Vorgang zu **einem** Paket zusammen, sobald JD fertig gesammelt hat. Es heißt wie die DLC-Datei (ohne `.dlc`) bzw. wie das Paket, das die Seite per Click'n'Load mitschickt. Ohne solchen Namen, oder wenn die DLC nur eine ID als Namen hat (z. B. `C3A52F7E1B.dlc`), nimmt sie den gemeinsamen Anfang der Dateinamen. Solange das läuft, steht in der Linkgrabber-Karte „wird zusammengefasst …“ (ein paar Sekunden).

Über das Formular hinzugefügte Links und Container übernimmt die WebUI danach automatisch in die Download-Liste, mit der im Formular gewählten Kategorie (ohne Kategorie nach `Fertig/<Name>`). Bei „Sofort starten“ wird der Download auch gleich gestartet. Was im Linkgrabber wartet (z. B. per Click'n'Load), zeigt die Karte **Linkgrabber**.

**Pro Paket:** ▶ startet genau dieses Paket sofort („Erzwungener Start“ in JD), auch wenn der Rest gestoppt ist. ⏸ hält es an: JD kennt kein Pausieren einzelner Pakete, das Paket wird deshalb **deaktiviert** (Anzeige „Angehalten“). Bereits geladene Teile bleiben liegen, ▶ setzt es fort, sofern der Hoster Fortsetzen unterstützt.

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
Die JD-Konfiguration unter `/opt/jdownloader/config` bleibt erhalten.

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
| `CNL_ALLOWED` | empfohlen: IP-Adresse(n) oder Netze, die Click'n'Load schicken dürfen, z. B. `10.200.0.10` oder `10.200.0.10, 192.168.1.0/24`. Leer = jede Adresse |
| `CNL_PORT` | optional, Port für Click'n'Load auf dem Server, Standard `9666` |
| `CNL_ACTION` | optional, was mit per Click'n'Load geschickten Paketen passiert: `grabber` = bleiben im Linkgrabber (Standard), `queue` = kommen in die Download-Liste, `start` = kommen in die Download-Liste und starten |
| `SESSION_DAYS` | optional, so viele Tage bleibt ein Browser nach „Angemeldet bleiben“ angemeldet, Standard `365` |
| `CATEGORIES` | optional, eigene Kategorien, siehe [Kategorien](#6-kategorien) |
| `CNL_PUBLIC_URL` | optional, Adresse des Click'n'Load-Ports, die in die heruntergeladene Erweiterung geschrieben wird, z. B. `http://192.168.1.10:9666`. Leer = Hostname der WebUI + `CNL_PORT` |
| `FALLBACK_DIR` | optional, wohin Pakete ohne Kategorie gehen, Standard `/fertig` (= `Fertig` auf der HDD) |
| `STAGING_DIR` | optional, Zwischenablage (SSD), Standard `/output`. Leer = wie früher direkt ins Ziel laden, siehe [Download-Ordner](#5-download-ordner-ssd--hdd) |
| `POST_INTERVAL` | optional, alle wie viele Sekunden die WebUI nach fertigen Paketen schaut, Standard `20` |
| `STAGING_JUNK` | optional, Endungen, die beim Verschieben gelöscht statt mitgenommen werden, Standard `.sfv,.par2,.md5,.sha1,.nfo,.url` |

Danach auf **Deploy the stack** klicken. Beim ersten Mal baut Portainer das WebUI-Image, das dauert etwa eine Minute.

### 4. Prüfen
1. *Containers → jd-init → Logs* sollte `Lokale JD-API freigeschaltet.` oder `… bereits freigeschaltet` zeigen. Dass `jd-init` danach als **exited** dasteht, ist normal.
2. Öffne `http://<server-ip>:8080` und melde dich mit `JD_USER`/`JD_PASS` an. Mit „Angemeldet bleiben“ fragt die WebUI in diesem Browser erst nach `SESSION_DAYS` Tagen wieder. Ändert sich `JD_PASS`, sind alle Anmeldungen ungültig.
3. JD braucht nach dem Start ca. 30–60 s. Bis dahin zeigt die WebUI „Keine Verbindung“.

Optional kannst du die API direkt testen (Konsole des `jd-webui`-Containers in Portainer):
```sh
wget -qO- http://jdownloader:3128/jd/version
```

### 5. Download-Ordner (SSD → HDD)
Damit die Daten-HDD möglichst selten anläuft, lädt JD alles zuerst auf eine SSD (Zwischenablage). Erst beim Entpacken bzw. Verschieben wird auf die HDD geschrieben.

| Auf dem Host | Im Container | Zweck |
|---|---|---|
| `/mnt/ssd/Download` | `/output` | **SSD**: Zwischenablage, je Paket ein Unterordner (`STAGING_DIR`), außerdem Standard-Ordner von JD |
| `/mnt/data_disk/Fertig` | `/fertig` | HDD: Pakete ohne Kategorie, je Paket ein Unterordner (`FALLBACK_DIR`) |
| `/mnt/data_disk/Medien` | `/medien` | HDD: Kategorien Anime, Filme, Serien (`/medien/Anime` …) |
| `/mnt/data_disk/Spiele` | `/spiele` | HDD: Kategorie Spiele |

`jdownloader` **und** `jd-webui` brauchen diese Volumes mit **denselben Pfaden im Container**: JD lädt und entpackt, die WebUI verschiebt und räumt auf.

**Ablauf pro Paket:**
1. Beim Übernehmen setzt die WebUI den Download-Ordner auf `/output/<Paket>` (SSD) und merkt sich das Ziel (Kategorie-Ordner oder `/fertig/<Paket>`) in `/output/.jd-webui-ziele.json`. Haben zwei Pakete denselben Namen, bekommt das zweite `/output/<Paket> (2)`.
2. Sobald das Paket in der Download-Liste ist, setzt sie für seine Archive „Entpacken nach“ = Ziel. JD entpackt also direkt auf die HDD und löscht die Archive danach von der SSD.
3. Ist alles geladen und entpackt, verschiebt sie die übrigen Dateien (z. B. eine einzelne `.mkv` ohne Archiv) ins Ziel, löscht Archivreste und `STAGING_JUNK` (`.sfv`, `.nfo` …), entfernt den Ordner auf der SSD und nimmt das Paket aus der Download-Liste (nur aus der Liste). Vorhandene Dateien im Ziel werden nie überschrieben, sondern bekommen ` (1)` angehängt.
4. In der Oberfläche steht dabei „Fertig · wartet aufs Entpacken“, „Fertig · wird gleich verschoben“ bzw. „Verschiebt ins Ziel“, danach eine Meldung „»Paket« fertig in …“.

Auch Pakete, die du direkt in der JD-GUI in einen Unterordner von `/output` lädst, verschiebt die WebUI, und zwar wie Pakete ohne Kategorie nach `/fertig/<Ordner>`. Was direkt in `/output` (ohne Unterordner) liegt, fasst sie nicht an.

Einmalig in der JD-GUI (Port 5800) einstellen:

| Einstellung | Wert | Warum |
|---|---|---|
| *Allgemein → Download-Ordner* | `/output` | Standard für alles, was nicht über die WebUI kommt |
| *Allgemein → Fertige Downloads aus der Liste entfernen* | **Nie** | Die WebUI muss fertige Pakete noch sehen, um sie zu verschieben. Sie räumt die Liste danach selbst auf. |
| *Archiv-Entpacker → Entpacken nach (eigener Ordner)* | **aus** | Ein fester Ordner kennt keine Kategorien. Das Ziel setzt die WebUI pro Archiv. |
| *Archiv-Entpacker → Unterordner erstellen* | **aus** | Den Ordner pro Paket legt schon die WebUI an, sonst entsteht `…/Name/Name/`. |
| *Archiv-Entpacker → Archive nach erfolgreichem Entpacken löschen* | **an**, endgültig löschen | Die Archive verschwinden von der SSD, der Platz wird frei. |
| *Archiv-Entpacker → Automatisch entpacken* | **an** | Ohne Entpacken bleibt ein Paket mit Archiven in der Zwischenablage liegen. |

Der JD-Container und die WebUI schreiben als UID 1000. `/mnt/ssd/Download`, `Fertig`, `Medien/*` und `Spiele` müssen für diesen Benutzer beschreibbar sein (`ls -ln /mnt/ssd /mnt/data_disk /mnt/data_disk/Medien` zeigt Besitzer und Rechte).

SSD und HDD müssen eingehängt sein, bevor der Stack startet. Fehlt eine, legt Docker die Ordner leer auf der Systemplatte an und JD lädt dorthin.

Ohne SSD: `STAGING_DIR` in Portainer leer setzen und `/output` wieder auf die HDD legen. Dann lädt JD wie früher direkt ins Ziel.

### 6. Kategorien
Im Linkgrabber hat jedes Paket Knöpfe für die Kategorien. Ein Klick wählt die Kategorie und öffnet darunter das Feld für den **Ordnernamen**, vorbelegt mit dem Paketnamen und markiert, sodass du direkt lostippen kannst. Enter oder Klick daneben speichert, „Übernehmen“ nimmt den eingetippten Namen auch ohne Enter mit. Ergebnis z. B. `/medien/Anime/<Name>` (= `/mnt/data_disk/Medien/Anime/<Name>` auf dem Host). Verschachtelt geht auch (`Frieren/Staffel 1`). Ein leeres Feld bedeutet den Paketnamen, lose Dateien direkt im Kategorie-Ordner gibt es nicht. Nochmal auf die aktive Kategorie klicken wählt sie wieder ab.

**Ohne Kategorie** einfach „Übernehmen“: Das Paket geht nach `/fertig/<Paket>` (`FALLBACK_DIR`), also alles aus einer DLC in einen Ordner. Von dort sortierst du es selbst. Das gilt auch für „Alle übernehmen“ (Pakete mit Kategorie behalten ihren Ordner) und für das Formular ohne Kategorie. Mit „danach starten“ startet der Download gleich.

Im Formular „Hinzufügen“ gibt es dieselben Kategorien als Auswahl, die gilt dann für alles, was gerade hinzugefügt wird.

Die Kategorien kommen aus der Variable `CATEGORIES` (Format `Name=/ordner/im/jdownloader-container`, mehrere mit `;` getrennt). Standard:
```
Anime=/medien/Anime;Filme=/medien/Filme;Serien=/medien/Serien;Spiele=/spiele
```
Neue Hauptordner brauchen zusätzlich ein Volume beim Service `jdownloader`. `FALLBACK_DIR` leer setzen, wenn Pakete ohne Kategorie im Standard-Ordner von JD (`/output`) bleiben sollen.

### Updates
Nach Änderungen im Repo: *Stack → Pull and redeploy*. Taucht die Änderung danach nicht auf, lösche unter *Images* das Image `jd-webui:local` und führe den Redeploy noch einmal aus.

---

## Click'n'Load

Download-Seiten schicken die Links beim Klick auf den Click'n'Load-Button an `http://127.0.0.1:9666`, also an einen JDownloader **auf dem eigenen PC**. Damit sie beim Server ankommen, braucht der Browser-PC eine Brücke dorthin. Die WebUI nimmt die Daten auf Port 9666 an, entschlüsselt die Links und übergibt sie an JDownloader. Standardmäßig landen sie im Linkgrabber von JDownloader. Die WebUI zeigt sie in der Karte **Linkgrabber**, dort wählst du die Kategorie und übernimmst sie. Mit `CNL_ACTION=queue` bzw. `start` übernimmt die WebUI sie stattdessen automatisch in die Download-Liste bzw. startet sie auch. Alles bleibt im LAN, MyJDownloader wird nicht gebraucht.

Auf dem Server ist dafür nur eines zu tun: In Portainer `CNL_ALLOWED` auf die IP(s) der PCs setzen, von denen Click'n'Load kommen darf, und den Stack neu deployen. Für die Brücke am PC gibt es zwei Wege.

### Variante A: Chrome-Erweiterung (empfohlen)
Funktioniert in Chrome, Edge und Brave, auf jedem Gerät und ohne Änderungen an Windows. Der Ordner `extension/` in diesem Repo ist die Erweiterung, die WebUI bietet sie auch zum Download an.

1. In der WebUI unter „Hinzufügen“ **Click'n'Load-Erweiterung → Erweiterung herunterladen (ZIP)**. Die ZIP enthält die Adresse des Click'n'Load-Ports schon (Hostname, unter dem du die WebUI geöffnet hast, plus `CNL_PORT`). Entpacken und den Ordner an einen **festen Ort** legen, z. B. in den Nextcloud-Ordner. Der Browser lädt die Erweiterung bei jedem Start von dort: Ordner gelöscht = Erweiterung weg. Dann einfach die ZIP neu laden.
2. `chrome://extensions` öffnen (Brave: `brave://extensions`, Edge: `edge://extensions`), **Entwicklermodus** einschalten, **Entpackte Erweiterung laden** und den Ordner auswählen.
3. Erweiterung an die Symbolleiste anheften, Symbol anklicken. Die Adresse steht schon drin (sonst eintragen, z. B. `http://192.168.1.10:9666`, und **Speichern**). **Verbindung testen**, erwartet: „Verbunden. WebUI und JDownloader erreichbar.“
4. Zeigt das Popup **„Website-Zugriff fehlt“** (am Symbol ein rotes „!“), auf **Zugriff erlauben** klicken. Ohne diesen Zugriff kommt nichts an. Brave und Chrome stellen ihn je nach Einstellung auf „Bei Klick“. Alternativ in den Details der Erweiterung bei „Websitezugriff“ **„Auf allen Websites“** wählen.
5. Auf einer Download-Seite den Click'n'Load-Button drücken. Es erscheint eine Benachrichtigung, und in der WebUI steht unter dem DLC-Feld „Click'n'Load zuletzt …“.

So funktioniert sie:
```
Seite ──► 127.0.0.1:9666 ──(von der Erweiterung umgeleitet)──► Antwort aus der Erweiterung („success“)
             └─ Formulardaten mitgelesen ──► http://<server>:9666 (jd-webui) ──API──► jdownloader:3128
```
Die Seite bekommt ihre Antwort direkt von der Erweiterung, die Daten schickt die Erweiterung im Hintergrund an den Server. Eine direkte Umleitung von HTTPS-Seiten wie filecrypt auf `http://<server>` würde Chrome als Mixed Content blockieren, so dagegen reicht reines HTTP im LAN, ein Zertifikat ist nicht nötig. Weil die Seite immer „success“ bekommt, steht das echte Ergebnis in der Benachrichtigung, im Popup der Erweiterung unter „Zuletzt“ und in der WebUI.

Berechtigungen: Die Erweiterung fragt nach Zugriff auf alle Websites. Ohne diesen Zugriff lässt Chrome sie Anfragen, die eine Download-Seite an `127.0.0.1:9666` schickt, weder umleiten noch mitlesen. Sie reagiert nur auf Anfragen an `127.0.0.1:9666` bzw. `localhost:9666` und schickt Daten nur an die eingetragene Adresse. Ist sie im Popup ausgeschaltet (Symbol zeigt „AUS“), verhält sich der Browser wie ohne Erweiterung.

### Variante B: Windows-Portweiterleitung (ohne Erweiterung)
Gilt für alle Browser auf diesem PC, muss aber auf jedem Windows-PC eingerichtet werden. In einer Eingabeaufforderung **als Administrator** (Server-IP = IP des Docker-Hosts, `hostname -I` auf dem Host):
```bat
netsh interface portproxy add v4tov4 listenaddress=127.0.0.1 listenport=9666 connectaddress=SERVER-IP connectport=9666
```
Die Weiterleitung bleibt nach einem Neustart erhalten und braucht den Windows-Dienst **IP-Hilfsdienst** (`iphlpsvc`), der normalerweise läuft. Test: `http://127.0.0.1:9666/jdcheck.js` im Browser öffnen, erwartet wird `jdownloader=true;`. Chrome/Edge fragen hier eventuell einmal pro Seite, ob sie auf „Apps und Dienste auf diesem Gerät“ zugreifen darf. Das muss erlaubt werden.

Anzeigen oder entfernen:
```bat
netsh interface portproxy show v4tov4
netsh interface portproxy delete v4tov4 listenaddress=127.0.0.1 listenport=9666
```

### Für beide Varianten
- Vom PC zum Docker-Host muss **TCP 9666** erlaubt sein (Firewall/OPNsense, ggf. Proxmox-Firewall an der VM).
- Ein JDownloader auf dem PC selbst oder eine andere Click'n'Load-Erweiterung kommen sich mit der Brücke in die Quere. Bei Variante A gewinnt die Erweiterung, solange sie aktiv ist. Bei Variante B belegt ein lokaler JDownloader den Port.
- Nicht beide Varianten gleichzeitig nötig. Ist die Erweiterung aktiv, wird die Portweiterleitung nicht benutzt.

### Grenzen
Die WebUI liest den Schlüssel aus der `jk`-Funktion der Seite ohne JavaScript-Interpreter. Das deckt den üblichen Fall ab (Funktion gibt einen festen Hex-String zurück). Nutzt eine Seite etwas Exotisches, erscheint „Schlüssel nicht lesbar“. Dann den DLC-Download der Seite verwenden.

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

**Paket bleibt auf der SSD liegen (Zwischenablage)**
- Log von `jd-webui` ansehen, die Zeilen beginnen mit „Zwischenablage“, „Entpacken:“ oder dem Paketnamen.
- „Entpacken fehlgeschlagen“: falsches/fehlendes Passwort oder defektes Archiv. Nach dem Entpacken in JD (Rechtsklick → Entpacken) verschiebt die WebUI das Paket von selbst.
- „wartet aufs Entpacken“ hält an: In JD ist *Automatisch entpacken* aus, oder JD hat das Archiv noch nicht erkannt.
- „Verschieben … fehlgeschlagen“: meist Rechte oder volle HDD. Neuer Versuch nach 10 Minuten.
- `STAGING_DIR … Zwischenablage AUS` beim Start: Das Volume `/output` fehlt bei `jd-webui` oder ist nicht beschreibbar.
- `JD kennt extraction/getArchiveInfo nicht`: Ältere JD-Version. Dann entpackt JD auf der SSD, und die WebUI verschiebt das Ergebnis danach ins Ziel (geht auch, braucht nur kurz mehr Platz auf der SSD).
- Ein Paket, das du selbst aus der Liste entfernst, verschiebt die WebUI nicht mehr. Seine Dateien liegen dann unter `/mnt/ssd/Download/<Paket>`.

**Andere USER_ID/GROUP_ID als 1000**
Passe bei `jd-init` die Zeile `user: "1000:1000"` an, sonst darf das Init-Skript die JD-Config nicht schreiben.

**Port 8080 schon belegt**
Setze in Portainer die Variable `WEBUI_PORT`, z. B. auf `8090`.

**Click'n'Load kommt nicht an**
1. Mit der Erweiterung: Symbol anklicken, **Verbindung testen**. Die Meldung sagt, woran es hängt (nicht erreichbar, IP abgelehnt, falscher Port, JDownloader aus). Unter „Zuletzt“ steht das Ergebnis der letzten Übergabe. Steht dort „Noch nichts weitergeleitet“, obwohl die Seite Erfolg meldet, fehlt fast immer der Website-Zugriff (rotes „!“ am Symbol, siehe Einrichtung Schritt 4).
2. Mit der Portweiterleitung: `http://127.0.0.1:9666/jdcheck.js` am PC aufrufen:
   - Seite lädt nicht: Weiterleitung (`netsh interface portproxy show v4tov4`) oder Firewall prüfen.
   - HTTP 403: Die WebUI sieht eine andere Absender-IP als in `CNL_ALLOWED`. Im Log von `jd-webui` steht „Click'n'Load von … abgelehnt“ mit der tatsächlichen IP.
   - HTTP 503 / `jdownloader=false`: WebUI läuft, aber JDownloader ist nicht erreichbar (siehe oben).
3. Ergebnis der letzten Anfrage steht in der WebUI unter dem DLC-Feld und im Log von `jd-webui`.

---

## Sicherheit
- Die WebUI läuft absichtlich per **plain HTTP** und ist nur fürs Intranet gedacht. Gib Port 8080 im Router **nicht** frei.
- Login über die Anmeldeseite mit denselben Zugangsdaten wie die JD-GUI. „Angemeldet bleiben“ setzt ein signiertes Cookie (HttpOnly, SameSite=Lax) ohne Passwort darin. Abmelden oben rechts löscht es in diesem Browser. Für Skripte geht weiterhin Basic Auth.
- Die JD-GUI (5800) hat ihren eigenen Login aus dem Image; `WEB_AUTHENTICATION_TOKEN_VALIDITY_TIME=8760` hält ihn ein Jahr. Nach einem Neustart des Containers fragt sie trotzdem einmal neu (Verhalten des Images).
- Die JD-API (Port 3128) hat keinen eigenen Login. Sie wird **nicht** nach außen veröffentlicht und ist nur aus den Docker-Netzen `jd-internal` und `proxy-network` erreichbar.
- Der Click'n'Load-Port (9666) hat **keinen Login**, weil Download-Seiten sich nicht anmelden können. Wer ihn erreicht, kann Links in deinen JDownloader schieben (sonst nichts, die Oberfläche und Steuerung gibt es dort nicht). Deshalb `CNL_ALLOWED` setzen und den Port in der Firewall nur für deinen PC öffnen.

---

## Entwicklung (lokal ohne Docker)
```sh
cd webui
pip install flask
python dev/mock_jd.py                     # Fake-JDownloader auf Port 3128 (simuliert Download/Entpacken)
JD_API_URL=http://127.0.0.1:3128 WEBUI_USER=admin WEBUI_PASS=test WEB_PORT=8091 python app.py
```
Danach im Browser `http://127.0.0.1:8091` öffnen. Mit `MOCK_SPEED_MB=40` simuliert der Mock schnellere Downloads. Mit `MOCK_FILES=1` legt er die Dateien wirklich an (fertige Links, nach dem Entpacken `<Archiv>.mkv` im Entpack-Ziel). Zusammen mit `STAGING_DIR=/output POST_INTERVAL=5` lässt sich so die Zwischenablage durchspielen, die Ordner `/output`, `/medien/…`, `/spiele` und `/fertig` müssen dafür lokal existieren und beschreibbar sein. Der Click'n'Load-Empfänger lauscht dabei auf `127.0.0.1:9666` (anderer Port über `CNL_PORT`).

## Dateien
```
docker-compose.yml      Portainer-Stack
webui/Dockerfile        python:3.12-alpine + gunicorn, läuft als UID 1000 (Build-Kontext: Repo)
webui/app.py            Backend (Flask): Login, Übersicht, Steuerung, Hinzufügen, Click'n'Load
webui/cnl.py            Click'n'Load: Schlüssel lesen, Links entschlüsseln, IP-Filter
webui/jdapi.py          Client für die lokale JD-API
webui/jd_init.py        Init-Skript: schaltet die JD-API in der Config frei
webui/mover.py          Verschiebt fertige Pakete von der SSD ins Ziel (auch über Dateisystemgrenzen)
webui/static/           Oberfläche (HTML/CSS/JS, keine externen Abhängigkeiten)
webui/dev/mock_jd.py    Fake-JDownloader für lokale Tests (nicht im Image)
extension/              Chrome-/Brave-/Edge-Erweiterung für Click'n'Load (im Image, Download über die WebUI)
```
