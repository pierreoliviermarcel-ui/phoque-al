"""Telecommande sur telephone / tablette : petit serveur web sur le reseau local.

- Aucune application a installer : le telephone ouvre une page web (QR code a scanner).
- Protection : code PIN (6 chiffres) ou lien d'appairage du QR code ; 5 essais rates = 1 minute de blocage.
- Le serveur tourne dans un fil a part ; toutes les commandes sont executees dans le fil Qt (pump).
- Accessible uniquement depuis le reseau local (regle de pare-feu « sous-reseau local »).
"""
import hmac
import io
import json
import os
import queue
import secrets
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import db
from .webremote_page import ICON_SVG, MANIFEST, PAGE

DEFAULT_PORT = 8765
COOKIE = "focal_t"


# ----------------------------------------------------------------------------------------
# Outils
# ----------------------------------------------------------------------------------------
def _adapters():
    """[(ip, nom de la carte)] sous Windows (vide ailleurs ou en cas d'erreur)."""
    if sys.platform != "win32":
        return []
    try:
        import subprocess
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-NetIPAddress -AddressFamily IPv4 | ForEach-Object { $_.IPAddress + '|' + $_.InterfaceAlias }"],
                             capture_output=True, text=True, timeout=8, creationflags=0x08000000).stdout
        return [tuple(l.strip().split("|", 1)) for l in out.splitlines() if "|" in l]
    except Exception:
        return []


VIRTUAL = ("vethernet", "virtual", "vmware", "vbox", "virtualbox", "hyper-v", "wsl", "loopback", "tailscale",
           "zerotier", "vpn", "tap-", "tun", "bluetooth", "docker", "npcap", "teredo")
_IPCACHE = {"t": 0, "v": []}


