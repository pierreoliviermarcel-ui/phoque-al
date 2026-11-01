"""Tags proposés automatiquement (sans internet ni compte, tout reste sur le PC).

1. Reconnaissance par l'image : une petite IA (CLIP, ~155 Mo, téléchargée une fois) regarde 4 images
   de chaque vidéo et propose des tags d'une liste modifiable (lieu, nombre de personnes, cheveux,
   style, tenue…).
2. Apprentissage : pour chaque tag déjà posé sur au moins 6 vidéos, Phoque Al apprend à quoi ces vidéos
   ressemblent et propose le tag sur les vidéos qui leur ressemblent.
3. pmv : musique continue + coupes très rapides.

Les propositions attendent ta validation (⋯ → Tags proposés) ; au-dessus d'un seuil réglable elles
s'appliquent seules. Un tag refusé n'est jamais reproposé. Tout tag automatique peut être annulé.
"""
import gzip
import json
import math
import os
import random
import re
import subprocess
import sys
import threading
import time
import urllib.request

import numpy as np

from .. import db
from ..config import DATA_DIR
from .common import log, safe

_FLAGS = 0x08000000 if sys.platform == "win32" else 0
MODEL_DIR = DATA_DIR / "models" / "clip-b32"
HF = "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main/"
MODEL_FILES = [("onnx/vision_model_quantized.onnx", "vision.onnx", 89),
               ("onnx/text_model_quantized.onnx", "text.onnx", 65),
               ("tokenizer.json", "tokenizer.json", 2)]
DEFS_VERSION = 2
NEUTRAL = ["a person", "a nude person", "two people"]
MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)

SCHEMA = """
CREATE TABLE IF NOT EXISTS vemb(video_id INTEGER PRIMARY KEY, emb BLOB NOT NULL, n INTEGER, done_at REAL, pmv REAL);
CREATE TABLE IF NOT EXISTS tag_suggest(video_id INTEGER NOT NULL, tag TEXT NOT NULL, score REAL NOT NULL,
    source TEXT NOT NULL, state INTEGER NOT NULL DEFAULT 0, at REAL, PRIMARY KEY(video_id, tag));
CREATE INDEX IF NOT EXISTS idx_tag_suggest_state ON tag_suggest(state, tag);
"""
# state : 0 en attente, 1 validé par toi, 2 appliqué automatiquement, -1 refusé (jamais reproposé)

# Liste par défaut. Groupes = choix exclusifs (un seul lieu…). "en" = description pour l'IA (anglais).
DEFAULT_DEFS = {
    "groups": [
        {"name": "Lieu", "options": [
            {"tag": "Chambre", "en": "a bedroom with a bed"},
            {"tag": "Salon", "en": "a living room with a sofa"},
            {"tag": "Salle de bain", "en": "a bathroom or a shower"},
            {"tag": "Cuisine", "en": "a kitchen"},
            {"tag": "Bureau", "en": "an office with a desk"},
            {"tag": "Voiture", "en": "the inside of a car"},
            {"tag": "Piscine / plage", "en": "a swimming pool or a beach"},
            {"tag": "Extérieur", "en": "outdoors in nature, a garden or a forest"},
            {"tag": None, "en": "a studio with a plain background"},
        ]},
        {"name": "Personnes", "options": [
            {"tag": "Solo", "en": "one single person alone"},
            {"tag": "Duo", "en": "two people together"},
            {"tag": "Groupe", "en": "a group of three or more people"},
        ]},
        {"name": "Cheveux", "options": [
            {"tag": "Blonde", "en": "a woman with blonde hair"},
            {"tag": "Brune", "en": "a woman with brown hair"},
            {"tag": "Cheveux noirs", "en": "a woman with black hair"},
            {"tag": "Rousse", "en": "a woman with red hair"},
        ]},
        {"name": "Style", "options": [
            {"tag": "Amateur", "en": "an amateur home video filmed with a phone"},
            {"tag": "Studio", "en": "a professional studio production with good lighting"},
            {"tag": "Webcam", "en": "a webcam recording"},
            {"tag": "POV", "en": "a first person point of view shot"},
        ]},
    ],
    "binary": [
        {"tag": "Lingerie", "en": "a woman wearing lingerie", "neg": "a woman wearing ordinary clothes"},
        {"tag": "Tatouages", "en": "a person with visible tattoos", "neg": "a person with no tattoos"},
        {"tag": "Lunettes", "en": "a person wearing glasses", "neg": "a person without glasses"},
        {"tag": "Talons", "en": "a woman wearing high heels", "neg": "a woman barefoot or in flat shoes"},
        {"tag": "Uniforme", "en": "a person wearing a uniform or a costume", "neg": "a person in casual clothes"},
        {"tag": "Noir et blanc", "en": "a black and white photo", "neg": "a color photo"},
    ],
}


def S(conn, k, d):
    return db.get_setting(conn, "at_" + k, d)


def defs(conn):
    try:
        d = json.loads(S(conn, "defs", "") or "null")
        if isinstance(d, dict) and "groups" in d:
            return d
    except Exception:
        pass
    return DEFAULT_DEFS


