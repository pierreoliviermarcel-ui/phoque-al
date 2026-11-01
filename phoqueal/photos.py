"""Photos : fichiers en vrac et archives zip / rar (cbz / cbr).

Les photos sont rangees dans la meme table que les videos, avec kind='photo' :
- photo en vrac      : rel_path = chemin du fichier
- photo d'archive    : rel_path = "archive.zip::dossier/photo.jpg", archive = "archive.zip"
Une archive = un album ; les photos en vrac sont regroupees par dossier.
"""
import io
import os
import shutil
import zipfile

from .config import APP_DIR, DATA_DIR

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
ARCHIVE_EXT = {".zip", ".cbz", ".rar", ".cbr"}
SEP = "::"
CACHE_DIR = DATA_DIR / "photocache"
CACHE_MAX = 400          # fichiers extraits gardes au plus
MIN_SIZE = 8 * 1024      # ignore les icones / vignettes minuscules

_rar_ready = None


def setup_rar():
    """Configure la lecture des .rar. Retourne le nom de l'outil trouve, ou None."""
    global _rar_ready
    if _rar_ready is not None:
        return _rar_ready or None
    try:
        import rarfile
    except ImportError:
        _rar_ready = ""
        return None
    pf = [os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", "")]
    found = ""

    def first(*cands):
        for c in cands:
            if c and os.path.isfile(c):
                return c
        return None

    unrar = first(str(APP_DIR / "tools" / "UnRAR.exe"), *(os.path.join(p, "WinRAR", "UnRAR.exe") for p in pf if p),
                  shutil.which("unrar") or "")
    if unrar:
        rarfile.UNRAR_TOOL = unrar
        found = found or "UnRAR"
    sevenzip = first(str(APP_DIR / "tools" / "7z.exe"), *(os.path.join(p, "7-Zip", "7z.exe") for p in pf if p),
                     shutil.which("7z") or "")
    if sevenzip:
        rarfile.SEVENZIP_TOOL = sevenzip
        found = found or "7-Zip"
    tar = first(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "tar.exe"),
                shutil.which("bsdtar") or "")
    if tar:
        rarfile.BSDTAR_TOOL = tar
        found = found or "tar de Windows"
    try:
        rarfile.tool_setup(force=True)
    except Exception:
        found = ""
    _rar_ready = found
    return found or None


def is_rar(path):
    return os.path.splitext(path)[1].lower() in (".rar", ".cbr")


def open_archive(path):
    if is_rar(path):
        import rarfile
        setup_rar()
        return rarfile.RarFile(path)
    return zipfile.ZipFile(path)


def list_images(path):
    """[(nom_interne, taille)] des images d'une archive (tri naturel)."""
    out = []
    with open_archive(path) as arc:
        for info in arc.infolist():
            name = info.filename
            if name.endswith("/") or (hasattr(info, "is_dir") and info.is_dir()):
                continue
            base = os.path.basename(name)
            if base.startswith((".", "__MACOSX")) or "__MACOSX" in name:
                continue
            if os.path.splitext(name)[1].lower() in IMAGE_EXT and info.file_size >= MIN_SIZE:
                out.append((name, info.file_size))
    return out


def read_member(archive_path, member):
    with open_archive(archive_path) as arc:
        return arc.read(member)


def split_rel(rel_path):
    archive, _, member = rel_path.partition(SEP)
    return archive, member


def extract(video_id, archive_path, member):
    """Extrait une photo d'archive dans le cache et renvoie son chemin."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    ext = os.path.splitext(member)[1].lower() or ".jpg"
    out = CACHE_DIR / ("%d%s" % (video_id, ext))
    if out.is_file():
        try:
            os.utime(out, None)
        except OSError:
            pass
        return str(out)
    data = read_member(archive_path, member)
    tmp = out.with_suffix(ext + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, out)
    _trim_cache()
    return str(out)


def _trim_cache():
    try:
        files = sorted(CACHE_DIR.iterdir(), key=lambda p: p.stat().st_mtime)
        for f in files[:-CACHE_MAX]:
            f.unlink()
    except OSError:
        pass


def make_thumb(out_path, width, file_path=None, data=None):
    """Miniature d'une photo (Pillow). True si reussi."""
    try:
        from PIL import Image, ImageOps
        img = Image.open(io.BytesIO(data) if data is not None else file_path)
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.thumbnail((width, width * 2))
        img.save(str(out_path), "JPEG", quality=82)
        return True
    except Exception:
        return False
