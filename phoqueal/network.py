"""Ordinateurs satellites (ex. un portable relie par cable Ethernet).

Le satellite fait tourner Phoque Al avec SA bibliotheque, sur SON disque, et active son serveur
(bouton « Telephone »). Le PC principal s'y connecte avec le code du satellite, puis :
- les ecrans du satellite deviennent les ecrans 4, 5... du PC principal (lecteurs « distants ») ;
- la bibliotheque du satellite apparait sur l'accueil (« bibliotheque secondaire ») ;
- une video du satellite lue sur un ecran du satellite est lue depuis SON disque (rien ne passe
  par le cable sauf les commandes) ; les cas croises sont diffuses par le cable ;
- si une video du PC principal existe aussi sur le satellite (meme nom, meme taille),
  le satellite lit sa propre copie.

Identifiants : une video d'un satellite a un identifiant NEGATIF dans Phoque Al :
    -(node_id * 10 000 000 + id_sur_le_satellite)
"""
import json
import os
import queue
import secrets
import socket
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from . import db
from .config import DATA_DIR

DISCOVERY_PORT = 8767
ID_BASE = 10_000_000
NODE_DIR = DATA_DIR / "satellites"

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes(id INTEGER PRIMARY KEY, name TEXT NOT NULL, host TEXT NOT NULL,
    port INTEGER NOT NULL, token TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, rev TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS remote_videos(node_id INTEGER NOT NULL, rid INTEGER NOT NULL, title TEXT,
    category TEXT, duration REAL, size INTEGER, fname TEXT, PRIMARY KEY(node_id, rid));
CREATE INDEX IF NOT EXISTS idx_rv_cat ON remote_videos(node_id, category);
CREATE INDEX IF NOT EXISTS idx_rv_dup ON remote_videos(node_id, size, fname);
"""

# Connexions directes (sans passer par un proxy configure dans Windows)
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http(host, port, path, token=None, body=None, timeout=3.0, raw=False):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Phoque Al-Token"] = token
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request("http://%s:%d%s" % (host, port, path), data=data, headers=headers)
    with _OPENER.open(req, timeout=timeout) as r:
        payload = r.read()
    return payload if raw else json.loads(payload or b"{}")


def encode_id(node_id, rid):
    return -(int(node_id) * ID_BASE + int(rid))


def decode_id(vid):
    v = -int(vid)
    return v // ID_BASE, v % ID_BASE


def local_ip_towards(host):
    """Adresse du PC principal vue depuis le satellite (interface qui mene a lui)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((host, 9))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


# ----------------------------------------------------------------------------------------
# Decouverte sur le reseau local (diffusion UDP)
# ----------------------------------------------------------------------------------------
def broadcast_addresses():
    from .webremote import local_ips
    out = {"255.255.255.255"}
    ips = list(local_ips())
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.append(info[4][0])
    except OSError:
        pass
    for ip in ips:
        parts = ip.split(".")
        if len(parts) != 4:
            continue
        if ip.startswith("169.254."):
            out.add("169.254.255.255")
        else:
            out.add(".".join(parts[:3] + ["255"]))
    return list(out)


def discover(seconds=1.5):
    """Cherche les Phoque Al (serveur actif) sur le reseau local. -> [{name, host, port, screens}]"""
    found = {}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(0.3)
    try:
        s.bind(("", 0))
        end = time.time() + seconds
        next_send = 0
        while time.time() < end:
            if time.time() >= next_send:
                for addr in broadcast_addresses():
                    try:
                        s.sendto(b"PHOQUE AL?", (addr, DISCOVERY_PORT))
                    except OSError:
                        pass
                next_send = time.time() + 0.5
            try:
                data, (host, _p) = s.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                continue
            try:
                info = json.loads(data.decode("utf-8"))
            except ValueError:
                continue
            if info.get("focal"):
                info["host"] = host
                found[(host, info.get("port"))] = info
    finally:
        s.close()
    try:
        from .webremote import local_ips
        own = set(local_ips())
    except Exception:
        own = set()
    return [v for v in found.values() if v["host"] not in own]


class DiscoveryResponder(threading.Thread):
    """Cote satellite : repond « je suis la » aux recherches du PC principal."""

    def __init__(self, info_fn):
        super().__init__(name="focal-discovery", daemon=True)
        self.info_fn = info_fn
        self.stop_flag = threading.Event()
        self.sock = None

    def run(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind(("", DISCOVERY_PORT))
            self.sock.settimeout(0.5)
        except OSError:
            return
        while not self.stop_flag.is_set():
            try:
                data, addr = self.sock.recvfrom(256)
            except socket.timeout:
                continue
            except OSError:
                break
            if data.strip() == b"PHOQUE AL?":
                try:
                    info = dict(self.info_fn(), focal=1)
                    self.sock.sendto(json.dumps(info).encode("utf-8"), addr)
                except OSError:
                    pass
        try:
            self.sock.close()
        except OSError:
            pass

    def stop(self):
        self.stop_flag.set()


# ----------------------------------------------------------------------------------------
# Fil de communication avec un satellite
# ----------------------------------------------------------------------------------------
class NodeWorker(QThread):
    state = Signal(int, object)
    catalog = Signal(int, object)
    online = Signal(int, bool, str)
    thumbs = Signal(int)

    def __init__(self, node):
        super().__init__()
        self.node_id = node["id"]
        self.host = node["host"]
        self.port = int(node["port"])
        self.token = node["token"]
        self.rev = node["rev"] or ""
        self.actions = queue.Queue()
        self.wake = threading.Event()
        self.want_catalog = True
        self.thumb_dir = NODE_DIR / str(self.node_id)

    def send(self, action):
        self.actions.put(action)
        self.wake.set()

    def call(self, path, body=None, timeout=3.0, raw=False):
        return http(self.host, self.port, path, self.token, body, timeout, raw)

    def run(self):
        status = None
        last_cat = 0.0
        todo = []
        self.thumb_dir.mkdir(parents=True, exist_ok=True)
        while not self.isInterruptionRequested():
            sent = False
            while True:
                try:
                    a = self.actions.get_nowait()
                except queue.Empty:
                    break
                try:
                    self.call("/api/action", a)
                    sent = True
                except Exception:
                    pass
            try:
                st = self.call("/api/state")
                if status != "ok":
                    status = "ok"
                    self.online.emit(self.node_id, True, "connecté")
                    self.want_catalog = True
                self.state.emit(self.node_id, st)
            except urllib.error.HTTPError as e:
                msg = "code à refaire (nouveau code sur le satellite)" if e.code == 401 else "erreur %d" % e.code
                if status != msg:
                    status = msg
                    self.online.emit(self.node_id, False, msg)
            except Exception:
                if status != "off":
                    status = "off"
                    self.online.emit(self.node_id, False, "injoignable")
            if status == "ok" and (self.want_catalog or time.time() - last_cat > 60):
                try:
                    cat = self.call("/api/catalog?rev=" + quote(self.rev), timeout=20)
                    last_cat = time.time()
                    self.want_catalog = False
                    if not cat.get("same"):
                        self.rev = cat.get("rev", "")
                        self.catalog.emit(self.node_id, cat)
                        todo = [v["id"] for v in cat.get("videos", []) if v.get("thumb")]
                except Exception:
                    pass
            if status == "ok" and todo:
                batch, todo = todo[:12], todo[12:]
                for rid in batch:
                    path = self.thumb_dir / ("%d.jpg" % rid)
                    if path.is_file():
                        continue
                    try:
                        data = self.call("/api/thumb/%d" % rid, timeout=5, raw=True)
                        tmp = path.with_suffix(".part")
                        tmp.write_bytes(data)
                        os.replace(tmp, path)
                    except Exception:
                        pass
                if not todo:
                    self.thumbs.emit(self.node_id)
            self.wake.wait(0.1 if (sent or todo) else 0.5)
            self.wake.clear()

    def stop(self):
        self.requestInterruption()
        self.wake.set()
        self.wait(4000)


# ----------------------------------------------------------------------------------------
# Ecran distant : imite un lecteur de Phoque Al, les commandes partent vers le satellite
# ----------------------------------------------------------------------------------------
class _RemoteVLC:
    """Ce qu'utilisent la ligne de temps et les volumes (comme un lecteur VLC)."""

    def __init__(self, scr):
        self.scr = scr

    def get_time(self):
        return int(self.scr.time * 1000)

    def get_length(self):
        return int(self.scr.length * 1000) if self.scr.length else -1

    def set_time(self, ms):
        if self.scr.length:
            self.scr.time = ms / 1000.0
            self.scr.send("seek_to", value=max(0.0, min(1.0, ms / 1000.0 / self.scr.length)))

    def audio_set_volume(self, v):
        self.scr.volume = int(v)
        self.scr.send("volume", value=int(v))

    def get_state(self):
        return None

    def is_playing(self):
        return self.scr.playing


class RemoteScreen:
    is_remote = True
    is_mosaic = False

    def __init__(self, net, node_id, sn, number):
        self.net = net
        self.node_id = node_id
        self.sn = sn                     # numero de l'ecran SUR le satellite
        self.number = number             # numero de l'ecran dans le PC principal
        self.video_id = None             # (exclu du montage et des reprises)
        self.title = ""
        self.playing = False
        self.muted = False
        self.volume = 80
        self.view_mode = "original"
        self.time = 0.0
        self.length = 0.0
        self.queue = None                # file geree par le PC principal
        self.qi = 0
        self.qname = None
        self._loop = False
        self._random = False
        self.was_fullscreen = True
        self.grace = 0.0                 # le satellite n'a pas encore demarre la video
        self.active = False              # present dans la liste des lecteurs du PC principal
        self.player = _RemoteVLC(self)

    # --- etat recu du satellite -------------------------------------------------------
    def update(self, s):
        self.title = s.get("title", "")
        self.playing = bool(s.get("playing"))
        self.muted = bool(s.get("muted"))
        self.volume = int(s.get("volume", self.volume))
        self.view_mode = s.get("view_key", self.view_mode)
        self.time = float(s.get("time") or 0)
        self.length = float(s.get("length") or 0)
        if not self.queue:
            self._loop = bool(s.get("loop"))
            self._random = bool(s.get("random"))

    def send(self, cmd, **kw):
        self.net.send(self.node_id, dict(kw, cmd=cmd, screen=self.sn))

    # --- interface d'un lecteur de Phoque Al ------------------------------------------------
    @property
    def loop(self):
        return self._loop

    @loop.setter
    def loop(self, value):
        self._loop = bool(value)
        if not self.queue:
            self.send("set_loop", value=self._loop)

    @property
    def random_mode(self):
        return self._random

    @random_mode.setter
    def random_mode(self, value):
        self._random = bool(value)
        if not self.queue:
            self.send("set_random", value=self._random)

    def display_title(self):
        node = self.net.node_name(self.node_id)
        t = self.title or "…"
        if self.queue:
            t = "%s   (%s %d/%d)" % (t.split("   (")[0], self.qname or "playlist", self.qi + 1, len(self.queue))
        return "%s · %s" % (node, t)

    def is_playing(self):
        return self.playing

    def toggle_pause(self):
        self.playing = not self.playing
        self.send("pause")

    def set_pause(self, paused):
        self.playing = not paused
        self.send("set_pause", value=bool(paused))

    def seek(self, seconds):
        self.send("seek", value=int(seconds))

    def change_volume(self, delta):
        self.volume = max(0, min(150, self.volume + delta))
        self.send("volume", value=self.volume)

    def set_mute(self, muted):
        self.muted = bool(muted)
        self.send("set_mute", value=self.muted)

    def toggle_mute(self):
        self.set_mute(not self.muted)

    def set_view(self, mode, show=True):
        self.view_mode = mode
        self.send("set_view", value=mode)

    def cycle_view(self):
        from . import view
        self.set_view(view.next_mode(self.view_mode))

    def osd(self, text, ms=2500):
        self.send("osd", value=str(text))

    def close(self):
        self.queue = None
        self.send("stop")
        self.net.forget(self)

    # fenetre (utilise par la touche panique et la touche « aller a l'ecran »)
    def raise_(self):
        self.send("focus")

    def activateWindow(self):
        pass

    def isFullScreen(self):
        return True

    def hide(self):
        self.net.node_hidden(self.node_id, True)

    def place(self):
        self.net.node_hidden(self.node_id, False)

    show = place


# ----------------------------------------------------------------------------------------
# Gestion des satellites (fil Qt)
# ----------------------------------------------------------------------------------------
class Network(QObject):
    changed = Signal()

    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.conn = main.conn
        self.conn.executescript(SCHEMA)
        self.workers = {}
        self.status = {}             # node_id -> (en_ligne, texte)
        self.screens = {}            # node_id -> nb d'ecrans du satellite
        self.proxies = {}            # (node_id, sn) -> RemoteScreen
        self.hidden = {}             # node_id -> masque par la touche panique
        self._numbers = []
        self._home_dirty = False
        self.home_timer = QTimer(self)
        self.home_timer.setSingleShot(True)
        self.home_timer.setInterval(1500)
        self.home_timer.timeout.connect(self._refresh_home)
        for n in self.nodes():
            if n["enabled"]:
                self.start_node(n)
        if self.nodes():
            QTimer.singleShot(1200, self.ensure_server)

    # --- satellites enregistres -------------------------------------------------------------
    def nodes(self):
        return self.conn.execute("SELECT * FROM nodes ORDER BY id").fetchall()

    def node(self, node_id):
        return self.conn.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()

    def node_name(self, node_id):
        n = self.node(node_id)
        return n["name"] if n else "Satellite"

    def pair(self, host, port, pin, name=None):
        """Appairage avec le code du satellite. Retourne (ok, message)."""
        try:
            r = http(host, int(port), "/api/login", body={"pin": str(pin).strip()}, timeout=5)
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read() or b"{}").get("error", "")
            except ValueError:
                msg = ""
            return False, msg or "Refusé (%d)" % e.code
        except Exception as e:
            return False, "Injoignable : %s" % e
        token = r.get("token")
        if not token:
            return False, "Réponse inattendue"
        if not name:
            try:
                name = http(host, int(port), "/api/catalog?rev=x", token, timeout=10).get("name")
            except Exception:
                name = None
        name = name or host
        old = self.conn.execute("SELECT id FROM nodes WHERE host=? AND port=?", (host, int(port))).fetchone()
        if old:
            self.conn.execute("UPDATE nodes SET token=?, name=?, enabled=1, rev='' WHERE id=?", (token, name, old["id"]))
            node_id = old["id"]
            self.stop_node(node_id)
        else:
            cur = self.conn.execute("INSERT INTO nodes(name, host, port, token) VALUES (?,?,?,?)",
                                    (name, host, int(port), token))
            node_id = cur.lastrowid
        self.conn.commit()
        self.start_node(self.node(node_id))
        self.ensure_server()
        return True, "Appairé avec « %s »" % name

    def remove(self, node_id):
        self.stop_node(node_id)
        self.conn.execute("DELETE FROM remote_videos WHERE node_id=?", (node_id,))
        self.conn.execute("DELETE FROM nodes WHERE id=?", (node_id,))
        self.conn.commit()
        self.schedule_home()

    def rename(self, node_id, name):
        self.conn.execute("UPDATE nodes SET name=? WHERE id=?", (name, node_id))
        self.conn.commit()
        self.schedule_home()

    def set_enabled(self, node_id, on):
        self.conn.execute("UPDATE nodes SET enabled=? WHERE id=?", (1 if on else 0, node_id))
        self.conn.commit()
        if on:
            self.start_node(self.node(node_id))
        else:
            self.stop_node(node_id)

    def start_node(self, node):
        if node["id"] in self.workers:
            return
        w = NodeWorker(node)
        w.state.connect(self.on_state)
        w.catalog.connect(self.on_catalog)
        w.online.connect(self.on_online)
        w.thumbs.connect(lambda _nid: self.schedule_home())
        self.workers[node["id"]] = w
        self.status[node["id"]] = (False, "connexion…")
        w.start()

    def stop_node(self, node_id):
        w = self.workers.pop(node_id, None)
        if w is not None:
            w.stop()
        self.drop_screens(node_id)
        self.status[node_id] = (False, "désactivé")

    def stop(self):
        workers = list(self.workers.values())
        self.workers.clear()
        for w in workers:
            w.requestInterruption()
            w.wake.set()
        for w in workers:
            w.wait(2000)

    def send(self, node_id, action):
        w = self.workers.get(node_id)
        if w is not None:
            w.send(action)

    def ensure_server(self):
        """Le PC principal doit aussi servir ses videos aux satellites (lecture croisee)."""
        web = getattr(self.main, "web", None)
        if web is not None and not web.running():
            if web.start():
                db.set_setting(self.conn, "phone_on", "1")
                if hasattr(self.main, "phone_btn"):
                    self.main.phone_btn.setChecked(True)

    def stream_token(self):
        tok = db.get_setting(self.conn, "net_stream_token")
        if not tok:
            tok = secrets.token_urlsafe(24)
            db.set_setting(self.conn, "net_stream_token", tok)
        web = getattr(self.main, "web", None)
        if web is not None:
            with web.bridge.lock:
                web.bridge.tokens.add(tok)
        return tok

    # --- etat des satellites ---------------------------------------------------------------
    def on_online(self, node_id, ok, text):
        self.status[node_id] = (ok, text)
        if not ok:
            self.drop_screens(node_id)
        self.changed.emit()

    def drop_screens(self, node_id):
        for key in [k for k in self.proxies if k[0] == node_id]:
            p = self.proxies.pop(key)
            self._unregister(p)
        if self.screens.pop(node_id, None) is not None:
            self.renumber()

    def on_state(self, node_id, st):
        scr = st.get("screens") or []
        if self.screens.get(node_id) != len(scr):
            self.screens[node_id] = len(scr)
            self.renumber()
        now = time.time()
        for s in scr:
            key = (node_id, s["n"])
            p = self.proxies.get(key)
            if s.get("busy"):
                if p is None:
                    p = self.proxy(node_id, s["n"])
                p.update(s)
                p.grace = 0
                self._register(p)
            elif p is not None:
                if p.grace and now < p.grace:
                    continue                           # la video est en train de demarrer
                if p.queue and self.step(p, 1, manual=False, ended=True):
                    continue
                self._unregister(p)
                p.queue = None

    def proxy(self, node_id, sn):
        key = (node_id, sn)
        p = self.proxies.get(key)
        if p is None:
            p = RemoteScreen(self, node_id, sn, self.number_of(node_id, sn) or 0)
            self.proxies[key] = p
        return p

    def _register(self, p):
        p.active = True
        players = self.main.ctrl.players
        if p.number and players.get(p.number) is not p:
            players[p.number] = p

    def _unregister(self, p):
        p.active = False
        players = self.main.ctrl.players
        if players.get(p.number) is p:
            del players[p.number]

    def forget(self, p):
        self._unregister(p)

    def node_hidden(self, node_id, hide):
        if self.hidden.get(node_id, False) != hide:
            self.hidden[node_id] = hide
            self.send(node_id, {"cmd": "panic_set", "value": hide})

    # --- numerotation des ecrans --------------------------------------------------------------
    def local_count(self):
        return len(self.main.ctrl.screens())

    def renumber(self):
        nums = []
        base = self.local_count()
        mapping = {}
        for n in self.nodes():
            k = self.screens.get(n["id"], 0)
            for sn in range(1, k + 1):
                mapping[(n["id"], sn)] = base + sn
                nums.append(base + sn)
            base += k
        players = self.main.ctrl.players
        for key, p in self.proxies.items():
            new = mapping.get(key)
            if players.get(p.number) is p:
                del players[p.number]
            p.number = new or 0
        self._map = mapping
        for p in self.proxies.values():
            if p.number and p.active:
                players[p.number] = p
        if nums != self._numbers:
            self._numbers = nums
            if hasattr(self.main, "on_screens_changed"):
                self.main.on_screens_changed()
        self.changed.emit()

    _map = {}

    def numbers(self):
        return list(self._numbers)

    def number_of(self, node_id, sn):
        return self._map.get((node_id, sn))

    def owns(self, number):
        return number in self._numbers

    def where(self, number):
        for key, num in self._map.items():
            if num == number:
                return key
        return None

    def label(self, number):
        key = self.where(number)
        return "%s %d" % (self.node_name(key[0]), key[1]) if key else ""

    # --- lecture -------------------------------------------------------------------------------
    def play_on(self, number, video_id, queue=None, qi=0, qname=None, loop=False):
        key = self.where(number)
        if key is None:
            return False
        p = self.proxy(*key)
        p.number = number
        p.queue = list(queue) if queue else None
        p.qi = qi
        p.qname = qname
        if queue:
            p._loop = bool(loop)
        ok = self._play_item(p, video_id)
        if ok:
            self._register(p)
            self.main.status.setText("Écran %d (%s) : lecture lancée" % (number, self.node_name(key[0])))
        return ok

    def _play_item(self, p, video_id, start=None):
        node = self.node(p.node_id)
        if node is None or not self.status.get(p.node_id, (False,))[0]:
            self.main.status.setText("Satellite « %s » injoignable" % (node["name"] if node else "?"))
            return False
        ctrl = self.main.ctrl
        p.grace = time.time() + 6
        p.title = "…"
        vid = int(video_id)
        if vid < 0:
            nid, rid = decode_id(vid)
            if nid == p.node_id:
                p.send("play", value=rid)                       # sa propre video, sur son disque
                return True
            row, url = self.resolve(vid)
            if not url:
                return False
            p.send("play_url", value=url, title=row["title"], start=ctrl.start_position(row))
            return True
        row = self.conn.execute("SELECT * FROM videos WHERE id=?", (vid,)).fetchone()
        if row is None:
            return False
        fname = os.path.basename(row["rel_path"].split("::")[0])
        twin = self.conn.execute("SELECT rid FROM remote_videos WHERE node_id=? AND size=? AND fname=?",
                                 (p.node_id, row["size"], fname)).fetchone()
        ctrl._count_play(vid)
        if twin:
            p.send("play", value=twin["rid"])                  # meme fichier present sur le satellite
            return True
        host = local_ip_towards(node["host"])
        web = self.main.web
        url = "http://%s:%d/api/stream/%d?t=%s" % (host, web.port, vid, self.stream_token())
        p.send("play_url", value=url, title=row["title"], start=ctrl.start_position(row))
        return True

    def step(self, p, step, manual=False, ended=False):
        """Suivante / precedente dans la file geree par le PC principal."""
        if not p.queue:
            if manual:
                p.send("next" if step > 0 else "prev")         # le satellite enchaine dans le theme
                return True
            return False
        n = len(p.queue)
        if p._random and n > 1:
            import random
            choices = [i for i in range(n) if i != p.qi]
            p.qi = random.choice(choices)
            return self._play_item(p, p.queue[p.qi])
        i = p.qi + (1 if step > 0 else -1)
        if i >= n or i < 0:
            if not p._loop:
                if manual:
                    p.osd("Fin de la playlist" if i >= n else "Début de la playlist")
                return False
            i %= n
        p.qi = i
        return self._play_item(p, p.queue[i])

    def resolve(self, vid):
        """Video d'un satellite lue sur un ecran du PC principal : diffusee par le cable."""
        nid, rid = decode_id(vid)
        node = self.node(nid)
        r = self.conn.execute("SELECT * FROM remote_videos WHERE node_id=? AND rid=?", (nid, rid)).fetchone()
        if node is None or r is None:
            return None, None
        row = {"id": vid, "title": r["title"], "duration": r["duration"] or 0, "position": 0,
               "kind": "video", "category": r["category"], "rel_path": r["fname"], "archive": None}
        if not self.status.get(nid, (False,))[0]:
            self.main.status.setText("Satellite « %s » injoignable" % node["name"])
            return row, None
        url = "http://%s:%d/api/stream/%d?t=%s" % (node["host"], node["port"], rid, node["token"])
        return row, url

    # --- catalogue (bibliotheque secondaire) ------------------------------------------------------
    def on_catalog(self, node_id, cat):
        vids = cat.get("videos") or []
        self.conn.execute("DELETE FROM remote_videos WHERE node_id=?", (node_id,))
        self.conn.executemany(
            "INSERT OR REPLACE INTO remote_videos(node_id, rid, title, category, duration, size, fname) "
            "VALUES (?,?,?,?,?,?,?)",
            [(node_id, v["id"], v.get("title"), v.get("category"), v.get("duration"), v.get("size"), v.get("name"))
             for v in vids])
        name = cat.get("name")
        if name:
            n = self.node(node_id)
            if n and n["name"] in (n["host"], "", None):
                self.conn.execute("UPDATE nodes SET name=? WHERE id=?", (name, node_id))
        self.conn.execute("UPDATE nodes SET rev=? WHERE id=?", (cat.get("rev", ""), node_id))
        self.conn.commit()
        self.schedule_home()

    def schedule_home(self):
        self.home_timer.start()

    def _refresh_home(self):
        m = self.main
        if m.pages.currentIndex() == 0 and not getattr(m, "hover_item", None):
            m.show_home()
        self.changed.emit()

    def row(self, r, node_name):
        thumb = NODE_DIR / str(r["node_id"]) / ("%d.jpg" % r["rid"])
        return {"id": encode_id(r["node_id"], r["rid"]), "title": r["title"] or "?",
                "category": "%s · %s" % (node_name, r["category"]), "kind": "video",
                "thumb": str(thumb) if thumb.is_file() else None, "duration": r["duration"] or 0,
                "liked": 0, "position": 0, "play_count": 0, "added_at": 0, "rel_path": r["fname"] or ""}

    def categories(self, node_id):
        return self.conn.execute("SELECT category, COUNT(*) AS n FROM remote_videos WHERE node_id=? "
                                 "GROUP BY category ORDER BY category COLLATE NOCASE", (node_id,)).fetchall()

    def category_rows(self, node_id, category, limit=None):
        name = self.node_name(node_id)
        sql = "SELECT * FROM remote_videos WHERE node_id=? AND category=? ORDER BY title COLLATE NOCASE"
        rows = self.conn.execute(sql + (" LIMIT %d" % limit if limit else ""), (node_id, category)).fetchall()
        return [self.row(r, name) for r in rows]

    def category_ids(self, node_id, category):
        return [encode_id(node_id, r["rid"]) for r in self.conn.execute(
            "SELECT rid FROM remote_videos WHERE node_id=? AND category=? ORDER BY title COLLATE NOCASE",
            (node_id, category))]

    def search(self, text, limit=100):
        words = [w for w in text.lower().split() if w]
        if not words:
            return []
        online = [nid for nid, (ok, _t) in self.status.items() if ok]
        if not online:
            return []
        sql = ("SELECT * FROM remote_videos WHERE node_id IN (%s)" % ",".join("?" * len(online))
               + "".join(" AND (lower(title) LIKE ? OR lower(category) LIKE ? OR lower(fname) LIKE ?)" for _ in words)
               + " ORDER BY title COLLATE NOCASE LIMIT %d" % limit)
        args = list(online)
        for w in words:
            args += ["%" + w + "%"] * 3
        names = {n["id"]: n["name"] for n in self.nodes()}
        return [self.row(r, names.get(r["node_id"], "Satellite")) for r in self.conn.execute(sql, args)]

    def add_home_rows(self, row_limit):
        m = self.main
        for k, n in enumerate(self.nodes(), 1):
            if not n["enabled"]:
                continue
            cats = self.categories(n["id"])
            if not cats:
                continue
            ok = self.status.get(n["id"], (False, ""))[0]
            for j, c in enumerate(cats, 1):
                if m.discreet:
                    label = "Collection S%d-%d" % (k, j)
                else:
                    label = "\U0001F4BB %s · %s%s" % (n["name"], c["category"], "" if ok else "  (hors ligne)")
                spec = ("node", n["id"], "%s · %s" % (n["name"], c["category"]), c["category"])
                m.add_row("%s  (%d)" % (label, c["n"]), self.category_rows(n["id"], c["category"], row_limit), spec)


# ----------------------------------------------------------------------------------------
# Fenetre « Ordinateurs satellites »
# ----------------------------------------------------------------------------------------
def NodesDialog(main):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (QDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
                                   QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout)
    net = main.network
    dlg = QDialog(main)
    dlg.setWindowTitle("Ordinateurs satellites")
    dlg.resize(760, 560)
    lay = QVBoxLayout(dlg)
    intro = QLabel(
        "Un <b>satellite</b> est un autre ordinateur (ex. un portable relié par câble Ethernet) qui fait tourner "
        "Phoque Al avec <b>sa propre bibliothèque</b>. Ses écrans deviennent des écrans supplémentaires de ce PC, et "
        "ses vidéos apparaissent sur l'accueil.<br><br><b>Sur le satellite</b> : ouvre Phoque Al, clique sur "
        "« Téléphone » (le serveur s'active) et note le <b>code à 6 chiffres</b>.<br><b>Ici</b> : « Rechercher », "
        "choisis-le, puis tape ce code.")
    intro.setWordWrap(True)
    lay.addWidget(intro)
    lst = QListWidget()
    lay.addWidget(lst, 1)
    info = QLabel()
    info.setObjectName("status")
    info.setWordWrap(True)
    lay.addWidget(info)
    row = QHBoxLayout()
    b_find = QPushButton("Rechercher sur le réseau…")
    b_ip = QPushButton("Ajouter par adresse IP…")
    b_toggle = QPushButton("Activer / désactiver")
    b_ren = QPushButton("Renommer…")
    b_del = QPushButton("Retirer")
    close = QPushButton("Fermer")
    for b in (b_find, b_ip, b_toggle, b_ren, b_del):
        row.addWidget(b)
    row.addStretch(1)
    row.addWidget(close)
    lay.addLayout(row)

    def fill():
        cur = lst.currentItem().data(Qt.UserRole) if lst.currentItem() else None
        lst.clear()
        for n in net.nodes():
            ok, text = net.status.get(n["id"], (False, "désactivé" if not n["enabled"] else "connexion…"))
            nb = net.screens.get(n["id"], 0)
            nv = net.conn.execute("SELECT COUNT(*) AS c FROM remote_videos WHERE node_id=?", (n["id"],)).fetchone()["c"]
            nums = [num for (nid, _sn), num in net._map.items() if nid == n["id"]]
            screens = ("écrans %s" % ", ".join(map(str, sorted(nums)))) if nums else "aucun écran"
            it = QListWidgetItem("%s %s   —   %s:%d   —   %s   ·   %s   ·   %d vidéos" % (
                "●" if ok else "○", n["name"], n["host"], n["port"], text, screens if ok else "—", nv))
            it.setData(Qt.UserRole, n["id"])
            lst.addItem(it)
            if n["id"] == cur:
                lst.setCurrentItem(it)
        if not lst.count():
            lst.addItem("Aucun satellite pour l'instant.")
            lst.item(0).setFlags(Qt.NoItemFlags)

    def selected():
        it = lst.currentItem()
        return it.data(Qt.UserRole) if it else None

    def do_pair(host, port, name=None):
        pin, ok = QInputDialog.getText(dlg, "Code du satellite",
                                       "Code à 6 chiffres affiché sur « %s »\n(bouton « Téléphone » de Phoque Al) :" % (name or host))
        if not ok or not pin.strip():
            return
        info.setText("Connexion à %s…" % host)
        dlg.repaint()
        ok, msg = net.pair(host, port, pin, name)
        info.setText(msg)
        if not ok:
            QMessageBox.warning(dlg, "Satellite", msg)
        fill()

    def do_find():
        info.setText("Recherche des Phoque Al sur le réseau…")
        dlg.repaint()
        found = discover()
        known = {(n["host"], n["port"]) for n in net.nodes()}
        found = [f for f in found if (f["host"], f["port"]) not in known]
        if not found:
            info.setText("Aucun nouveau Phoque Al trouvé. Sur le satellite : Phoque Al ouvert, bouton « Téléphone » activé, "
                         "pare-feu autorisé (même bouton). Sinon, utilise « Ajouter par adresse IP ».")
            return
        labels = ["%s  (%s, %d écran(s))" % (f.get("name", "?"), f["host"], f.get("screens", 0)) for f in found]
        choice, ok = QInputDialog.getItem(dlg, "Satellites trouvés", "Choisis l'ordinateur :", labels, 0, False)
        if ok:
            f = found[labels.index(choice)]
            do_pair(f["host"], f.get("port", 8765), f.get("name"))

    def do_ip():
        text, ok = QInputDialog.getText(dlg, "Adresse du satellite",
                                        "Adresse IP du satellite (affichée dans sa fenêtre « Téléphone ») :")
        text = text.strip().replace("http://", "").strip("/")
        if ok and text:
            host, _, port = text.partition(":")
            do_pair(host, int(port) if port.isdigit() else 8765)

    def do_toggle():
        nid = selected()
        if nid:
            n = net.node(nid)
            net.set_enabled(nid, not n["enabled"])
            fill()

    def do_ren():
        nid = selected()
        if nid:
            name, ok = QInputDialog.getText(dlg, "Renommer", "Nom du satellite :", text=net.node_name(nid))
            if ok and name.strip():
                net.rename(nid, name.strip())
                fill()

    def do_del():
        nid = selected()
        if nid and QMessageBox.question(dlg, "Retirer", "Retirer ce satellite ? (rien n'est supprimé sur lui)") \
                == QMessageBox.Yes:
            net.remove(nid)
            fill()

    b_find.clicked.connect(lambda: do_find())
    b_ip.clicked.connect(lambda: do_ip())
    b_toggle.clicked.connect(lambda: do_toggle())
    b_ren.clicked.connect(lambda: do_ren())
    b_del.clicked.connect(lambda: do_del())
    close.clicked.connect(dlg.accept)
    net.changed.connect(fill)
    dlg.finished.connect(lambda _r: net.changed.disconnect(fill))
    fill()
    return dlg