def ensure_schema(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def model_ready():
    return all((MODEL_DIR / name).is_file() for _src, name, _mb in MODEL_FILES)


def deps_ready():
    try:
        import onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


# ----------------------------------------------------------------------------------------
# Tokeniseur CLIP (BPE), lu depuis tokenizer.json
# ----------------------------------------------------------------------------------------
def _bytes_to_unicode():
    bs = list(range(ord("!"), ord("~") + 1)) + list(range(ord("¡"), ord("¬") + 1)) + list(range(ord("®"), ord("ÿ") + 1))
    cs = bs[:]
    n = 0
    for b in range(256):
        if b not in bs:
            bs.append(b)
            cs.append(256 + n)
            n += 1
    return dict(zip(bs, [chr(c) for c in cs]))


class ClipTokenizer:
    PAT = re.compile(r"<\|startoftext\|>|<\|endoftext\|>|'s|'t|'re|'ve|'m|'ll|'d|[^\W\d_]+|\d|[^\s\w]+|_+",
                     re.IGNORECASE)

    def __init__(self, path):
        d = json.load(open(path, encoding="utf-8"))
        m = d["model"]
        self.vocab = m["vocab"]
        merges = [tuple(x.split(" ")) if isinstance(x, str) else tuple(x) for x in m["merges"]]
        self.ranks = {p: i for i, p in enumerate(merges)}
        self.byte_enc = _bytes_to_unicode()
        self.sot = self.vocab.get("<|startoftext|>", 49406)
        self.eot = self.vocab.get("<|endoftext|>", 49407)
        self.cache = {}

    def bpe(self, token):
        if token in self.cache:
            return self.cache[token]
        word = tuple(token[:-1]) + (token[-1] + "</w>",)
        while len(word) > 1:
            pairs = {(word[i], word[i + 1]) for i in range(len(word) - 1)}
            best = min(pairs, key=lambda p: self.ranks.get(p, 1 << 30))
            if best not in self.ranks:
                break
            a, b = best
            out, i = [], 0
            while i < len(word):
                if i < len(word) - 1 and word[i] == a and word[i + 1] == b:
                    out.append(a + b)
                    i += 2
                else:
                    out.append(word[i])
                    i += 1
            word = tuple(out)
        self.cache[token] = word
        return word

    def encode(self, text, length=77):
        text = " ".join(text.lower().split())
        ids = [self.sot]
        for tok in self.PAT.findall(text):
            t = "".join(self.byte_enc[b] for b in tok.encode("utf-8"))
            for piece in self.bpe(t):
                if piece in self.vocab:
                    ids.append(self.vocab[piece])
        ids = ids[:length - 1] + [self.eot]
        return ids


# ----------------------------------------------------------------------------------------
# Modèle
# ----------------------------------------------------------------------------------------
class Clip:
    def __init__(self, threads=2):
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = 1
        prov = ["CPUExecutionProvider"]
        self.vis = ort.InferenceSession(str(MODEL_DIR / "vision.onnx"), so, providers=prov)
        self.txt = ort.InferenceSession(str(MODEL_DIR / "text.onnx"), so, providers=prov)
        self.tok = ClipTokenizer(MODEL_DIR / "tokenizer.json")
        self.tcache = {}

    @staticmethod
    def _pick(sess, outs, want):
        names = [o.name for o in sess.get_outputs()]
        for i, n in enumerate(names):
            if n == want:
                return outs[i]
        for i, n in enumerate(names):
            if "embed" in n:
                return outs[i]
        return outs[0]

    def image(self, pil_images):
        arr = np.stack([preprocess(im) for im in pil_images]).astype(np.float32)
        inp = self.vis.get_inputs()[0].name
        out = self._pick(self.vis, self.vis.run(None, {inp: arr}), "image_embeds")
        out = np.asarray(out, dtype=np.float32)
        if out.ndim == 3:                       # (n, tokens, d) : prend le jeton de classe
            out = out[:, 0, :]
        return normalize(out)

    def text(self, prompt):
        if prompt in self.tcache:
            return self.tcache[prompt]
        ids = np.array([self.tok.encode(prompt)], dtype=np.int64)
        feed = {}
        for i in self.txt.get_inputs():
            if "mask" in i.name:
                feed[i.name] = np.ones_like(ids)
            else:
                feed[i.name] = ids
        out = np.asarray(self._pick(self.txt, self.txt.run(None, feed), "text_embeds"), dtype=np.float32)
        if out.ndim == 3:
            out = out[:, int(np.argmax(ids[0])), :]
        v = normalize(out)[0]
        self.tcache[prompt] = v
        return v


def normalize(x):
    return x / (np.linalg.norm(x, axis=-1, keepdims=True) + 1e-9)


def preprocess(im):
    from PIL import Image
    im = im.convert("RGB")
    w, h = im.size
    s = 224 / min(w, h)
    im = im.resize((max(224, round(w * s)), max(224, round(h * s))), Image.BICUBIC)
    w, h = im.size
    l, t = (w - 224) // 2, (h - 224) // 2
    im = im.crop((l, t, l + 224, t + 224))
    a = (np.asarray(im, dtype=np.float32) / 255.0 - MEAN) / STD
    return a.transpose(2, 0, 1)


# ----------------------------------------------------------------------------------------
# Scores (purs, testables)
# ----------------------------------------------------------------------------------------
def prompt(en):
    return "a photo of " + en if not en.lower().startswith(("a photo", "a picture", "an image")) else en


def zero_shot(frame_embs, d, text_fn, scale=100.0):
    """frame_embs (n, 512) normalisés -> [(tag, score)] ; moyenne des probabilités sur les images."""
    out = []
    for g in d.get("groups", []):
        opts = [o for o in g["options"] if o.get("en")]
        if len(opts) < 2:
            continue
        T = np.stack([text_fn(prompt(o["en"])) for o in opts])          # (k, 512)
        logits = scale * frame_embs @ T.T                                   # (n, k)
        p = np.exp(logits - logits.max(axis=1, keepdims=True))
        p = (p / p.sum(axis=1, keepdims=True)).mean(axis=0)
        for o, s in zip(opts, p):
            if o.get("tag"):
                out.append((o["tag"], float(s)))
    for b in d.get("binary", []):
        if not b.get("tag") or not b.get("en"):
            continue
        # le tag doit battre son contraire ET des descriptions neutres (sinon une image sans vetement
        # passe facilement pour « lingerie » plutot que pour « vetements ordinaires »)
        alts = [b.get("neg") or "something else"] + NEUTRAL
        T = np.stack([text_fn(prompt(b["en"]))] + [text_fn(prompt(a)) for a in alts])
        logits = scale * frame_embs @ T.T
        p = np.exp(logits - logits.max(axis=1, keepdims=True))
        p = (p / p.sum(axis=1, keepdims=True))[:, 0]
        # la majorite des images doit le montrer : moyenne, legerement tiree vers le minimum
        out.append((b["tag"], float(0.7 * p.mean() + 0.3 * np.median(p))))
    return out


def train_tag(pos, neg, iters=300, lr=0.5, l2=1e-3):
    """Régression logistique simple sur les empreintes moyennes. -> (w, b, précision en validation)."""
    X = np.vstack([pos, neg]).astype(np.float32)
    y = np.concatenate([np.ones(len(pos)), np.zeros(len(neg))]).astype(np.float32)
    idx = np.random.default_rng(0).permutation(len(X))
    cut = max(1, int(len(X) * 0.8))
    tr, va = idx[:cut], idx[cut:]
    w = np.zeros(X.shape[1], dtype=np.float32)
    b = 0.0
    sw = np.where(y[tr] > 0, len(tr) / (2 * max(1, y[tr].sum())), len(tr) / (2 * max(1, (1 - y[tr]).sum())))
    for _ in range(iters):
        z = X[tr] @ w * 10 + b
        p = 1 / (1 + np.exp(-z))
        g = (p - y[tr]) * sw
        w -= lr * (X[tr].T @ g * 10 / len(tr) + l2 * w)
        b -= lr * g.mean()
    if len(va):
        pv = 1 / (1 + np.exp(-(X[va] @ w * 10 + b)))
        acc = float(((pv > 0.5) == (y[va] > 0)).mean())
    else:
        acc = 0.0
    return w, b, acc


def predict(w, b, X):
    return 1 / (1 + np.exp(-(X @ w * 10 + b)))


# ----------------------------------------------------------------------------------------
# Images d'une vidéo
# ----------------------------------------------------------------------------------------
def frames(ffmpeg, path, duration, thumb=None, n=3):
    from io import BytesIO
    from PIL import Image
    ims = []
    if thumb and os.path.isfile(thumb):
        try:
            ims.append(Image.open(thumb).convert("RGB"))
        except Exception:
            pass
    if ffmpeg and path and os.path.isfile(path) and duration and duration > 5:
        for k in range(n):
            t = duration * (0.25 + 0.5 * k / max(1, n - 1))
            try:
                r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-threads", "1", "-ss", "%.2f" % t,
                                    "-i", path, "-frames:v", "1", "-vf", "scale=320:-2", "-f", "image2pipe",
                                    "-vcodec", "png", "-"], capture_output=True, timeout=40, creationflags=_FLAGS)
                if r.stdout:
                    ims.append(Image.open(BytesIO(r.stdout)).convert("RGB"))
            except Exception:
                pass
    return ims


