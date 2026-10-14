"""
Dateien eines fertigen Pakets aus der Zwischenablage (SSD) ins Ziel (HDD) verschieben.

Zwischen zwei Dateisystemen ist "verschieben" kopieren + löschen. Kopiert wird unter
einem Hilfsnamen (*.jdtmp) und erst am Ende umbenannt, damit z. B. Jellyfin keine halb
kopierten Dateien sieht. Vorhandene Dateien im Ziel werden nie überschrieben.
"""

import errno
import os
import shutil

TMP_SUFFIX = ".jdtmp"


def free_name(path):
    """path, oder mit " (1)", " (2)" ... davor, falls es schon existiert."""
    if not os.path.lexists(path):
        return path
    root, ext = os.path.splitext(path)
    i = 1
    while os.path.lexists(f"{root} ({i}){ext}"):
        i += 1
    return f"{root} ({i}){ext}"


def move_file(src, dst):
    """Verschiebt eine Datei, auch über Dateisystemgrenzen. Liefert den Zielpfad."""
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    dst = free_name(dst)
    try:
        os.rename(src, dst)
        return dst
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
    tmp = dst + TMP_SUFFIX
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.unlink(src)
    return dst


def move_tree(src_dir, dst_dir, drop_names=(), junk_ext=()):
    """Verschiebt alles aus src_dir nach dst_dir (Unterordner bleiben erhalten).

    drop_names: Dateinamen, die gelöscht statt verschoben werden (z. B. Archivteile,
    die schon entpackt sind). junk_ext: Endungen, die ebenfalls gelöscht werden
    (z. B. .sfv). Danach werden leere Ordner in src_dir samt src_dir entfernt.
    Liefert (verschoben, gelöscht)."""
    if not os.path.isdir(src_dir):
        return 0, 0
    drop = set(drop_names)
    junk = tuple(e.lower() for e in junk_ext if e)
    moved = deleted = 0
    for root, _dirs, files in os.walk(src_dir):
        for name in files:
            path = os.path.join(root, name)
            low = name.lower()
            if name in drop or low.endswith(TMP_SUFFIX) or (junk and low.endswith(junk)):
                os.unlink(path)
                deleted += 1
                continue
            move_file(path, os.path.join(dst_dir, os.path.relpath(path, src_dir)))
            moved += 1
    for root, _dirs, _files in os.walk(src_dir, topdown=False):
        try:
            os.rmdir(root)
        except OSError:
            pass
    return moved, deleted
