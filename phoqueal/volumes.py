"""Identification des disques par numero de serie (la lettre peut changer)."""
import os
import string
import sys


def volume_info(root):
    """Retourne (numero_de_serie, nom_du_volume) pour une racine type 'E:\\'."""
    if sys.platform != "win32":
        return ("LOCAL", "local")
    import ctypes
    name = ctypes.create_unicode_buffer(261)
    serial = ctypes.c_ulong(0)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(
        ctypes.c_wchar_p(root), name, 261, ctypes.byref(serial), None, None, None, 0)
    if not ok:
        return (None, None)
    return ("%08X" % serial.value, name.value)


def connected_drives():
    """Dictionnaire numero_de_serie -> racine actuelle ('E:\\')."""
    if sys.platform != "win32":
        return {"LOCAL": "/"}
    import ctypes
    result = {}
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    for i, letter in enumerate(string.ascii_uppercase):
        if mask & (1 << i):
            root = letter + ":\\"
            serial, _ = volume_info(root)
            if serial:
                result[serial] = root
    return result


def split_path(path):
    """Decoupe un dossier en (racine, chemin_relatif)."""
    path = os.path.abspath(path)
    if sys.platform != "win32":
        return "/", path.lstrip("/")
    drive, rest = os.path.splitdrive(path)
    return drive + "\\", rest.lstrip("\\/")


def resolve(serial, rel_root, drives=None):
    """Chemin actuel d'une source, ou None si le disque est absent."""
    drives = connected_drives() if drives is None else drives
    root = drives.get(serial)
    if not root:
        return None
    full = os.path.join(root, rel_root)
    return full if os.path.isdir(full) else None