def cut_rate(ffmpeg, path, duration):
    """Coupes par seconde : 3 échantillons de 12 s (25 %, 50 %, 75 %), images réduites. On garde le plus élevé."""
    if not (ffmpeg and path and duration and duration > 20):
        return None
    best = None
    for frac in (0.25, 0.5, 0.75):
        t = max(0.0, duration * frac)
        try:
            r = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-threads", "1", "-ss", "%.1f" % t, "-t", "12", "-i", path,
                                "-an", "-vf", "scale=160:-2,select='gt(scene,0.32)',showinfo", "-f", "null", "-"],
                               capture_output=True, text=True, errors="replace", timeout=60, creationflags=_FLAGS)
            v = r.stderr.count("pts_time:") / 12.0
            best = v if best is None else max(best, v)
        except Exception:
            pass
    return best


def pmv_score(ffmpeg, path, duration):
    """Musique continue au rythme très régulier (le plus parlant) + montage (coupes)."""
    from . import beats as B
    try:
        x = B.decode(ffmpeg, path, start=max(0.0, duration * 0.4), dur=30)
        d = B.analyse_signal(x)
    except Exception:
        d = None
    reg = 0.0
    if d and len(d["beats"]) > 8:
        iv = np.diff(d["beats"])
        reg = float(max(0.0, 1.0 - (np.std(iv) / (np.median(iv) + 1e-9)) / 0.12))
    if reg < 0.35:
        return round(0.5 * reg, 3)                  # pas de musique reguliere : ce n'est pas un PMV
    cr = cut_rate(ffmpeg, path, duration) or 0.0
    cut = min(1.0, cr / 0.6)
    return round(0.65 * reg + 0.35 * cut, 3)


