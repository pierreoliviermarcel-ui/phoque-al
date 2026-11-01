"""Amélioration IA de l'image : agrandit et nettoie une vidéo de mauvaise qualité (flou, bruit, blocs).

- Moteur : Real-ESRGAN (version ncnn-vulkan), qui calcule sur la carte graphique (NVIDIA, AMD ou Intel).
- Modèles : « Rapide » (upscayl-lite, conseillé pour les vidéos réelles) ou « Qualité max » (Real-ESRGAN x4plus,
  4 à 8 fois plus lent).
- Le résultat est une NOUVELLE vidéo à côté de l'originale : « nom [HD].mp4 » (l'originale n'est jamais touchée).
- Le calcul se fait par morceaux de 10 s : on peut fermer Phoque Al, il reprend où il en était.
- « Tester sur 30 s » fabrique d'abord un court extrait pour juger avant de lancer la vidéo entière.
"""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile

from .. import db, volumes
from ..config import DATA_DIR, find_tool
from .common import log, safe

_FLAGS = 0x08000000 if sys.platform == "win32" else 0
TOOL_DIR = DATA_DIR / "models" / "realesrgan"
EXE = TOOL_DIR / "realesrgan-ncnn-vulkan.exe"
ZIP_URL = "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-windows.zip"
LITE_URL = "https://raw.githubusercontent.com/upscayl/upscayl/main/resources/models/upscayl-lite-4x."
WORK = DATA_DIR / "amelioration_en_cours"
SEG = 10.0                      # secondes par morceau
MODELS = [("upscayl-lite-4x", "Rapide — vidéos réelles (conseillé)"),
          ("realesrgan-x4plus", "Qualité max — vidéos réelles (lent)"),
          ("realesr-animevideov3-x4", "Dessins animés / hentai")]
TARGETS = [(1080, "1080p (Full HD)"), (1440, "1440p"), (2160, "2160p (4K)"), (0, "Même taille (image nettoyée)")]
SCHEMA = """
CREATE TABLE IF NOT EXISTS enhance(id INTEGER PRIMARY KEY, video_id INTEGER, path TEXT NOT NULL, model TEXT,
    target INTEGER, test INTEGER DEFAULT 0, status TEXT DEFAULT 'attente', progress REAL DEFAULT 0,
    out TEXT, error TEXT, added_at REAL, done_at REAL);
"""
WORKER = None
GPU = {"name": None}


# ----------------------------------------------------------------------------------------
# Outils
# ----------------------------------------------------------------------------------------
def tool_ready(model=None):
    if not EXE.is_file():
        return False
    names = [model] if model else ["upscayl-lite-4x", "realesrgan-x4plus"]
    return all((TOOL_DIR / "models" / (n + ".bin")).is_file() for n in names)


def download(progress=None, cancel=lambda: False):
    """Real-ESRGAN (zip officiel, ≈ 45 Mo) + modèle rapide upscayl-lite (≈ 2,5 Mo)."""
    TOOL_DIR.mkdir(parents=True, exist_ok=True)
    (TOOL_DIR / "models").mkdir(exist_ok=True)
    if not EXE.is_file() or not (TOOL_DIR / "models" / "realesrgan-x4plus.bin").is_file():
        buf = io.BytesIO()
        req = urllib.request.Request(ZIP_URL, headers={"User-Agent": "Phoque Al"})
        with urllib.request.urlopen(req, timeout=60) as r:
            total = int(r.headers.get("Content-Length") or 45_000_000)
            while True:
                if cancel():
                    raise RuntimeError("annulé")
                b = r.read(1 << 20)
                if not b:
                    break
                buf.write(b)
                if progress:
                    progress(min(0.95, buf.tell() / total))
        with zipfile.ZipFile(buf) as z:
            for n in z.namelist():
                base = n.split("/")[-1]
                if not base or base.endswith((".jpg", ".mp4")):
                    continue
                dst = TOOL_DIR / ("models/" + base if "/models/" in "/" + n else base)
                with z.open(n) as s, open(dst, "wb") as d:
                    shutil.copyfileobj(s, d)
    for ext in ("param", "bin"):
        dst = TOOL_DIR / "models" / ("upscayl-lite-4x." + ext)
        if dst.is_file():
            continue
        req = urllib.request.Request(LITE_URL + ext, headers={"User-Agent": "Phoque Al"})
        with urllib.request.urlopen(req, timeout=60) as r, open(str(dst) + ".part", "wb") as f:
            shutil.copyfileobj(r, f)
        os.replace(str(dst) + ".part", dst)
    if progress:
        progress(1.0)


