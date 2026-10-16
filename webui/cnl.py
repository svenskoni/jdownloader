"""
Click'n'Load (CNL2) - Hilfsfunktionen für den Empfänger in app.py.

Download-Seiten schicken ihre Links per Formular an http://127.0.0.1:9666/flash/...,
also an einen JDownloader auf dem eigenen PC. Am PC wird dieser Port an den Server
weitergeleitet (siehe README). Die WebUI nimmt die Anfrage dort an, entschlüsselt
sie und gibt die Links über die lokale API an JDownloader weiter.

addcrypted2: "crypted" ist Base64, verschlüsselt mit AES-128-CBC. Schlüssel und IV
sind dieselben 16 Byte, die als Hex-String in "jk" stehen - verpackt in eine kleine
JavaScript-Funktion, z. B.  function f(){ return '31323334...'; }
"""

import base64
import binascii
import ipaddress
import re
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_RETURN_RE = re.compile(r"return\s*(.+?)\s*;?\s*\}", re.S)
_STRING_RE = re.compile(r"'([^']*)'|\"([^\"]*)\"")
_HEXKEY_RE = re.compile(r"[0-9a-fA-F]{32}")
_URL_RE = re.compile(r"(?:https?|ftp)://[^\s\"'<>]+", re.I)


class CnlError(ValueError):
    """Anfrage ließ sich nicht verarbeiten - Text landet im UI und in der Antwort."""


def key_from_jk(jk):
    """Liest den Hex-Schlüssel aus der jk-Funktion.

    Kein JavaScript-Interpreter: unterstützt wird der übliche Fall, dass die Funktion
    einen festen String zurückgibt (auch aus mehreren Teilen mit + zusammengesetzt).
    """
    m = _RETURN_RE.search(jk)
    expr = m.group(1) if m else jk
    parts = _STRING_RE.findall(expr)
    hexkey = "".join(a or b for a, b in parts) if parts else expr
    return hexkey.strip()


def parse_key(jk=None, k=None):
    if jk:
        hexkey = key_from_jk(jk)
    elif k:
        hexkey = k.strip()
    else:
        raise CnlError("kein Schlüssel übermittelt (jk/k fehlt)")
    if not _HEXKEY_RE.fullmatch(hexkey):
        raise CnlError("Schlüssel nicht lesbar (Seite nutzt ein unbekanntes jk-Format, "
                       "DLC-Download verwenden)")
    return bytes.fromhex(hexkey)


def _b64decode(text):
    # Leerzeichen statt "+" kommen vor, wenn eine Seite das Formular nicht sauber kodiert
    s = re.sub(r"\s", "", text.replace(" ", "+"))
    s += "=" * (-len(s) % 4)
    try:
        return base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError) as e:
        raise CnlError("Daten sind kein gültiges Base64") from e


def decrypt(crypted, key):
    """Entschlüsselt einen CNL2-Block und liefert den Klartext (Links, zeilenweise)."""
    if not crypted:
        raise CnlError("keine Daten übermittelt (crypted fehlt)")
    data = _b64decode(crypted)
    if not data or len(data) % 16:
        raise CnlError("Daten unvollständig (Länge passt nicht zu AES)")
    dec = Cipher(algorithms.AES(key), modes.CBC(key)).decryptor()
    plain = dec.update(data) + dec.finalize()
    text = plain.replace(b"\x00", b"").decode("utf-8", errors="replace").strip()
    if not _URL_RE.search(text):
        raise CnlError("keine Links gefunden (falscher Schlüssel?)")
    return text


def count_links(text):
    return len(_URL_RE.findall(text or ""))


def first_password(passwords):
    for line in (passwords or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def source_label(source, referer):
    for value in (source, referer):
        host = urlsplit(value or "").hostname
        if host:
            return host.removeprefix("www.")
    return "unbekannte Quelle"


def parse_networks(spec):
    """'10.200.0.10, 192.168.1.0/24' -> Liste von Netzen. Ungültige Einträge -> ValueError."""
    nets = []
    for part in re.split(r"[,\s]+", spec or ""):
        if part:
            nets.append(ipaddress.ip_network(part, strict=False))
    return nets


def ip_allowed(addr, nets):
    if not nets:
        return True
    try:
        ip = ipaddress.ip_address(addr or "")
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip in net for net in nets)