# ----------------------------------------------------------------------------------------
# Écriture des propositions
# ----------------------------------------------------------------------------------------
def tag_id(conn, name):
    r = conn.execute("SELECT id FROM tags WHERE lower(name)=lower(?)", (name,)).fetchone()
    if r:
        return r[0]
    conn.execute("INSERT INTO tags(name) VALUES (?)", (name,))
    return conn.execute("SELECT id FROM tags WHERE lower(name)=lower(?)", (name,)).fetchone()[0]


def has_tag(conn, vid, name):
    return conn.execute("SELECT 1 FROM video_tags vt JOIN tags t ON t.id=vt.tag_id WHERE vt.video_id=? "
                        "AND lower(t.name)=lower(?) AND vt.manual>=0", (vid, name)).fetchone() is not None


def removed_by_hand(conn, vid, name):
    return conn.execute("SELECT 1 FROM video_tags vt JOIN tags t ON t.id=vt.tag_id WHERE vt.video_id=? "
                        "AND lower(t.name)=lower(?) AND vt.manual<0", (vid, name)).fetchone() is not None


def apply_tag(conn, vid, name):
    tid = tag_id(conn, name)
    conn.execute("INSERT OR REPLACE INTO video_tags(video_id, tag_id, manual) VALUES (?,?,1)", (vid, tid))


def unapply_tag(conn, vid, name):
    r = conn.execute("SELECT id FROM tags WHERE lower(name)=lower(?)", (name,)).fetchone()
    if r:
        conn.execute("DELETE FROM video_tags WHERE video_id=? AND tag_id=?", (vid, r[0]))


def propose(conn, vid, tag, score, source, auto_at):
    """Enregistre une proposition ; l'applique seule si le score dépasse le seuil automatique."""
    if has_tag(conn, vid, tag) or removed_by_hand(conn, vid, tag):
        return
    r = conn.execute("SELECT state FROM tag_suggest WHERE video_id=? AND tag=?", (vid, tag)).fetchone()
    if r and r[0] != 0:
        return                                       # deja traite (valide, refuse ou automatique)
    state = 0
    if auto_at and score >= auto_at:
        apply_tag(conn, vid, tag)
        state = 2
    conn.execute("INSERT OR REPLACE INTO tag_suggest(video_id, tag, score, source, state, at) VALUES (?,?,?,?,?,?)",
                 (vid, tag, round(score, 3), source, state, time.time()))