def gpu_name():
    """Nom de la carte graphique (pour information)."""
    if GPU["name"] is not None:
        return GPU["name"]
    name = ""
    for cmd in (["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name) -join ' + '"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=15, creationflags=_FLAGS).stdout.strip()
            if out:
                name = " + ".join(l.strip() for l in out.splitlines() if l.strip())
                break
        except Exception:
            continue
    GPU["name"] = name
    return name


def probe(path):
    ffprobe = find_tool("ffprobe")
    out = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,r_frame_rate,avg_frame_rate:format=duration", "-of", "json", path],
                         capture_output=True, text=True, timeout=60, creationflags=_FLAGS).stdout
    j = json.loads(out or "{}")
    st = (j.get("streams") or [{}])[0]

    def rate(s):
        try:
            a, b = s.split("/")
            return float(a) / float(b) if float(b) else 0
        except Exception:
            return 0
    fps = rate(st.get("avg_frame_rate", "0/0")) or rate(st.get("r_frame_rate", "0/0")) or 30
    if fps > 61 or fps < 10:
        fps = 30
    has_audio = bool(subprocess.run([ffprobe, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                                     "-of", "csv=p=0", path], capture_output=True, text=True, timeout=60,
                                    creationflags=_FLAGS).stdout.strip())
    return {"w": int(st.get("width") or 0), "h": int(st.get("height") or 0), "fps": round(fps, 3),
            "dur": float((j.get("format") or {}).get("duration") or 0), "audio": has_audio}


def out_path(path, test):
    stem, _ext = os.path.splitext(path)
    p = "%s [HD%s].mp4" % (stem, " test" if test else "")
    d = os.path.dirname(p)
    if os.access(d, os.W_OK):
        return p
    alt = os.path.join(os.path.expanduser("~"), "Videos", "Phoque Al HD")
    os.makedirs(alt, exist_ok=True)
    return os.path.join(alt, os.path.basename(p))


def out_height(src_h, target):
    if target <= 0 or src_h >= target:
        return src_h - src_h % 2            # meme taille : image nettoyee
    return target


# ----------------------------------------------------------------------------------------
# Calcul
# ----------------------------------------------------------------------------------------
def run(cmd, timeout=None):
    r = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=_FLAGS)
    if r.returncode != 0:
        lines = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(lines[-1] if lines else "erreur FFmpeg")
    return r


