"""Séparation IA du son : retire la musique d'un PMV en gardant les voix, cris, gémissements…

- Modèle MDX-Net « Kim Vocal 2 » (projet Ultimate Vocal Remover, ≈ 64 Mo, téléchargé une seule fois),
  exécuté sur le processeur avec onnxruntime (déjà utilisé pour les tags).
- Le résultat est un petit fichier son (dossier de données de Phoque Al), jamais une modification de tes vidéos.
- Pendant la lecture, ce son devient une piste supplémentaire de la vidéo : la sourdine l'utilise quand
  ta musique joue (à la place de couper le PMV), touche U pour forcer.
- Analyse : à la demande (clic droit sur une vignette) ou en tâche de fond pour tous les PMV.
"""
import hashlib
import os
import subprocess
import sys
import threading
import time
import urllib.request

import numpy as np

from .. import db, volumes
from ..config import DATA_DIR, find_tool
from .common import log, safe

_FLAGS = 0x08000000 if sys.platform == "win32" else 0
MODEL_DIR = DATA_DIR / "models" / "mdx"
MODEL = MODEL_DIR / "Kim_Vocal_2.onnx"
MODEL_URL = "https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/Kim_Vocal_2.onnx"
MODEL_SHA = "ce74ef3b6a6024ce44211a07be9cf8bc6d87728cc852a68ab34eb8e58cde9c8b"
MODEL_MB = 64
OUT_DIR = DATA_DIR / "sons_sans_musique"
SR = 44100
N_FFT, HOP, DIM_F, DIM_T = 7680, 1024, 3072, 256
CHUNK = HOP * (DIM_T - 1)
TRIM = N_FFT // 2
GEN = CHUNK - 2 * TRIM
SCHEMA = """
CREATE TABLE IF NOT EXISTS sep(video_id INTEGER PRIMARY KEY, key TEXT, file TEXT, done_at REAL, secs REAL);
CREATE INDEX IF NOT EXISTS idx_sep_key ON sep(key);
"""
WORKER = None
KEYS = {}            # cle fichier (nom + taille) -> chemin du son separe
SLAVES = {}          # mrl / chemin video -> chemin du son separe (medias ouverts)


# ----------------------------------------------------------------------------------------
# Calcul (pur numpy + onnxruntime)
# ----------------------------------------------------------------------------------------
_WIN = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(N_FFT) / N_FFT)).astype(np.float32)   # hann periodique


def stft(x):
    """x : (2, CHUNK) -> (4, DIM_F, DIM_T)  [G réel, G imag, D réel, D imag] (comme torch.stft center=True)."""
    pad = N_FFT // 2
    xp = np.pad(x, ((0, 0), (pad, pad)), mode="reflect")
    idx = np.arange(DIM_T)[:, None] * HOP + np.arange(N_FFT)[None, :]
    fr = xp[:, idx] * _WIN                                   # (2, T, N_FFT)
    sp = np.fft.rfft(fr, axis=-1)[:, :, :DIM_F]              # (2, T, F)
    sp = np.transpose(sp, (0, 2, 1))                         # (2, F, T)
    out = np.empty((4, DIM_F, DIM_T), np.float32)
    out[0], out[1], out[2], out[3] = sp[0].real, sp[0].imag, sp[1].real, sp[1].imag
    return out


def istft(s):
    """(4, DIM_F, DIM_T) -> (2, CHUNK)"""
    nb = N_FFT // 2 + 1
    sp = np.zeros((2, nb, DIM_T), np.complex64)
    sp[0, :DIM_F] = s[0] + 1j * s[1]
    sp[1, :DIM_F] = s[2] + 1j * s[3]
    fr = np.fft.irfft(np.transpose(sp, (0, 2, 1)), n=N_FFT, axis=-1) * _WIN       # (2, T, N_FFT)
    total = N_FFT + HOP * (DIM_T - 1)
    y = np.zeros((2, total), np.float32)
    wsum = np.zeros(total, np.float32)
    w2 = _WIN ** 2
    for t in range(DIM_T):
        a = t * HOP
        y[:, a:a + N_FFT] += fr[:, t]
        wsum[a:a + N_FFT] += w2
    pad = N_FFT // 2
    y = y[:, pad:pad + CHUNK]
    w = wsum[pad:pad + CHUNK]
    return y / np.maximum(w, 1e-8)