# ----------------------------------------------------------------------------------------
# Analyse en tâche de fond
# ----------------------------------------------------------------------------------------
class Worker:
    def __init__(self, main):
        self.main = main
        self.thread = None
        self.stop = False
        self.paused = False
        self.done = 0
        self.total = 0
        self.status = ""
        self.priority = []

    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self):
        if self.running() or not (model_ready() and deps_ready()):
            return False
        self.stop = False
        self.thread = threading.Thread(target=lambda: safe(self.run), daemon=True)
        self.thread.start()
        return True

    def busy_playing(self):
        try:
            return S(self.main.conn, "only_idle", "1") == "1" and len(self.main.ctrl.players) > 0
        except Exception:
            return False

    def run(self):
        from .. import volumes
        from ..config import THUMB_DIR, find_tool
        conn = db.connect()
        conn.execute("PRAGMA busy_timeout=8000")
        ensure_schema(conn)
        ffmpeg = find_tool("ffmpeg")
        clip = Clip()
        d = defs(conn)
        ver = "%d:%s" % (DEFS_VERSION, hash(json.dumps(d, sort_keys=True)) & 0xffffff)
        if S(conn, "defs_ver", "") != ver:          # liste changee : on revoit les videos deja vues
            conn.execute("UPDATE vemb SET done_at=0")
            db.set_setting(conn, "at_defs_ver", ver)
            conn.commit()
        do_pmv = S(conn, "pmv", "1") == "1"
        # disque de la bibliotheque branche : les videos vues seulement par leur vignette sont reprises
        try:
            if any(volumes.resolve(r["serial"], r["rel_root"]) for r in conn.execute("SELECT serial, rel_root FROM sources")):
                conn.execute("UPDATE vemb SET done_at=0 WHERE n<=1")
                conn.commit()
        except Exception:
            pass
        while not self.stop:
            if self.paused or self.busy_playing():
                self.status = "en pause" if self.paused else "en attente (vidéo en cours)"
                time.sleep(3)
                continue
            todo = self.next_batch(conn)
            self.total = conn.execute("SELECT COUNT(*) FROM videos WHERE missing=0 AND kind='video'").fetchone()[0]
            self.done = conn.execute("SELECT COUNT(*) FROM vemb WHERE done_at>0").fetchone()[0]
            if not todo:
                self.status = "apprentissage…"
                safe(self.learn, conn)
                self.status = "terminé"
                return
            auto_at = float(S(conn, "auto", "0.90") or 0) or None
            for r in todo:
                if self.stop or self.paused:
                    break
                try:
                    self.one(conn, r, clip, ffmpeg, d, do_pmv, auto_at)
                except Exception as e:
                    log("tags auto video %s : %r" % (r["id"], e))
                    try:
                        conn.rollback()
                        conn.execute("INSERT OR REPLACE INTO vemb(video_id, emb, n, done_at) VALUES (?,?,0,?)",
                                     (r["id"], b"", time.time()))
                        conn.commit()
                    except Exception:
                        time.sleep(2)

    def one(self, conn, r, clip, ffmpeg, d, do_pmv, auto_at):
        from .. import volumes
        from ..config import THUMB_DIR
        if True:
                vid = r["id"]
                self.status = "analyse"
                root = None
                try:
                    root = volumes.resolve(r["serial"], r["rel_root"])
                except Exception:
                    pass
                path = os.path.join(root, r["rel_path"]) if root and r["rel_path"] else None
                thumb = r["thumb"]
                if thumb and not os.path.isabs(thumb):
                    thumb = str(THUMB_DIR / thumb)
                old = conn.execute("SELECT emb, n FROM vemb WHERE video_id=?", (vid,)).fetchone()
                full = bool(path and os.path.isfile(path))
                if old is not None and old["n"] and (old["n"] >= 3 or not full):
                    E = np.frombuffer(old["emb"], dtype=np.float16).astype(np.float32).reshape(old["n"], -1)
                else:
                    ims = frames(ffmpeg, path, r["duration"], thumb)
                    if not ims:
                        conn.execute("INSERT OR REPLACE INTO vemb(video_id, emb, n, done_at) VALUES (?,?,0,?)",
                                     (vid, b"", time.time()))
                        conn.commit()
                        return
                    E = clip.image(ims)
                pmv = conn.execute("SELECT pmv FROM vemb WHERE video_id=?", (vid,)).fetchone()
                pmv = pmv[0] if pmv is not None else None
                if pmv is None and do_pmv and path and os.path.isfile(path):
                    pmv = pmv_score(ffmpeg, path, r["duration"])
                conn.execute("INSERT OR REPLACE INTO vemb(video_id, emb, n, done_at, pmv) VALUES (?,?,?,?,?)",
                             (vid, E.astype(np.float16).tobytes(), len(E), time.time(), pmv))
                binary = {b["tag"] for b in d.get("binary", [])}
                # une seule image (disque debranche : vignette seule) -> propositions seulement, jamais auto
                auto_here = auto_at if len(E) >= 3 else None
                for tag, s in zero_shot(E, d, clip.text):
                    if s >= (0.60 if tag in binary else 0.55):
                        propose(conn, vid, tag, s, "ia", (auto_here + 0.04) if (auto_here and tag in binary) else auto_here)
                if pmv is not None and pmv >= 0.5:
                    propose(conn, vid, "pmv", pmv, "pmv", (auto_at + 0.05) if auto_at else None)
                conn.commit()
                self.done += 1

    def next_batch(self, conn):
        pr = []
        while self.priority:
            pr.append(self.priority.pop(0))
        q = ("SELECT v.id, v.rel_path, v.thumb, v.duration, s.serial, s.rel_root FROM videos v "
             "LEFT JOIN sources s ON s.id=v.source_id LEFT JOIN vemb e ON e.video_id=v.id "
             "WHERE v.missing=0 AND v.kind='video' AND %s ORDER BY v.id DESC LIMIT 20")
        rows = []
        if pr:
            rows = conn.execute(q % ("v.id IN (%s)" % ",".join("?" * len(pr))), pr).fetchall()
        return rows or conn.execute(q % "(e.video_id IS NULL OR e.done_at=0)").fetchall()

    def learn(self, conn):
        """Option 3 : apprend chaque tag posé sur au moins 6 vidéos et propose-le aux vidéos semblables."""
        rows = conn.execute("SELECT video_id, emb, n FROM vemb WHERE n>0").fetchall()
        if len(rows) < 30:
            return
        ids = np.array([r["video_id"] for r in rows])
        X = normalize(np.stack([np.frombuffer(r["emb"], dtype=np.float16).astype(np.float32)
                                .reshape(r["n"], -1).mean(axis=0) for r in rows]))
        pos_of = {}
        for r in conn.execute("SELECT vt.video_id, t.name FROM video_tags vt JOIN tags t ON t.id=vt.tag_id "
                              "WHERE vt.manual>=0"):
            pos_of.setdefault(r[1], set()).add(r[0])
        refused = {}
        for r in conn.execute("SELECT video_id, tag FROM tag_suggest WHERE state=-1"):
            refused.setdefault(r[1], set()).add(r[0])
        auto_at = float(S(conn, "auto", "0.90") or 0) or None
        rng = np.random.default_rng(1)
        for tag, vids in pos_of.items():
            if self.stop:
                return
            pmask = np.isin(ids, list(vids))
            if pmask.sum() < 6:
                continue
            nmask = ~pmask
            # exemples negatifs : parmi les videos non taguees, celles qui ressemblent le MOINS aux positives
            # (une video non taguee n'est pas forcement « sans » ce tag)
            cen = normalize(X[pmask].mean(axis=0))
            sim = X @ cen
            cand = np.where(nmask)[0]
            cand = cand[sim[cand] <= np.median(sim[cand])]
            if len(cand) < 10:
                continue
            neg_idx = rng.choice(cand, size=min(len(cand), max(60, int(pmask.sum()) * 4)), replace=False)
            # les refus comptent comme exemples negatifs
            ref = np.where(np.isin(ids, list(refused.get(tag, ()))))[0]
            neg_idx = np.unique(np.concatenate([neg_idx, ref]))
            w, b, acc = train_tag(X[pmask], X[neg_idx])
            if acc < 0.80:                       # pas assez fiable pour ce tag
                continue
            p = predict(w, b, X)
            for k in np.where(nmask & (p >= 0.80))[0]:
                propose(conn, int(ids[k]), tag, float(p[k]) * acc, "appris", (auto_at + 0.05) if auto_at else None)
            conn.commit()