def process(job, report, stopping, paused, upscale=None):
    """Traite un travail. report(fraction). Reprend les morceaux déjà faits. -> chemin du résultat."""
    ffmpeg = find_tool("ffmpeg")
    path = job["path"]
    info = probe(path)
    if not info["h"] or info["dur"] <= 0:
        raise RuntimeError("vidéo illisible")
    fps = info["fps"]
    H = out_height(info["h"], job["target"])
    start0, total = 0.0, info["dur"]
    if job["test"]:
        start0 = max(0.0, info["dur"] * 0.4)
        total = min(30.0, info["dur"] - start0)
    nseg = max(1, int((total + SEG - 0.001) // SEG))
    key = hashlib.md5(("%s|%s|%s|%s" % (path, job["model"], H, job["test"])).encode()).hexdigest()[:16]
    wd = WORK / key
    wd.mkdir(parents=True, exist_ok=True)
    upscale = upscale or upscale_dir
    for k in range(nseg):
        seg = wd / ("seg_%05d.mp4" % k)
        if seg.is_file():
            report((k + 1) / (nseg + 1))
            continue
        while paused():
            if stopping():
                return None
            time.sleep(2)
        if stopping():
            return None
        a = start0 + k * SEG
        d = min(SEG, start0 + total - a)
        nfr = max(1, int(round(d * fps)))
        src, dst = wd / "in", wd / "out"
        for p in (src, dst):
            shutil.rmtree(p, ignore_errors=True)
            p.mkdir()
        run([ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", "%.3f" % a, "-i", path, "-an", "-sn",
             # entree reduite a la moitie de la cible au plus : x4 donne 2x la cible, puis reduction nette
             "-vf", "fps=%s,scale=-2:'min(ih,%d)':flags=area" % (fps, max(2, (H // 2) - (H // 2) % 2)),
             "-frames:v", str(nfr), "-q:v", "2", str(src / "%08d.jpg")], timeout=1800)
        upscale(src, dst, job["model"])
        tmp = str(seg) + ".part.mp4"
        run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-framerate", str(fps), "-i", str(dst / "%08d.jpg"),
             "-vf", "scale=-2:%d:flags=lanczos,format=yuv420p" % H, "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "18", "-r", str(fps), tmp], timeout=3600)
        os.replace(tmp, seg)
        shutil.rmtree(src, ignore_errors=True)
        shutil.rmtree(dst, ignore_errors=True)
        report((k + 1) / (nseg + 1))
    # assemblage + son d'origine
    lst = wd / "liste.txt"
    lst.write_text("".join("file '%s'\n" % (wd / ("seg_%05d.mp4" % k)).as_posix() for k in range(nseg)),
                   encoding="utf-8")
    out = out_path(path, job["test"])
    tmp = out + ".part.mp4"
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst)]
    if info["audio"]:
        cmd += ["-ss", "%.3f" % start0, "-t", "%.3f" % total, "-i", path, "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest"]
    else:
        cmd += ["-map", "0:v:0", "-c:v", "copy"]
    cmd += ["-movflags", "+faststart", "-metadata", "comment=Image améliorée par Phoque Al (Real-ESRGAN)", tmp]
    run(cmd, timeout=3600)
    os.replace(tmp, out)
    shutil.rmtree(wd, ignore_errors=True)
    report(1.0)
    return out


def upscale_dir(src, dst, model):
    """Real-ESRGAN sur un dossier d'images (carte graphique)."""
    cmd = [str(EXE), "-i", str(src), "-o", str(dst), "-n", model, "-s", "4", "-f", "jpg",
           "-m", str(TOOL_DIR / "models"), "-j", "1:2:2"]
    r = subprocess.run(cmd, capture_output=True, timeout=7200, creationflags=_FLAGS, cwd=str(TOOL_DIR))
    n_in = len(os.listdir(src))
    n_out = len([f for f in os.listdir(dst) if f.endswith(".jpg")])
    if n_out < n_in:
        msg = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError("Real-ESRGAN : %s" % (msg[-1] if msg else "échec (carte graphique ?)"))


class Worker:
    def __init__(self):
        self.wake = threading.Event()
        self.stopping = False
        self.cancel_id = None
        self.current = None
        self.pause_when_playing = False
        self.playing = lambda: False
        self.thread = threading.Thread(target=lambda: safe(self.run), daemon=True)

    def start(self):
        self.thread.start()

    def run(self):
        conn = db.connect()
        conn.execute("PRAGMA busy_timeout=8000")
        conn.executescript(SCHEMA)
        conn.execute("UPDATE enhance SET status='attente' WHERE status='en cours'")
        conn.commit()
        while not self.stopping:
            r = conn.execute("SELECT * FROM enhance WHERE status='attente' ORDER BY test DESC, id LIMIT 1").fetchone()
            if r is None or not tool_ready(r["model"]):
                self.wake.wait(30)
                self.wake.clear()
                continue
            job = dict(r)
            if not os.path.isfile(job["path"]):
                conn.execute("UPDATE enhance SET status='attente', error=? WHERE id=?",
                             ("fichier introuvable (disque débranché ?)", job["id"]))
                conn.commit()
                self.wake.wait(60)
                self.wake.clear()
                continue
            conn.execute("UPDATE enhance SET status='en cours', error=NULL WHERE id=?", (job["id"],))
            conn.commit()
            self.current = job
            t0 = time.time()

            def report(f, jid=job["id"]):
                self.current = dict(job, progress=f)
                conn.execute("UPDATE enhance SET progress=? WHERE id=?", (f, jid))
                conn.commit()
            try:
                out = process(job, report, lambda: self.stopping or self.cancel_id == job["id"],
                              lambda: self.pause_when_playing and self.playing())
                if out is None:
                    st = "annulé" if self.cancel_id == job["id"] else "attente"
                    conn.execute("UPDATE enhance SET status=? WHERE id=?", (st, job["id"]))
                else:
                    conn.execute("UPDATE enhance SET status='terminé', out=?, done_at=?, progress=1 WHERE id=?",
                                 (out, time.time(), job["id"]))
                    log("amélioration : %s en %.0f s" % (out, time.time() - t0))
            except Exception as e:
                log("amélioration %s : %r" % (job["path"], e))
                conn.execute("UPDATE enhance SET status='erreur', error=? WHERE id=?", (str(e)[:300], job["id"]))
            conn.commit()
            self.current = None
            self.cancel_id = None


# ----------------------------------------------------------------------------------------
# Interface
# ----------------------------------------------------------------------------------------
def video_path(conn, vid):
    r = conn.execute("SELECT v.rel_path, s.serial, s.rel_root FROM videos v JOIN sources s ON s.id=v.source_id "
                     "WHERE v.id=?", (vid,)).fetchone()
    if not r:
        return None
    root = volumes.resolve(r["serial"], r["rel_root"])
    return os.path.join(root, r["rel_path"]) if root else None


def ensure_tool(main, then):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QMessageBox, QProgressDialog
    if tool_ready():
        then()
        return
    if QMessageBox.question(main, "Phoque Al", "L'amélioration d'image demande Real-ESRGAN (≈ 50 Mo, téléchargé une "
                            "seule fois depuis GitHub).\nLe télécharger maintenant ?") != QMessageBox.Yes:
        return
    dlg = QProgressDialog("Téléchargement de Real-ESRGAN…", "Annuler", 0, 100, main)
    dlg.setWindowTitle("Phoque Al")
    dlg.setMinimumDuration(0)
    st = {"p": 0.0, "err": None, "done": False}

    def job():
        try:
            download(lambda f: st.__setitem__("p", f), cancel=lambda: dlg.wasCanceled())
        except Exception as e:
            st["err"] = str(e)
        st["done"] = True
    threading.Thread(target=job, daemon=True).start()
    t = QTimer(dlg)

    def poll():
        dlg.setValue(int(st["p"] * 100))
        if st["done"]:
            t.stop()
            dlg.close()
            if st["err"]:
                if not dlg.wasCanceled():
                    QMessageBox.warning(main, "Phoque Al", "Téléchargement impossible : %s" % st["err"])
            else:
                then()
    t.timeout.connect(poll)
    t.start(200)


def open_dialog(main, vids):
    from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel,
                                   QVBoxLayout)
    paths = [(v, video_path(main.conn, v)) for v in vids]
    paths = [(v, p) for v, p in paths if p and os.path.isfile(p)]
    if not paths:
        main.status.setText("Fichier introuvable (disque débranché ?).")
        return
    d = QDialog(main)
    d.setWindowTitle("Améliorer l'image (IA)")
    lay = QVBoxLayout(d)
    try:
        info = probe(paths[0][1])
        src = "%d × %d · %.0f img/s · %d min" % (info["w"], info["h"], info["fps"], info["dur"] // 60)
    except Exception:
        info, src = None, "?"
    head = QLabel(("<b>%s</b><br>" % os.path.basename(paths[0][1]) if len(paths) == 1 else
                   "<b>%d vidéos</b><br>" % len(paths)) + "Image d'origine : %s<br>" % src +
                  "Carte graphique : %s" % (gpu_name() or "non détectée"))
    head.setWordWrap(True)
    lay.addWidget(head)
    form = QFormLayout()
    model = QComboBox()
    for k, lab in MODELS:
        model.addItem(lab, k)
    target = QComboBox()
    for k, lab in TARGETS:
        target.addItem(lab, k)
    form.addRow("Modèle", model)
    form.addRow("Résolution visée", target)
    lay.addLayout(form)
    test = QCheckBox("Tester d'abord sur 30 s (conseillé)")
    test.setChecked(True)
    lay.addWidget(test)
    note = QLabel("Le résultat est une nouvelle vidéo « [HD] » à côté de l'originale (qui n'est pas modifiée). "
                  "Compte de quelques minutes (Rapide) à plus d'une heure (Qualité max) pour 10 min de vidéo, "
                  "selon la carte graphique. Tu peux fermer Phoque Al : le calcul reprendra.")
    note.setWordWrap(True)
    note.setStyleSheet("color:#8a8a96")
    lay.addWidget(note)
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.button(QDialogButtonBox.Ok).setText("Démarrer")
    bb.accepted.connect(d.accept)
    bb.rejected.connect(d.reject)
    lay.addWidget(bb)
    if d.exec() != QDialog.Accepted:
        return
    m, t, tst = model.currentData(), target.currentData(), test.isChecked()

    def go():
        now = time.time()
        for v, p in paths:
            if tst:
                main.conn.execute("INSERT INTO enhance(video_id, path, model, target, test, added_at) VALUES (?,?,?,?,1,?)",
                                  (v, p, m, t, now))
            main.conn.execute("INSERT INTO enhance(video_id, path, model, target, test, added_at) VALUES (?,?,?,?,0,?)",
                              (v, p, m, t, now))
        main.conn.commit()
        WORKER.wake.set()
        main.status.setText("Amélioration de l'image : %d vidéo(s) en file d'attente (⋯ → Image : amélioration IA)"
                            % len(paths))
    ensure_tool(main, go)


def card_menu(main, menu, vids):
    if not vids or WORKER is None:
        return
    menu.addAction("Améliorer l'image (IA)…").triggered.connect(lambda: open_dialog(main, list(vids)))


def menu(main, m):
    sub = m.addMenu("Image : amélioration IA")
    cur = WORKER.current if WORKER else None
    if cur:
        a = sub.addAction("En cours : %s — %d %%%s" % (os.path.basename(cur["path"])[:50],
                                                       int((cur.get("progress") or 0) * 100),
                                                       " (test 30 s)" if cur.get("test") else ""))
        a.setEnabled(False)
        sub.addAction("Annuler le travail en cours").triggered.connect(
            lambda: (setattr(WORKER, "cancel_id", cur["id"])))
    try:
        rows = main.conn.execute("SELECT status, COUNT(*) FROM enhance GROUP BY status").fetchall()
        n = dict((r[0], r[1]) for r in rows)
    except Exception:
        n = {}
    a = sub.addAction("En attente : %d · terminées : %d · erreurs : %d" % (n.get("attente", 0), n.get("terminé", 0),
                                                                          n.get("erreur", 0)))
    a.setEnabled(False)
    try:
        last = main.conn.execute("SELECT out FROM enhance WHERE status='terminé' ORDER BY done_at DESC LIMIT 1").fetchone()
    except Exception:
        last = None
    if last and last[0] and os.path.isfile(last[0]):
        sub.addAction("Ouvrir le dossier de la dernière vidéo améliorée").triggered.connect(
            lambda: subprocess.Popen(["explorer", "/select,", os.path.normpath(last[0])]))
    if n.get("attente"):
        sub.addAction("Vider la file d'attente").triggered.connect(
            lambda: (main.conn.execute("DELETE FROM enhance WHERE status='attente'"), main.conn.commit()))
    if n.get("erreur"):
        sub.addAction("Réessayer les vidéos en erreur").triggered.connect(
            lambda: (main.conn.execute("UPDATE enhance SET status='attente', progress=0 WHERE status='erreur'"),
                     main.conn.commit(), WORKER.wake.set()))
    p = sub.addAction("Mettre en pause pendant que des vidéos jouent")
    p.setCheckable(True)
    p.setChecked(db.get_setting(main.conn, "enh_pause", "0") == "1")

    def set_p(on):
        db.set_setting(main.conn, "enh_pause", "1" if on else "0")
        WORKER.pause_when_playing = bool(on)
    p.triggered.connect(set_p)
    g = sub.addAction("Carte graphique : %s" % (gpu_name() or "non détectée"))
    g.setEnabled(False)
    i = sub.addAction("Clic droit sur une vignette : « Améliorer l'image (IA)… »")
    i.setEnabled(False)


def install(main):
    global WORKER
    main.conn.executescript(SCHEMA)
    main.conn.commit()
    WORKER = Worker()
    WORKER.pause_when_playing = db.get_setting(main.conn, "enh_pause", "0") == "1"

    def playing():
        try:
            return any(getattr(w, "player", None) is not None and w.player.is_playing()
                       for w in list(main.ctrl.players.values()))
        except Exception:
            return False
    WORKER.playing = playing
    WORKER.start()
    threading.Thread(target=gpu_name, daemon=True).start()
    old_close = main.closeEvent

    def close_event(event):
        WORKER.stopping = True
        WORKER.wake.set()
        old_close(event)
    main.closeEvent = close_event