class Separator:
    def __init__(self, threads=2):
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.intra_op_num_threads = max(1, threads)
        so.inter_op_num_threads = 1
        self.sess = ort.InferenceSession(str(MODEL), so, providers=["CPUExecutionProvider"])
        self.inp = self.sess.get_inputs()[0].name

    def voices(self, mix, cancel=lambda: False, progress=None):
        """mix : (2, n) float32 44,1 kHz -> voix / sons (2, n)."""
        n = mix.shape[1]
        pad = GEN - n % GEN
        mp = np.concatenate([np.zeros((2, TRIM), np.float32), mix, np.zeros((2, pad + TRIM), np.float32)], axis=1)
        out = []
        steps = list(range(0, n + pad, GEN))
        for k, i in enumerate(steps):
            if cancel():
                return None
            wave = mp[:, i:i + CHUNK]
            spec = stft(wave)[None]
            pred = self.sess.run(None, {self.inp: spec})[0][0]
            out.append(istft(pred)[:, TRIM:-TRIM])
            if progress:
                progress((k + 1) / len(steps))
        return np.concatenate(out, axis=1)[:, :n]


def read_audio(ffmpeg, path, start=None, dur=None):
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error"]
    if start:
        cmd += ["-ss", "%.2f" % start]
    cmd += ["-i", path]
    if dur:
        cmd += ["-t", "%.2f" % dur]
    cmd += ["-vn", "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, timeout=3600, creationflags=_FLAGS).stdout
    a = np.frombuffer(raw, dtype=np.float32)
    a = a[:len(a) // 2 * 2].reshape(-1, 2).T.copy()
    return a


def write_audio(ffmpeg, data, dst):
    tmp = str(dst) + ".part.m4a"
    p = subprocess.Popen([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "f32le", "-ar", str(SR),
                          "-ac", "2", "-i", "-", "-c:a", "aac", "-b:a", "128k",
                          "-metadata", "title=Sons des vidéos (sans musique)", tmp],
                         stdin=subprocess.PIPE, creationflags=_FLAGS)
    p.communicate(np.clip(data.T, -1, 1).astype(np.float32).tobytes())
    if p.returncode != 0 or not os.path.isfile(tmp):
        raise RuntimeError("encodage du son impossible")
    os.replace(tmp, dst)


def file_key(path):
    try:
        return "%s|%d" % (os.path.basename(path).lower(), os.path.getsize(path))
    except OSError:
        return None


# ----------------------------------------------------------------------------------------
# Modèle
# ----------------------------------------------------------------------------------------
def model_ready():
    return MODEL.is_file() and MODEL.stat().st_size > 60_000_000


def onnx_ok():
    try:
        import onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


def download(progress=None, cancel=lambda: False):
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    tmp = MODEL.with_suffix(".part")
    req = urllib.request.Request(MODEL_URL, headers={"User-Agent": "Phoque Al"})
    h = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or MODEL_MB * 1_048_576)
        got = 0
        while True:
            if cancel():
                raise RuntimeError("annulé")
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
            h.update(b)
            got += len(b)
            if progress:
                progress(min(1.0, got / total))
    if h.hexdigest() != MODEL_SHA:
        os.replace(tmp, str(tmp) + ".bad")
        raise RuntimeError("fichier du modèle abîmé, réessaie")
    os.replace(tmp, MODEL)


# ----------------------------------------------------------------------------------------
# Base et tâche de fond
# ----------------------------------------------------------------------------------------
def load_keys(conn):
    KEYS.clear()
    try:
        for r in conn.execute("SELECT key, file FROM sep WHERE file IS NOT NULL"):
            f = OUT_DIR / r[1]
            if r[0] and f.is_file():
                KEYS[r[0]] = str(f)
    except Exception as e:
        log("séparation : %r" % e)


def video_path(conn, vid):
    r = conn.execute("SELECT v.rel_path, s.serial, s.rel_root FROM videos v JOIN sources s ON s.id=v.source_id "
                     "WHERE v.id=?", (vid,)).fetchone()
    if not r:
        return None
    root = volumes.resolve(r["serial"], r["rel_root"])
    return os.path.join(root, r["rel_path"]) if root else None


def has_three_tracks(ffmpeg, path):
    """PMV créé par Phoque Al : il a déjà sa piste sans musique."""
    ffprobe = find_tool("ffprobe")
    if not ffprobe:
        return False
    try:
        out = subprocess.run([ffprobe, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                              "-of", "csv=p=0", path], capture_output=True, text=True, timeout=30,
                             creationflags=_FLAGS).stdout
        return len([l for l in out.splitlines() if l.strip()]) >= 3
    except Exception:
        return False


class Worker:
    def __init__(self, main_conn_setting):
        self.queue = []
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.background = main_conn_setting
        self.stopping = False
        self.current = None           # (video_id, avancement 0..1)
        self.done_n = 0
        self.last_error = ""
        self.listeners = []
        self.thread = threading.Thread(target=lambda: safe(self.run), daemon=True)

    def start(self):
        self.thread.start()

    def request(self, vid, front=True):
        with self.lock:
            if vid in self.queue:
                self.queue.remove(vid)
            self.queue.insert(0, vid) if front else self.queue.append(vid)
        self.wake.set()

    def _next(self, conn):
        with self.lock:
            while self.queue:
                v = self.queue.pop(0)
                if conn.execute("SELECT 1 FROM sep WHERE video_id=?", (v,)).fetchone() is None:
                    return v, True
        if self.background:
            for r in conn.execute("SELECT v.id, v.title, v.rel_path FROM videos v LEFT JOIN sep s ON s.video_id=v.id "
                                  "WHERE s.video_id IS NULL AND v.missing=0 AND v.kind='video' "
                                  "ORDER BY v.liked DESC, v.play_count DESC"):
                if pmv_row(conn, r):
                    return r[0], False
        return None, False

    def run(self):
        ffmpeg = find_tool("ffmpeg")
        if not ffmpeg or not onnx_ok():
            return
        conn = db.connect()
        conn.execute("PRAGMA busy_timeout=8000")
        conn.executescript(SCHEMA)
        sep = None
        while not self.stopping:
            if not model_ready():
                self.wake.wait(10)
                self.wake.clear()
                continue
            vid, asked = self._next(conn)
            if vid is None:
                self.wake.wait(90 if self.background else 30)
                self.wake.clear()
                continue
            path = video_path(conn, vid)
            if not path or not os.path.isfile(path):
                time.sleep(10 if not asked else 0)     # disque debranche : on reessaiera
                if asked:
                    self.last_error = "fichier introuvable (disque débranché ?)"
                    self._notify(vid, False)
                continue
            try:
                if has_three_tracks(ffmpeg, path):
                    conn.execute("INSERT OR REPLACE INTO sep(video_id, key, file, done_at, secs) VALUES (?,?,?,?,?)",
                                 (vid, file_key(path), None, time.time(), 0))
                    conn.commit()
                    self._notify(vid, True)
                    continue
                if sep is None:
                    sep = Separator(threads=max(1, (os.cpu_count() or 2) // 2))   # la lecture garde de la marge
                t0 = time.time()
                self.current = (vid, 0.0)
                mix = read_audio(ffmpeg, path)
                if mix.shape[1] < SR:
                    raise RuntimeError("pas de son")
                voc = sep.voices(mix, cancel=lambda: self.stopping,
                                 progress=lambda f, v=vid: setattr(self, "current", (v, f)))
                if voc is None:
                    break
                OUT_DIR.mkdir(parents=True, exist_ok=True)
                name = "%d.m4a" % vid
                write_audio(ffmpeg, voc, OUT_DIR / name)
                key = file_key(path)
                conn.execute("INSERT OR REPLACE INTO sep(video_id, key, file, done_at, secs) VALUES (?,?,?,?,?)",
                             (vid, key, name, time.time(), time.time() - t0))
                conn.commit()
                if key:
                    KEYS[key] = str(OUT_DIR / name)
                self.done_n += 1
                log("séparation : vidéo %d en %.0f s" % (vid, time.time() - t0))
                self._notify(vid, True)
            except Exception as e:
                self.last_error = str(e)
                log("séparation %s : %r" % (vid, e))
                conn.execute("INSERT OR REPLACE INTO sep(video_id, key, file, done_at, secs) VALUES (?,?,?,?,?)",
                             (vid, None, None, time.time(), -1))
                conn.commit()
                self._notify(vid, False)
            finally:
                self.current = None

    def _notify(self, vid, ok):
        for f in list(self.listeners):
            try:
                f(vid, ok)
            except Exception:
                pass


def pmv_row(conn, r):
    from .rythme import PMV_WORD, pmv_ids
    if r["id"] in pmv_ids(conn):
        return True
    return bool(PMV_WORD.search("%s %s" % (r["title"] or "", r["rel_path"] or "")))


# ----------------------------------------------------------------------------------------
# Lecture : le son séparé devient une piste en plus
# ----------------------------------------------------------------------------------------
def slave_for(player):
    """Chemin du son séparé attaché au média en cours de ce lecteur VLC, ou None."""
    try:
        m = player.get_media()
        return SLAVES.get(m.get_mrl()) if m is not None else None
    except Exception:
        return None


def install_media(main):
    from ..players import Controller
    if getattr(Controller, "_extras_sep", False):
        return
    orig = Controller.media_for

    def media_for(self, path, hold=False):
        m = orig(self, path, hold)
        try:
            if KEYS and db.get_setting(self.conn, "sep_on", "1") == "1":
                f = KEYS.get(file_key(path))
                if f and os.path.isfile(f):
                    import pathlib
                    import vlc
                    m.slaves_add(vlc.MediaSlaveType.audio, 0, pathlib.Path(f).as_uri())
                    SLAVES[m.get_mrl()] = f
        except Exception as e:
            log("séparation (lecture) : %r" % e)
        return m
    Controller.media_for = media_for
    Controller._extras_sep = True


# ----------------------------------------------------------------------------------------
# Interface
# ----------------------------------------------------------------------------------------
def counts(conn):
    try:
        done = conn.execute("SELECT COUNT(*) FROM sep WHERE file IS NOT NULL").fetchone()[0]
    except Exception:
        done = 0
    return done


def ensure_model(main, then=None):
    """Propose le téléchargement du modèle (une fois). then() une fois prêt."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QMessageBox, QProgressDialog
    if not onnx_ok():
        QMessageBox.warning(main, "Phoque Al", "Il manque un composant (onnxruntime) : relance Phoque Al avec "
                            "lancer_phoqueal.bat sur un PC connecté.")
        return
    if model_ready():
        if then:
            then()
        return
    if QMessageBox.question(main, "Phoque Al", "Séparer la musique demande le modèle d'IA « Kim Vocal 2 » "
                            "(≈ %d Mo, téléchargé une seule fois depuis GitHub).\nLe télécharger maintenant ?"
                            % MODEL_MB) != QMessageBox.Yes:
        return
    dlg = QProgressDialog("Téléchargement du modèle de séparation…", "Annuler", 0, 100, main)
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
                if WORKER:
                    WORKER.wake.set()
                if then:
                    then()
    t.timeout.connect(poll)
    t.start(200)


def separate(main, vids):
    def go():
        for v in vids:
            WORKER.request(v)
        main.status.setText("Séparation de la musique : %d vidéo(s) en file d'attente (⋯ → Son : séparation IA)"
                            % len(vids))
    ensure_model(main, go)


def card_menu(main, menu, vids):
    if not vids or WORKER is None:
        return
    menu.addAction("Retirer la musique (IA, garde les voix)").triggered.connect(lambda: separate(main, list(vids)))


def menu(main, m):
    sub = m.addMenu("Son : séparation IA (musique / voix)")
    done = counts(main.conn)
    cur = WORKER.current if WORKER else None
    if cur:
        info = "En cours : %d %% · %d vidéo(s) séparée(s)" % (int(cur[1] * 100), done)
    else:
        info = "Vidéos séparées : %d" % done
    a = sub.addAction(info)
    a.setEnabled(False)
    if not model_ready():
        sub.addAction("Télécharger le modèle d'IA (≈ %d Mo)…" % MODEL_MB).triggered.connect(lambda: ensure_model(main))
    on = sub.addAction("Utiliser le son séparé pendant ma musique")
    on.setCheckable(True)
    on.setChecked(db.get_setting(main.conn, "sep_on", "1") == "1")
    on.triggered.connect(lambda v: db.set_setting(main.conn, "sep_on", "1" if v else "0"))
    bg = sub.addAction("Séparer tous les PMV en tâche de fond (coups de cœur d'abord)")
    bg.setCheckable(True)
    bg.setChecked(db.get_setting(main.conn, "sep_background", "0") == "1")

    def set_bg(v):
        db.set_setting(main.conn, "sep_background", "1" if v else "0")
        if v:
            ensure_model(main)
        if WORKER:
            WORKER.background = bool(v)
            WORKER.wake.set()
    bg.triggered.connect(set_bg)
    i = sub.addAction("Clic droit sur une vignette : « Retirer la musique (IA) » · quelques minutes de calcul par vidéo")
    i.setEnabled(False)


def install(main):
    global WORKER
    main.conn.executescript(SCHEMA)
    main.conn.commit()
    load_keys(main.conn)
    install_media(main)
    WORKER = Worker(db.get_setting(main.conn, "sep_background", "0") == "1")
    WORKER.listeners.append(lambda vid, ok: None)
    WORKER.start()

    old_close = main.closeEvent

    def close_event(event):
        try:
            WORKER.stopping = True
            WORKER.wake.set()
        except Exception:
            pass
        old_close(event)
    main.closeEvent = close_event