WORKER = None


def reset_v2(conn):
    """Premiere version trop genereuse sur les tags oui/non : on retire ce qu'elle a pose seule
    et on refait les propositions (les empreintes deja calculees sont gardees : c'est rapide)."""
    if S(conn, "reset", "") == "2":
        return
    for r in conn.execute("SELECT video_id, tag FROM tag_suggest WHERE state=2").fetchall():
        unapply_tag(conn, r[0], r[1])
    conn.execute("DELETE FROM tag_suggest WHERE state IN (0, 2)")
    conn.execute("UPDATE vemb SET done_at=0")
    db.set_setting(conn, "at_reset", "2")
    conn.commit()


# ----------------------------------------------------------------------------------------
# Téléchargement du modèle
# ----------------------------------------------------------------------------------------
def download(progress=None, cancel=lambda: False):
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    total = sum(mb for _s, _n, mb in MODEL_FILES) * 1024 * 1024
    got = 0
    for src, name, _mb in MODEL_FILES:
        dst = MODEL_DIR / name
        if dst.is_file():
            got += dst.stat().st_size
            continue
        tmp = dst.with_suffix(".part")
        req = urllib.request.Request(HF + src, headers={"User-Agent": "Phoque Al"})
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            while True:
                if cancel():
                    raise RuntimeError("annulé")
                chunk = r.read(1 << 18)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if progress:
                    progress(min(99, int(100 * got / total)))
        os.replace(tmp, dst)
    if progress:
        progress(100)


# ----------------------------------------------------------------------------------------
# Fenêtres
# ----------------------------------------------------------------------------------------
def open_review(main):
    safe(lambda: ReviewDialog(main).exec())