def local_ips_named():
    """[(ip, nom)] du PC sur le réseau local, la plus probable en premier.
    En tête : la carte qui mène à internet (box ou partage de connexion du téléphone) ;
    en dernier : les cartes virtuelles (Hyper-V, WSL, VirtualBox, VPN…)."""
    import time as _t
    if _t.time() - _IPCACHE["t"] < 20 and _IPCACHE["v"]:
        return list(_IPCACHE["v"])
    names = {ip: name for ip, name in _adapters()}
    first = []
    for probe in ("8.8.8.8", "192.168.255.255", "10.255.255.255", "172.31.255.255"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((probe, 1))                  # aucun paquet envoyé : sert à trouver l'interface
            first.append(s.getsockname()[0])
            s.close()
        except OSError:
            pass
    others = list(names)
    try:
        others += [i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    except OSError:
        pass

    def virtual(ip):
        n = (names.get(ip) or "").lower()
        return any(w in n for w in VIRTUAL)

    def rank(ip):
        if virtual(ip):
            return 9
        if ip.startswith("169.254."):
            return 8                               # câble direct sans réseau (satellite) : gardé mais en dernier
        if ip == (first[0] if first else None):
            return 0
        if ip.startswith("192.168."):
            return 1
        if ip.startswith("10."):
            return 2
        return 3
    out = []
    for ip in first + others:
        if ip.startswith(("127.", "0.")) or ip in [o for o, _n in out]:
            continue
        out.append((ip, names.get(ip, "")))
    out.sort(key=lambda x: rank(x[0]))
    out = out or [("127.0.0.1", "")]
    _IPCACHE.update(t=_t.time(), v=out)
    return list(out)


def local_ips():
    """Adresses IPv4 du PC sur le réseau local (la plus probable en premier)."""
    return [ip for ip, _n in local_ips_named()]


def flag(p, which):
    if getattr(p, "is_mosaic", False):
        return p.config["end"] == "replay" if which == "loop" else p.config["order"] == "random"
    return p.loop if which == "loop" else p.random_mode


def toggle_flag(p, which, value=None):
    value = (not flag(p, which)) if value is None else value
    if getattr(p, "is_mosaic", False):
        if which == "loop":
            p.config["end"] = "replay" if value else "next"
            p.osd("Fin de vidéo : rejouer" if value else "Fin de vidéo : suivante", 1500)
        else:
            p.config["order"] = "random" if value else "ordered"
            p.build_pool()
            p.osd("Ordre aléatoire" if value else "Dans l'ordre", 1500)
    elif which == "loop":
        p.loop = value
        p.osd("Boucle activée" if value else "Boucle désactivée")
    else:
        p.random_mode = value
        p.osd("Aléatoire activé" if value else "Aléatoire désactivé")


def set_volume(p, v):
    v = max(0, min(150, int(v)))
    if getattr(p, "is_mosaic", False):
        for t in p.tiles:
            t.volume = v
            t.player.audio_set_volume(v)
    else:
        p.volume = v
        p.player.audio_set_volume(v)


# ----------------------------------------------------------------------------------------
# Pont entre le serveur (fil web) et Phoque Al (fil Qt)
# ----------------------------------------------------------------------------------------
class Bridge:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = {"screens": [], "music": {}, "discreet": False}
        self.actions = queue.Queue()
        self.last_request = 0.0
        self.pin = ""
        self.key = ""
        self.tokens = set()
        self.fails = {}                # ip -> [nb_essais, bloque_jusqu_a]
        self.discreet = False
        self.local_screens = 0

    def valid_token(self, token):
        if not token:
            return False
        with self.lock:
            return any(hmac.compare_digest(token, t) for t in self.tokens)

    def new_token(self):
        token = secrets.token_urlsafe(24)
        with self.lock:
            self.tokens.add(token)
        self.actions.put({"cmd": "_save_tokens"})
        return token

    def check_pin(self, ip, pin):
        now = time.time()
        with self.lock:
            count, until = self.fails.get(ip, [0, 0])
            if until > now:
                return None                       # bloque
            if pin and hmac.compare_digest(str(pin), self.pin):
                self.fails.pop(ip, None)
                return True
            count += 1
            self.fails[ip] = [count, now + 60 if count >= 5 else 0]
            if count >= 5:
                self.fails[ip][0] = 0
            return False


def make_handler(bridge):
    local = threading.local()

    def conn():
        c = getattr(local, "conn", None)
        if c is None:
            c = local.conn = db.connect()
        return c

    class Handler(BaseHTTPRequestHandler):
        server_version = "Phoque Al"
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        # --- reponses ----------------------------------------------------------------
        def send(self, code, body, ctype="application/json; charset=utf-8", headers=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False)
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def cookie_header(self, token):
            return {"Set-Cookie": "%s=%s; Path=/; Max-Age=31536000; HttpOnly; SameSite=Strict" % (COOKIE, token)}

        def token(self):
            tok = self.headers.get("X-PhoqueAl-Token", "") or (parse_qs(urlparse(self.path).query).get("t") or [""])[0]
            if not tok:
                for part in self.headers.get("Cookie", "").split(";"):
                    k, _, v = part.strip().partition("=")
                    if k == COOKIE:
                        tok = v
            return tok

        def authed(self):
            return bridge.valid_token(self.token())

        def body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > 65536:
                return {}
            try:
                return json.loads(self.rfile.read(n).decode("utf-8"))
            except ValueError:
                return {}

        # --- GET ---------------------------------------------------------------------
        def do_GET(self):
            bridge.last_request = time.time()
            url = urlparse(self.path)
            path = url.path
            if path in ("/", "/index.html"):
                q = parse_qs(url.query)
                key = (q.get("k") or [""])[0]
                if key and bridge.key and hmac.compare_digest(key, bridge.key):
                    # lien d'appairage du QR code : connexion directe, puis on retire la cle de l'adresse
                    tok = bridge.new_token()
                    h = self.cookie_header(tok)
                    h["Location"] = "/"
                    return self.send(302, b"", "text/plain", h)
                return self.send(200, PAGE, "text/html; charset=utf-8")
            if path == "/manifest.json":
                return self.send(200, MANIFEST, "application/manifest+json")
            if path == "/icon.svg":
                return self.send(200, ICON_SVG, "image/svg+xml")
            if not path.startswith("/api/"):
                return self.send(404, {"error": "introuvable"})
            if not self.authed():
                return self.send(401, {"error": "code requis"})
            if path == "/api/state":
                with bridge.lock:
                    return self.send(200, bridge.state)
            if path == "/api/search":
                text = (parse_qs(url.query).get("q") or [""])[0]
                return self.send(200, self.search(text))
            if path == "/api/library":
                return self.send(200, self.library())
            if path.startswith("/api/thumb/"):
                return self.thumb(path.rsplit("/", 1)[-1])
            if path == "/api/catalog":                       # satellite : sa bibliotheque
                return self.send(200, self.catalog((parse_qs(url.query).get("rev") or [""])[0]))
            if path.startswith("/api/stream/"):              # diffusion d'une video par le reseau
                return self.stream(path.rsplit("/", 1)[-1])
            return self.send(404, {"error": "introuvable"})

        def do_HEAD(self):
            self.do_GET()

        # --- POST --------------------------------------------------------------------
        def do_POST(self):
            bridge.last_request = time.time()
            path = urlparse(self.path).path
            data = self.body()
            if path == "/api/login":
                ok = bridge.check_pin(self.client_address[0], str(data.get("pin", "")).strip())
                if ok is None:
                    return self.send(429, {"error": "Trop d'essais : attends une minute."})
                if not ok:
                    return self.send(403, {"error": "Code incorrect"})
                tok = bridge.new_token()
                return self.send(200, {"ok": True, "token": tok}, headers=self.cookie_header(tok))
            if not self.authed():
                return self.send(401, {"error": "code requis"})
            if path == "/api/action":
                cmd = str(data.get("cmd", ""))
                if not cmd or cmd.startswith("_"):
                    return self.send(400, {"error": "commande inconnue"})
                bridge.actions.put(data)
                return self.send(200, {"ok": True})
            if path == "/api/logout":
                tok = self.token()
                with bridge.lock:
                    bridge.tokens.discard(tok)
                bridge.actions.put({"cmd": "_save_tokens"})
                return self.send(200, {"ok": True})
            return self.send(404, {"error": "introuvable"})

        # --- donnees de la bibliotheque (lecture seule, connexion propre au fil) ------------
        def item(self, r, n=None):
            hide = bridge.discreet
            return {"id": r["id"], "title": ("Vidéo %d" % n) if hide else r["title"],
                    "sub": "" if hide else r["category"], "kind": r["kind"],
                    "thumb": bool(r["thumb"]) and not hide, "liked": bool(r["liked"]) if "liked" in r.keys() else False,
                    "duration": r["duration"] or 0}

        def search(self, text):
            c = conn()
            rows = db.search(c, text, 60) if text.strip() else db.recent(c, 30)
            return {"items": [self.item(r, i) for i, r in enumerate(rows, 1)]}

        def library(self):
            c = conn()
            hide = bridge.discreet
            pls = [{"id": p["id"], "name": ("Playlist %d" % i) if hide else p["name"], "n": p["n"],
                    "likes": p["system"] == "likes" if "system" in p.keys() else False}
                   for i, p in enumerate(db.list_playlists(c), 1)]
            themes = [{"id": t["category"], "name": ("Collection %d" % i) if hide else t["category"], "n": t["n"]}
                      for i, t in enumerate(db.categories(c), 1)]
            soirees = []
            try:
                for i, r in enumerate(c.execute("SELECT id, name, data FROM soirees ORDER BY updated_at DESC"), 1):
                    try:
                        k = len(json.loads(r["data"]).get("screens", []))
                    except Exception:
                        k = 0
                    soirees.append({"id": r["id"], "name": ("Soirée %d" % i) if hide else r["name"], "n": k})
            except Exception:
                pass
            return {"playlists": pls, "themes": themes, "soirees": soirees}

        def thumb(self, vid):
            if bridge.discreet:
                return self.send(404, {"error": "masqué"})
            try:
                row = conn().execute("SELECT thumb FROM videos WHERE id=?", (int(vid),)).fetchone()
            except (ValueError, TypeError):
                row = None
            if not row or not row["thumb"] or not os.path.isfile(row["thumb"]):
                return self.send(404, {"error": "pas de miniature"})
            with open(row["thumb"], "rb") as f:
                data = f.read()
            self.send(200, data, "image/jpeg", {"Cache-Control": "private, max-age=86400"})

        # --- satellite : bibliotheque et diffusion -------------------------------------------
        def catalog(self, rev):
            c = conn()
            st = c.execute("SELECT COUNT(*) AS n, MAX(id) AS m, MAX(added_at) AS a FROM videos "
                           "WHERE missing=0 AND kind='video'").fetchone()
            current = "%s:%s:%s" % (st["n"], st["m"], st["a"])
            info = {"name": socket.gethostname(), "screens": bridge.local_screens, "rev": current}
            if rev and rev == current:
                return dict(info, same=True)
            rows = c.execute("SELECT id, title, category, duration, size, rel_path, thumb FROM videos "
                             "WHERE missing=0 AND kind='video'").fetchall()
            info["videos"] = [{"id": r["id"], "title": r["title"], "category": r["category"],
                               "duration": r["duration"] or 0, "size": r["size"] or 0,
                               "name": os.path.basename(r["rel_path"]), "thumb": bool(r["thumb"])} for r in rows]
            return info

        def stream(self, vid):
            from . import photos, volumes
            try:
                r = conn().execute("SELECT v.id, v.rel_path, v.archive, s.serial, s.rel_root FROM videos v "
                                   "JOIN sources s ON s.id = v.source_id WHERE v.id=?", (int(vid),)).fetchone()
            except (ValueError, TypeError):
                r = None
            root = volumes.resolve(r["serial"], r["rel_root"]) if r else None
            if not root:
                return self.send(404, {"error": "introuvable"})
            if r["archive"]:
                path = photos.extract(r["id"], os.path.join(root, r["archive"]), photos.split_rel(r["rel_path"])[1])
            else:
                path = os.path.join(root, r["rel_path"])
            if not os.path.isfile(path):
                return self.send(404, {"error": "introuvable"})
            size = os.path.getsize(path)
            start, end, code = 0, size - 1, 200
            rng = self.headers.get("Range", "")
            if rng.startswith("bytes="):
                a, _, b = rng[6:].split(",")[0].strip().partition("-")
                try:
                    if a:
                        start, end = int(a), (int(b) if b else size - 1)
                    else:
                        start, end = size - int(b), size - 1
                except ValueError:
                    start, end = 0, size - 1
                start, end, code = max(0, start), min(end, size - 1), 206
                if start > end:
                    return self.send(416, b"", "text/plain", {"Content-Range": "bytes */%d" % size})
            self.send_response(code)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if code == 206:
                self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
            self.end_headers()
            if self.command == "HEAD":
                return
            try:
                with open(path, "rb") as f:
                    f.seek(start)
                    left = end - start + 1
                    while left > 0:
                        chunk = f.read(min(1024 * 1024, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass

    return Handler


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


# ----------------------------------------------------------------------------------------
# Cote Phoque Al (fil Qt)
# ----------------------------------------------------------------------------------------
try:
    from PySide6.QtCore import QObject, QTimer
except ImportError:          # tests sans Qt
    QObject = object
    QTimer = None


class WebRemote(QObject):
    def __init__(self, main):
        super().__init__(main) if QObject is not object else super().__init__()
        self.main = main
        self.conn = main.conn
        self.bridge = Bridge()
        self.server = None
        self.thread = None
        self.error = ""
        self.load_secrets()
        if QTimer is not None:
            self.pump = QTimer(self)
            self.pump.setInterval(40)
            self.pump.timeout.connect(self.drain)
            self.snap = QTimer(self)
            self.snap.setInterval(300)
            self.snap.timeout.connect(self.refresh_if_watched)

    # --- secrets -------------------------------------------------------------------------
    def load_secrets(self):
        g = lambda k: db.get_setting(self.conn, k)
        pin, key = g("phone_pin"), g("phone_key")
        if not pin:
            pin = "%06d" % secrets.randbelow(1000000)
            db.set_setting(self.conn, "phone_pin", pin)
        if not key:
            key = secrets.token_urlsafe(18)
            db.set_setting(self.conn, "phone_key", key)
        try:
            tokens = set(json.loads(g("phone_tokens") or "[]"))
        except ValueError:
            tokens = set()
        with self.bridge.lock:
            self.bridge.pin, self.bridge.key, self.bridge.tokens = pin, key, tokens

    def regenerate(self):
        """Nouveau code et nouveau QR : tous les telephones deja connectes sont deconnectes."""
        for k in ("phone_pin", "phone_key", "phone_tokens"):
            db.set_setting(self.conn, k, None)
        self.load_secrets()

    def save_tokens(self):
        with self.bridge.lock:
            tokens = list(self.bridge.tokens)[-20:]
            self.bridge.tokens = set(tokens)
        db.set_setting(self.conn, "phone_tokens", json.dumps(tokens))

    @property
    def pin(self):
        return self.bridge.pin

    @property
    def port(self):
        try:
            return int(db.get_setting(self.conn, "phone_port", str(DEFAULT_PORT)))
        except ValueError:
            return DEFAULT_PORT

    def url(self, ip, pair=False):
        base = "http://%s:%d/" % (ip, self.port)
        return base + ("?k=" + self.bridge.key if pair else "")

    # --- marche / arret --------------------------------------------------------------------
    def running(self):
        return self.server is not None

    def start(self):
        if self.server is not None:
            return True
        try:
            self.server = Server(("0.0.0.0", self.port), make_handler(self.bridge))
        except OSError as e:
            self.server = None
            self.error = "Le port %d est déjà utilisé ou bloqué (%s)." % (self.port, e)
            return False
        self.error = ""
        self.thread = threading.Thread(target=self.server.serve_forever, name="focal-web", daemon=True)
        self.thread.start()
        try:
            from .network import DiscoveryResponder
            self.responder = DiscoveryResponder(lambda: {"name": socket.gethostname(), "port": self.port,
                                                         "screens": self.bridge.local_screens})
            self.responder.start()
        except Exception:
            self.responder = None
        self.refresh()
        if QTimer is not None:
            self.pump.start()
            self.snap.start()
        return True

    def stop(self):
        if self.server is None:
            return
        if QTimer is not None:
            self.pump.stop()
            self.snap.stop()
        if getattr(self, "responder", None) is not None:
            self.responder.stop()
            self.responder = None
        srv, self.server = self.server, None
        threading.Thread(target=lambda: (srv.shutdown(), srv.server_close()), daemon=True).start()

    # --- etat publie -------------------------------------------------------------------------
    def refresh_if_watched(self):
        if time.time() - self.bridge.last_request < 8:
            self.refresh()

    def refresh(self):
        try:
            state = self.snapshot()
        except Exception as e:          # ne jamais faire tomber Phoque Al pour la telecommande
            state = {"screens": [], "music": {}, "discreet": True, "error": str(e)}
        with self.bridge.lock:
            self.bridge.state = state
            self.bridge.discreet = state.get("discreet", False)

    def snapshot(self):
        from . import view
        m = self.main
        ctrl = m.ctrl
        discreet = bool(m.discreet)
        screens = []
        self.bridge.local_screens = len(ctrl.screens())
        for n in (ctrl.all_numbers() if hasattr(ctrl, "all_numbers") else range(1, len(ctrl.screens()) + 1)):
            p = ctrl.players.get(n)
            if p is None:
                screens.append({"n": n, "busy": False})
                continue
            mosaic = getattr(p, "is_mosaic", False)
            owner = p.selected if mosaic else p
            mp = owner.player
            length = mp.get_length() or 0
            t = mp.get_time() or 0
            if mosaic:
                title = "Mosaïque" if discreet else "Mosaïque · %s" % p.config.get("name", "")
                sub = "" if discreet else "case sélectionnée : %s" % (owner.title or "")
            else:
                title = "Lecture en cours" if discreet else (p.display_title() if hasattr(p, "display_title") else p.title)
                sub = ""
            screens.append({
                "n": n, "busy": True, "mosaic": mosaic, "title": title, "sub": sub,
                "liked": bool(owner.video_id) and db.is_liked(m.conn, owner.video_id),
                "playing": bool(p.is_playing()), "loop": bool(flag(p, "loop")), "random": bool(flag(p, "random")),
                "muted": bool(p.muted), "volume": int(p.volume),
                "time": max(0, t) / 1000.0, "length": (length / 1000.0) if length > 0 else 0,
                "view": view.LABELS.get(p.view_mode, p.view_mode), "view_key": p.view_mode,
                "remote": bool(getattr(p, "is_remote", False)),
            })
        mu = m.music
        music = {
            "ready": bool(mu.files), "playing": bool(mu.is_playing()), "shuffle": bool(mu.shuffle),
            "montage": bool(m.montage_btn.isChecked()), "volume": int(mu.volume),
            "track": ("" if discreet else (mu.current_name() if mu.files else "")),
        }
        return {"screens": screens, "music": music, "discreet": discreet, "t": time.time()}

    # --- execution des commandes (fil Qt) -------------------------------------------------------
    def drain(self):
        done = False
        while True:
            try:
                a = self.bridge.actions.get_nowait()
            except queue.Empty:
                break
            if a.get("cmd") == "_save_tokens":
                self.save_tokens()
                continue
            try:
                self.run(a)
            except Exception as e:
                self.main.status.setText("Télécommande téléphone : %s" % e)
            done = True
        if done:
            self.refresh()

    def run(self, a):
        m = self.main
        ctrl = m.ctrl
        cmd = a.get("cmd")
        n = int(a.get("screen") or 0)
        v = a.get("value")
        p = ctrl.players.get(n) if n else None
        targets = list(ctrl.players.values()) if n == 0 else ([p] if p is not None else [])

        def each(fn):
            for w in targets:
                fn(w)

        if cmd == "pause":
            ctrl.pause_all() if n == 0 else each(lambda w: w.toggle_pause())
        elif cmd in ("next", "prev"):
            step = 1 if cmd == "next" else -1
            for w in targets:
                if getattr(w, "is_mosaic", False):
                    w.next_all() if step == 1 else w.osd("Précédent : non disponible en mosaïque")
                else:
                    ctrl.queue_step(w, step, manual=True)
        elif cmd == "seek":
            each(lambda w: w.seek(int(v or 0)))
        elif cmd == "seek_to" and p is not None:
            owner = p.selected if getattr(p, "is_mosaic", False) else p
            length = owner.player.get_length()
            if length and length > 0:
                t = int(length * max(0.0, min(1.0, float(v))))
                owner.player.set_time(min(t, length - 500))
        elif cmd in ("loop", "random"):
            if n == 0:
                if targets:
                    value = not all(flag(w, cmd) for w in targets)
                    each(lambda w: toggle_flag(w, cmd, value))
            else:
                each(lambda w: toggle_flag(w, cmd))
        elif cmd == "mute":
            ctrl.mute_all() if n == 0 else each(lambda w: w.toggle_mute())
        elif cmd == "volume":
            each(lambda w: set_volume(w, v))
        elif cmd == "view":
            ctrl.cycle_view_all() if n == 0 else each(lambda w: w.cycle_view())
        elif cmd == "stop":
            ctrl.close_all() if n == 0 else each(lambda w: w.close())
        elif cmd == "like" and p is not None:
            ctrl.toggle_like(p)
        elif cmd == "rotate":
            ctrl.rotate_screens(1 if int(v or 1) >= 0 else -1)
        elif cmd == "panic":
            ctrl.panic()
        elif cmd == "home":
            ctrl.focus(0)
        elif cmd == "identify":
            ctrl.identify()
        elif cmd == "focus" and n:
            ctrl.focus(n)
        elif cmd == "discreet":
            m.set_discreet(not m.discreet)
        elif cmd == "music_toggle":
            m.music.toggle()
        elif cmd == "music_next":
            m.music.next()
        elif cmd == "music_prev":
            m.music.previous()
        elif cmd == "music_shuffle":
            m.music_shuffle.toggle()
        elif cmd == "montage":
            m.montage_btn.toggle()
        elif cmd == "music_volume":
            vol = max(0, min(100, int(v or 0)))
            m.music.set_volume(vol)
            m.music_volume.blockSignals(True)
            m.music_volume.setValue(vol)
            m.music_volume.blockSignals(False)
        elif cmd == "play_url":                                  # ordre du PC principal
            ctrl.play_url(str(v), n, str(a.get("title") or ""), float(a.get("start") or 0))
        elif cmd == "set_pause":
            each(lambda w: w.set_pause(bool(v)))
        elif cmd == "set_mute":
            each(lambda w: w.set_mute(bool(v)))
        elif cmd == "set_view":
            each(lambda w: w.set_view(str(v)))
        elif cmd == "set_loop":
            each(lambda w: toggle_flag(w, "loop", bool(v)))
        elif cmd == "set_random":
            each(lambda w: toggle_flag(w, "random", bool(v)))
        elif cmd == "osd":
            each(lambda w: w.osd(str(v)))
        elif cmd == "panic_set":
            if bool(ctrl.hidden) != bool(v):
                ctrl.panic()
        elif cmd == "blur":
            for w in targets:
                o = w.selected if getattr(w, "is_mosaic", False) else w
                if hasattr(o, "toggle_blur"):
                    o.toggle_blur()
                elif hasattr(o, "set_blur"):
                    o.set_blur(not getattr(o, "_b_on", True))
        elif cmd in ("moment", "rate", "scene"):
            try:
                from .extras import moments, ratings, scenes
            except Exception:
                m.status.setText("Fonctions avancées non installées")
                return
            for w in targets:
                if cmd == "moment":
                    moments.mark(w)
                elif cmd == "rate":
                    ratings.rate_player(w, max(0, min(5, int(v or 0))))
                else:
                    scenes.jump(w, 1 if int(v or 1) >= 0 else -1)
        elif cmd == "seance":
            try:
                from .extras import seance
                seance.remote(m, a.get("sub") or "start")
            except Exception as e:
                m.status.setText("Séance : %s" % e)
        elif cmd == "rythme":
            try:
                from .extras import rythme
                rythme.remote(a.get("sub") or "toggle", v)
            except Exception as e:
                m.status.setText("Frise : %s" % e)
        elif cmd == "soiree":
            try:
                from .extras import soirees
                soirees.load(m, int(v))
            except Exception as e:
                m.status.setText("Soirée : %s" % e)
        elif cmd == "play":
            ctrl.play(int(v), n)
        elif cmd == "play_list":
            self.play_list(a, n)

    def play_list(self, a, n):
        """Lance une playlist ou un theme : sur un ecran, en aleatoire, ou reparti sur tous les ecrans."""
        import random
        m = self.main
        kind, ident = a.get("kind"), a.get("id")
        if kind == "playlist":
            ids = [r["id"] for r in db.playlist_videos(m.conn, int(ident))]
            pl = db.get_playlist(m.conn, int(ident))
            name, loop = (pl["name"] if pl else "playlist"), bool(pl and pl["loop"])
        else:
            ids = db.category_video_ids(m.conn, str(ident))
            name, loop = str(ident), db.get_setting(m.conn, "theme_loop:" + str(ident)) == "1"
        if not ids:
            return
        if a.get("shuffle"):
            random.shuffle(ids)
        if a.get("multi"):
            m.ctrl.play_queue_multi(ids, name, loop)
        else:
            m.ctrl.play_queue(ids, n, name, 0, loop)


# ----------------------------------------------------------------------------------------
# Fenetre « Telephone » (QR code, code PIN, pare-feu)
# ----------------------------------------------------------------------------------------
def qr_pixmap(text, size=260):
    """QR code dessine directement avec Qt (sans passer par une image PIL)."""
    import qrcode
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QPainter, QPixmap
    qr = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(text)
    qr.make(fit=True)
    m = qr.get_matrix()
    n = len(m)
    cell = max(1, size // n)
    side = cell * n
    pm = QPixmap(side, side)
    pm.fill(QColor("white"))
    p = QPainter(pm)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("black"))
    for y, row in enumerate(m):
        for x, v in enumerate(row):
            if v:
                p.drawRect(QRectF(x * cell, y * cell, cell, cell))
    p.end()
    return pm


def log_error(text):
    try:
        from .config import DATA_DIR
        with open(DATA_DIR / "telephone_erreurs.txt", "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S  ") + text + "\n")
    except OSError:
        pass


def open_firewall(port):
    """Ajoute (en administrateur) une regle de pare-feu limitee au reseau local."""
    import ctypes
    rule = 'netsh advfirewall firewall add rule dir=in action=allow remoteip=localsubnet profile=any '
    args = ('/c ' + rule + 'name="Phoque Al telecommande" protocol=TCP localport=%d & ' % port
            + rule + 'name="Phoque Al satellites" protocol=UDP localport=8767')
    try:
        return ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd", args, None, 0) > 32
    except Exception:
        return False


def PhoneDialog(main):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QMessageBox,
                                   QPushButton, QVBoxLayout)

    web = main.web
    dlg = QDialog(main)
    dlg.setWindowTitle("Télécommande sur téléphone")
    dlg.resize(620, 560)
    lay = QVBoxLayout(dlg)

    enable = QCheckBox("Activer la télécommande sur téléphone / tablette")
    enable.setChecked(web.running())
    lay.addWidget(enable)

    body = QHBoxLayout()
    qr = QLabel()
    qr.setFixedSize(270, 270)
    qr.setAlignment(Qt.AlignCenter)
    qr.setStyleSheet("background: white; border-radius: 8px;")
    body.addWidget(qr)
    side = QVBoxLayout()
    ip_box = QComboBox()
    for ip, name in local_ips_named():
        ip_box.addItem("%s  —  %s" % (ip, name) if name else ip, ip)
    url_lab = QLabel()
    url_lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
    url_lab.setWordWrap(True)
    pin_lab = QLabel()
    pin_lab.setStyleSheet("font-size: 22pt; font-weight: 700; letter-spacing: 6px;")
    pin_lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
    side.addWidget(QLabel("<b>1.</b> Téléphone et PC sur le <b>même Wi-Fi</b>."))
    side.addWidget(QLabel("<b>2.</b> Scanne le QR code avec l'appareil photo."))
    side.addWidget(QLabel("ou tape cette adresse dans le navigateur :"))
    side.addWidget(url_lab)
    side.addWidget(QLabel("puis ce code :"))
    side.addWidget(pin_lab)
    side.addWidget(QLabel("Réseau :"))
    side.addWidget(ip_box)
    side.addStretch(1)
    body.addLayout(side, 1)
    lay.addLayout(body)

    help_ = QLabel("Astuce : dans le navigateur du téléphone, menu ⋮ → « Ajouter à l'écran d'accueil » pour "
                   "l'ouvrir comme une appli. Si le téléphone n'arrive pas à se connecter, clique sur "
                   "« Autoriser dans le pare-feu » (Windows demande une confirmation administrateur). "
                   "Sans box internet, active le partage de connexion du téléphone et connecte le PC à ce Wi-Fi.")
    help_.setWordWrap(True)
    help_.setObjectName("status")
    lay.addWidget(help_)
    err = QLabel()
    err.setStyleSheet("color: #e5686a;")
    err.setWordWrap(True)
    lay.addWidget(err)

    row = QHBoxLayout()
    fw = QPushButton("Autoriser dans le pare-feu")
    regen = QPushButton("Nouveau code (déconnecte les téléphones)")
    close = QPushButton("Fermer")
    row.addWidget(fw)
    row.addWidget(regen)
    row.addStretch(1)
    row.addWidget(close)
    lay.addLayout(row)

    def update():
        ip = ip_box.currentData() or "127.0.0.1"
        on = web.running()
        url_lab.setText("<b>%s</b>" % web.url(ip))
        pin_lab.setText(web.pin if on else "——")
        qr.setPixmap(QPixmap_empty())
        problem = ""
        if not on:
            qr.setText("Coche « Activer »\nci-dessus")
        else:
            try:
                qr.setPixmap(qr_pixmap(web.url(ip, pair=True)))
            except Exception as e:
                import traceback
                log_error(traceback.format_exc())
                problem = "QR code indisponible (%s) : utilise l'adresse et le code." % e
                qr.setText("QR code\nindisponible")
        err.setText(web.error or problem)
        if web.error:
            log_error(web.error)
        for w in (url_lab, pin_lab, ip_box, regen):
            w.setEnabled(on)

    def QPixmap_empty():
        from PySide6.QtGui import QPixmap
        return QPixmap()

    def toggled(on):
        if on:
            if not web.start():
                enable.blockSignals(True)
                enable.setChecked(False)
                enable.blockSignals(False)
        else:
            web.stop()
        db.set_setting(main.conn, "phone_on", "1" if web.running() else "0")
        main.phone_btn.setChecked(web.running())
        update()

    def do_regen():
        if QMessageBox.question(dlg, "Nouveau code", "Créer un nouveau code ? Les téléphones déjà connectés "
                                "devront scanner le nouveau QR code.") == QMessageBox.Yes:
            web.regenerate()
            update()

    def do_fw():
        if open_firewall(web.port):
            QMessageBox.information(dlg, "Pare-feu", "Règle ajoutée (si tu as confirmé la demande de Windows). "
                                    "Réessaie depuis le téléphone.")
        else:
            QMessageBox.warning(dlg, "Pare-feu", "La règle n'a pas pu être ajoutée.")

    enable.toggled.connect(toggled)
    if not web.running():
        enable.setChecked(True)          # ouvrir cette fenetre = vouloir s'en servir
    ip_box.currentIndexChanged.connect(lambda _i: update())
    regen.clicked.connect(lambda: do_regen())
    fw.clicked.connect(lambda: do_fw())
    close.clicked.connect(dlg.accept)
    update()
    return dlg
