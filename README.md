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
- **Linkgrabber** mit **Kategorien** (Anime, Filme, Serien, Spiele): pro Paket Kategorie wählen und übernehmen – die Dateien landen direkt im passenden Ordner (siehe [Kategorien](#6-kategorien))
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
| `FALLBACK_DIR` | optional, wohin Pakete ohne Kategorie gehen, Standard `/fertig` (= `Fertig` auf der HDD) |

Danach auf **Deploy the stack** klicken. Beim ersten Mal baut Portainer das WebUI-Image, das dauert etwa eine Minute.

### 4. Prüfen
1. *Containers → jd-init → Logs* sollte `Lokale JD-API freigeschaltet.` oder `… bereits freigeschaltet` zeigen. Dass `jd-init` danach als **exited** dasteht, ist normal.
2. Öffne `http://<server-ip>:8080` und melde dich mit `JD_USER`/`JD_PASS` an. Mit „Angemeldet bleiben“ fragt die WebUI in diesem Browser erst nach `SESSION_DAYS` Tagen wieder. Ändert sich `JD_PASS`, sind alle Anmeldungen ungültig.
3. JD braucht nach dem Start ca. 30–60 s. Bis dahin zeigt die WebUI „Keine Verbindung“.

Optional kannst du die API direkt testen (Konsole des `jd-webui`-Containers in Portainer):
```sh
wget -qO- http://jdownloader:3128/jd/version
```

### 5. Download-Ordner (HDD)
Die Downloads liegen auf der Daten-HDD des Docker-Hosts, die dort unter `/mnt/data_disk` eingehängt ist:

| Auf dem Host | Im Container | Zweck |
|---|---|---|
| `/mnt/data_disk/Download` | `/output` | Standard-Ordner von JD, nur für Pakete, die nicht über die WebUI übernommen werden |
| `/mnt/data_disk/Fertig` | `/fertig` | Pakete ohne Kategorie, je Paket ein Unterordner (`FALLBACK_DIR`) |
| `/mnt/data_disk/Medien` | `/medien` | Kategorien Anime, Filme, Serien (`/medien/Anime` …) |
| `/mnt/data_disk/Spiele` | `/spiele` | Kategorie Spiele |

Einmalig in der JD-GUI (Port 5800) prüfen:
- *Einstellungen → Allgemein*: Download-Ordner `/output`.
- *Einstellungen → Archiv-Entpacker*: **„Entpacken nach“ ausschalten**, also im Ordner des Archivs entpacken. Sonst landet alles Entpackte in einem festen Ordner statt in der Kategorie. Pakete ohne Kategorie landen trotzdem in `Fertig`, weil die WebUI sie dorthin lädt. Dazu „Archive nach erfolgreichem Entpacken löschen“ einschalten, dann bleiben in den Medien-Ordnern nur die fertigen Dateien.

Der JD-Container schreibt als UID 1000. Die Ordner `Medien/*` und `Spiele` müssen für diesen Benutzer beschreibbar sein (`ls -ln /mnt/data_disk /mnt/data_disk/Medien` zeigt Besitzer und Rechte).

Die HDD muss eingehängt sein, bevor der Stack startet. Fehlt sie, legt Docker die Ordner leer auf der Systemplatte an und JD lädt dorthin.

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
Funktioniert in Chrome, Edge und Brave, auf jedem Gerät und ohne Änderungen an Windows. Der Ordner `extension/` in diesem Repo ist die Erweiterung.

1. Repo auf den PC holen (GitHub: *Code → Download ZIP*, entpacken). Den Ordner danach **nicht löschen oder verschieben**, der Browser lädt die Erweiterung von dort.
2. `chrome://extensions` öffnen (Edge: `edge://extensions`), **Entwicklermodus** einschalten, **Entpackte Erweiterung laden** und den Ordner `extension` auswählen.
3. Erweiterung an die Symbolleiste anheften, Symbol anklicken, Adresse des Click'n'Load-Ports eintragen, z. B. `http://192.168.1.10:9666`, und **Speichern**. Erwartet: „Verbunden. WebUI und JDownloader erreichbar.“
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
Danach im Browser `http://127.0.0.1:8091` öffnen. Mit `MOCK_SPEED_MB=40` simuliert der Mock schnellere Downloads. Der Click'n'Load-Empfänger lauscht dabei auf `127.0.0.1:9666` (anderer Port über `CNL_PORT`).

## Dateien
```
docker-compose.yml      Portainer-Stack
webui/Dockerfile        python:3.12-alpine + gunicorn, läuft als UID 1000
webui/app.py            Backend (Flask): Login, Übersicht, Steuerung, Hinzufügen, Click'n'Load
webui/cnl.py            Click'n'Load: Schlüssel lesen, Links entschlüsseln, IP-Filter
webui/jdapi.py          Client für die lokale JD-API
webui/jd_init.py        Init-Skript: schaltet die JD-API in der Config frei
webui/static/           Oberfläche (HTML/CSS/JS, keine externen Abhängigkeiten)
webui/dev/mock_jd.py    Fake-JDownloader für lokale Tests (nicht im Image)
extension/              Chrome-/Edge-Erweiterung für Click'n'Load (wird nicht ins Image gebaut)
```