def ReviewDialog(main):
    from PySide6.QtCore import QSize, Qt, QTimer
    from PySide6.QtGui import QIcon, QPixmap
    from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                                   QMessageBox, QProgressDialog, QPushButton, QVBoxLayout, QCheckBox)
    from ..config import THUMB_DIR

    conn = main.conn
    ensure_schema(conn)
    d = QDialog(main)
    d.setWindowTitle("Tags proposés")
    d.resize(1100, 720)
    lay = QVBoxLayout(d)
    head = QLabel()
    head.setWordWrap(True)
    lay.addWidget(head)
    body = QHBoxLayout()
    lay.addLayout(body, 1)
    tags = QListWidget()
    tags.setFixedWidth(260)
    body.addWidget(tags)
    right = QVBoxLayout()
    body.addLayout(right, 1)
    grid = QListWidget()
    grid.setViewMode(QListWidget.IconMode)
    grid.setIconSize(QSize(192, 108))
    grid.setGridSize(QSize(210, 150))
    grid.setResizeMode(QListWidget.Adjust)
    grid.setMovement(QListWidget.Static)
    grid.setSelectionMode(QListWidget.ExtendedSelection)
    right.addWidget(grid, 1)
    acts = QHBoxLayout()
    right.addLayout(acts)
    b_ok = QPushButton("✓  Valider la sélection")
    b_no = QPushButton("✕  Refuser la sélection")
    b_all = QPushButton("Tout sélectionner")
    b_play = QPushButton("▶  Lire")
    for b in (b_all, b_ok, b_no, b_play):
        acts.addWidget(b)
    acts.addStretch(1)
    hint = QLabel("Ctrl / Maj + clic pour sélectionner plusieurs vignettes. Double-clic : lire.")
    hint.setStyleSheet("color:#8b8c99")
    right.addWidget(hint)

    bottom = QHBoxLayout()
    lay.addLayout(bottom)
    run = QPushButton()
    auto = QComboBox()
    choices = (("Ne rien appliquer seul", "0"), ("Appliquer seul au-dessus de 95 %", "0.95"),
               ("Appliquer seul au-dessus de 90 %", "0.90"), ("Appliquer seul au-dessus de 85 %", "0.85"))
    for lab, v in choices:
        auto.addItem(lab, v)
    cur = S(conn, "auto", "0.90")
    vals = [v for _l, v in choices]
    auto.setCurrentIndex(vals.index(cur) if cur in vals else 2)
    auto.currentIndexChanged.connect(lambda _i: db.set_setting(conn, "at_auto", auto.currentData()))
    idle = QCheckBox("Seulement quand aucune vidéo ne joue")
    idle.setChecked(S(conn, "only_idle", "1") == "1")
    idle.toggled.connect(lambda on: db.set_setting(conn, "at_only_idle", "1" if on else "0"))
    undo = QPushButton("Annuler les tags appliqués seuls")
    edit = QPushButton("Liste des tags…")
    for wdg in (run, auto, idle):
        bottom.addWidget(wdg)
    bottom.addStretch(1)
    bottom.addWidget(edit)
    bottom.addWidget(undo)
    close = QPushButton("Fermer")
    bottom.addWidget(close)
    close.clicked.connect(d.accept)

    state = {"tag": None}

    def counts():
        return conn.execute("SELECT tag, COUNT(*) AS n, MAX(score) AS m FROM tag_suggest WHERE state=0 "
                            "GROUP BY tag ORDER BY n DESC").fetchall()

    def fill_tags():
        sel = state["tag"]
        tags.blockSignals(True)
        tags.clear()
        rows = counts()
        for r in rows:
            it = QListWidgetItem("%s   (%d)" % (r["tag"], r["n"]))
            it.setData(Qt.UserRole, r["tag"])
            tags.addItem(it)
        tags.blockSignals(False)
        names = [r["tag"] for r in rows]
        if sel in names:
            tags.setCurrentRow(names.index(sel))
        elif names:
            tags.setCurrentRow(0)
        else:
            state["tag"] = None
            grid.clear()
        refresh_head()

    def refresh_head():
        w = WORKER
        pend = conn.execute("SELECT COUNT(*) FROM tag_suggest WHERE state=0").fetchone()[0]
        autos = conn.execute("SELECT COUNT(*) FROM tag_suggest WHERE state=2").fetchone()[0]
        if not deps_ready():
            txt = "Il manque un composant (onnxruntime) : relance Phoque Al avec lancer_phoqueal.bat sur un PC connecté."
        elif not model_ready():
            txt = "Le modèle de reconnaissance d'images n'est pas encore téléchargé (≈ 155 Mo, une seule fois)."
        elif w and w.running():
            txt = "Analyse : %d / %d vidéos (%s)." % (w.done, w.total, w.status)
        else:
            txt = "Analyse arrêtée."
        head.setText("<b>%d proposition(s) en attente</b> · %d tag(s) appliqué(s) automatiquement.  %s"
                     % (pend, autos, txt))
        if not model_ready():
            run.setText("Télécharger le modèle et analyser")
        else:
            run.setText("Mettre en pause" if (w and w.running() and not w.paused) else "Lancer l'analyse")

    def fill_grid():
        grid.clear()
        tag = state["tag"]
        if not tag:
            return
        rows = conn.execute("SELECT s.video_id, s.score, s.source, v.title, v.thumb FROM tag_suggest s "
                            "JOIN videos v ON v.id=s.video_id WHERE s.state=0 AND s.tag=? "
                            "ORDER BY s.score DESC LIMIT 400", (tag,)).fetchall()
        for i, r in enumerate(rows, 1):
            label = "%d %%%s" % (round(r["score"] * 100), " · appris" if r["source"] == "appris" else "")
            it = QListWidgetItem(label if main.discreet else "%s\n%s" % (label, (r["title"] or "")[:28]))
            it.setData(Qt.UserRole, r["video_id"])
            if not main.discreet and r["thumb"]:
                p = r["thumb"] if os.path.isabs(r["thumb"]) else str(THUMB_DIR / r["thumb"])
                pm = QPixmap(p)
                if not pm.isNull():
                    it.setIcon(QIcon(pm.scaled(192, 108, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)))
            grid.addItem(it)

    def on_tag(row):
        it = tags.item(row)
        state["tag"] = it.data(Qt.UserRole) if it else None
        fill_grid()

    tags.currentRowChanged.connect(on_tag)

    def chosen():
        return [it.data(Qt.UserRole) for it in grid.selectedItems()]

    def decide(ok):
        tag = state["tag"]
        vids = chosen()
        if not tag or not vids:
            return
        for v in vids:
            if ok:
                apply_tag(conn, v, tag)
            conn.execute("UPDATE tag_suggest SET state=? WHERE video_id=? AND tag=?", (1 if ok else -1, v, tag))
        conn.commit()
        main.status.setText("%s : %d vidéo(s) %s" % (tag, len(vids), "taguée(s)" if ok else "refusée(s)"))
        fill_tags()

    b_ok.clicked.connect(lambda: decide(True))
    b_no.clicked.connect(lambda: decide(False))
    b_all.clicked.connect(grid.selectAll)

    def play(*_):
        vids = chosen()
        if vids:
            choice = main.ask_screen()
            if choice != -1:
                main.ctrl.play(vids[0], choice)
    b_play.clicked.connect(play)
    grid.itemDoubleClicked.connect(play)

    def do_undo():
        n = conn.execute("SELECT COUNT(*) FROM tag_suggest WHERE state=2").fetchone()[0]
        if not n:
            return
        if QMessageBox.question(d, "Tags automatiques", "Retirer les %d tag(s) appliqués automatiquement ?\n"
                                "Ils redeviennent des propositions à valider." % n) != QMessageBox.Yes:
            return
        for r in conn.execute("SELECT video_id, tag FROM tag_suggest WHERE state=2").fetchall():
            unapply_tag(conn, r[0], r[1])
        conn.execute("UPDATE tag_suggest SET state=0 WHERE state=2")
        conn.commit()
        fill_tags()
    undo.clicked.connect(do_undo)

    def toggle_run():
        global WORKER
        if not deps_ready():
            QMessageBox.information(d, "Tags proposés", "Relance Phoque Al avec lancer_phoqueal.bat (PC connecté à internet) : "
                                    "le composant d'IA s'installe tout seul.")
            return
        if not model_ready():
            dlg = QProgressDialog("Téléchargement du modèle (≈ 155 Mo)…", "Annuler", 0, 100, d)
            dlg.setWindowModality(Qt.WindowModal)
            box = {"p": 0, "err": None, "end": False}

            def job():
                try:
                    download(lambda p: box.__setitem__("p", p), lambda: dlg.wasCanceled())
                except Exception as e:
                    box["err"] = str(e)
                box["end"] = True
            threading.Thread(target=job, daemon=True).start()
            while not box["end"]:
                dlg.setValue(box["p"])
                from PySide6.QtWidgets import QApplication
                QApplication.processEvents()
                time.sleep(0.05)
            dlg.close()
            if box["err"]:
                QMessageBox.warning(d, "Tags proposés", "Téléchargement impossible : %s" % box["err"])
                return
        db.set_setting(conn, "at_enabled", "1")
        if WORKER is None:
            WORKER = Worker(main)
        if WORKER.running():
            WORKER.paused = not WORKER.paused
        else:
            WORKER.paused = False
            WORKER.start()
        refresh_head()
    run.clicked.connect(toggle_run)
    edit.clicked.connect(lambda: (DefsDialog(main).exec(), refresh_head()))

    timer = QTimer(d)
    timer.timeout.connect(lambda: (refresh_head(), fill_tags() if tags.count() == 0 else None))
    timer.start(2000)
    fill_tags()
    return d


def DefsDialog(main):
    """Liste des tags à reconnaître (nom en français, description en anglais pour l'IA)."""
    from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton,
                                   QVBoxLayout)
    d = QDialog(main)
    d.setWindowTitle("Liste des tags à reconnaître")
    d.resize(760, 640)
    lay = QVBoxLayout(d)
    info = QLabel("Une ligne par tag :  <b>Nom du tag = description en anglais</b>  (l'IA comprend l'anglais).<br>"
                  "Les lignes commençant par <b>#</b> ouvrent un groupe de choix exclusifs (un seul retenu par vidéo). "
                  "Sans groupe (après <b>## Seuls</b>) : oui / non, avec « / description contraire » en option.")
    info.setWordWrap(True)
    lay.addWidget(info)
    ed = QPlainTextEdit()
    ed.setPlainText(to_text(defs(main.conn)))
    lay.addWidget(ed, 1)
    row = QHBoxLayout()
    reset = QPushButton("Revenir à la liste par défaut")
    ok = QPushButton("Enregistrer")
    cancel = QPushButton("Annuler")
    row.addWidget(reset); row.addStretch(1); row.addWidget(cancel); row.addWidget(ok)
    lay.addLayout(row)
    reset.clicked.connect(lambda: ed.setPlainText(to_text(DEFAULT_DEFS)))
    cancel.clicked.connect(d.reject)

    def save():
        try:
            dd = from_text(ed.toPlainText())
        except ValueError as e:
            QMessageBox.warning(d, "Liste des tags", str(e))
            return
        db.set_setting(main.conn, "at_defs", json.dumps(dd, ensure_ascii=False))
        d.accept()
    ok.clicked.connect(save)
    return d


def to_text(d):
    out = []
    for g in d.get("groups", []):
        out.append("# " + g["name"])
        for o in g["options"]:
            out.append("%s = %s" % (o["tag"] or "(aucun)", o["en"]))
        out.append("")
    out.append("## Seuls")
    for b in d.get("binary", []):
        out.append("%s = %s%s" % (b["tag"], b["en"], (" / " + b["neg"]) if b.get("neg") else ""))
    return "\n".join(out)


def from_text(text):
    d = {"groups": [], "binary": []}
    cur = None
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if line.startswith("##"):
            cur = "binary"
            continue
        if line.startswith("#"):
            cur = {"name": line.lstrip("#").strip() or "Groupe", "options": []}
            d["groups"].append(cur)
            continue
        if "=" not in line:
            raise ValueError("Ligne %d : il manque « = » (Nom = description en anglais)." % n)
        name, en = [x.strip() for x in line.split("=", 1)]
        if not en:
            raise ValueError("Ligne %d : description vide." % n)
        if cur == "binary":
            pos, _s, neg = en.partition(" / ")
            d["binary"].append({"tag": name, "en": pos.strip(), "neg": neg.strip() or None})
        elif cur is None:
            raise ValueError("Ligne %d : commence par un « # Groupe » ou « ## Seuls »." % n)
        else:
            cur["options"].append({"tag": None if name in ("(aucun)", "") else name, "en": en})
    for g in d["groups"]:
        if len(g["options"]) < 2:
            raise ValueError("Le groupe « %s » doit avoir au moins 2 choix." % g["name"])
    return d


# ----------------------------------------------------------------------------------------
# Intégration
# ----------------------------------------------------------------------------------------
def menu(main, m):
    m.addAction("Tags proposés (reconnaissance automatique)…").triggered.connect(lambda: open_review(main))


def card_menu(main, menu_, vids):
    vids = [v for v in vids if isinstance(v, int) and v > 0]
    if not vids or not (model_ready() and deps_ready()):
        return {}
    a = menu_.addAction("Proposer des tags maintenant")

    def go(v=list(vids)):
        global WORKER
        try:
            main.conn.execute("UPDATE vemb SET done_at=0 WHERE video_id IN (%s)" % ",".join("?" * len(v)), v)
            main.conn.commit()
        except Exception:
            pass
        if WORKER is None:
            WORKER = Worker(main)
        WORKER.priority.extend(v)
        WORKER.paused = False
        WORKER.start()
        main.status.setText("Tags : analyse de %d vidéo(s)… (⋯ → Tags proposés)" % len(v))
    return {a: go}


def install(main):
    global WORKER
    try:
        ensure_schema(main.conn)
    except Exception as e:
        log("autotags schema : %r" % e)
        return
    WORKER = Worker(main)
    safe(reset_v2, main.conn)
    # reprend l'analyse au demarrage si elle avait ete lancee une fois
    if S(main.conn, "enabled", "0") == "1":
        from PySide6.QtCore import QTimer
        QTimer.singleShot(20000, WORKER.start)
