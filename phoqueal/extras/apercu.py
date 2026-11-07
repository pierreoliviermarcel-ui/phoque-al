"""Télécommande : aperçu en miniature de chaque écran sur le téléphone.

- Une petite image (≈ 360 px de large) par écran en lecture, rafraîchie toutes les ~2 s.
- Capturée seulement quand un téléphone regarde la page (rien sinon), et jamais en mode discret.
- Lecteur simple : capture de l'image de la vidéo (VLC, sans bandes noires) ; mosaïque : capture de l'écran.
- Toucher la vignette la masque (sur ce téléphone) ; « ▣ Aperçu » la remet.
- Réglage sur le PC : ⋯ → Préférences → « Aperçu des écrans sur le téléphone ».
"""
import os
import threading
import time
from urllib.parse import urlparse

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt, QTimer
from PySide6.QtGui import QImage

from .. import db
from ..config import DATA_DIR
from .common import log, safe

WIDTH = 360
SHOT_DIR = DATA_DIR / "apercu"
LIVE = {"want": {}, "img": {}, "lock": threading.Lock(), "busy": set()}

CSS = r"""
/* v2.5 : aperçu des écrans */
.live{display:block;width:100%;aspect-ratio:16/9;max-height:24vh;object-fit:contain;background:#000;
  border-radius:10px;margin:6px 0 8px;opacity:0;transition:opacity .35s}
.live.ok{opacity:1}
.liveOff{display:inline-block;margin:4px 0 6px;padding:4px 10px;border-radius:999px;border:1px solid #2c2d38;
  background:transparent;color:#8a8a96;font-size:12px}
.card.free .live,.card.free .liveOff{display:none}
body.compact .live{max-height:18vh}
"""

JS = r"""
// ---------- v2.5 : aperçu des écrans en miniature
let liveOn = true; try { liveOn = localStorage.getItem("focal_live") !== "0"; } catch(e) {}
const liveUrls = {};
function liveSet(on){ liveOn = on; try { localStorage.setItem("focal_live", on ? "1" : "0"); } catch(e) {} liveTick(); }
async function liveTick(){
  if (document.hidden || !state || state.discreet || $("#app").classList.contains("hidden")) return;
  for (const s of (state.screens || [])){
    const c = $("#sc"+s.n); if (!c) continue;
    let img = c.querySelector(".live"), off = c.querySelector(".liveOff");
    if (!img){
      img = document.createElement("img"); img.className = "live"; img.alt = "";
      img.onclick = () => { vibrate(); liveSet(false); };
      off = document.createElement("button"); off.className = "liveOff"; off.textContent = "▣ Aperçu";
      off.onclick = () => { vibrate(); liveSet(true); };
      const head = c.querySelector(".head"); head.after(img); img.after(off);
    }
    img.style.display = liveOn ? "" : "none"; off.style.display = liveOn ? "none" : "";
    if (!liveOn || !s.busy) continue;
    try {
      const opt = {headers:{}, credentials:"same-origin", cache:"no-store"};
      if (token) opt.headers["X-PhoqueAl-Token"] = token;
      const r = await fetch("/api/live/" + s.n, opt);
      if (r.status !== 200) continue;
      const u = URL.createObjectURL(await r.blob());
      img.onload = () => { img.classList.add("ok"); if (liveUrls[s.n] && liveUrls[s.n] !== u) URL.revokeObjectURL(liveUrls[s.n]); liveUrls[s.n] = u; };
      img.src = u;
    } catch(e) {}
  }
  document.querySelectorAll(".live").forEach(i => { if (state.discreet) i.style.display = "none"; });
}
setInterval(liveTick, 2000);
"""


def enabled(main):
    return db.get_setting(main.conn, "remote_live", "1") == "1"


# ----------------------------------------------------------------------------------------
# Serveur : /api/live/<n>
# ----------------------------------------------------------------------------------------
def patch_server(main):
    from .. import webremote
    if getattr(webremote, "_extras_live", False):
        return
    orig = webremote.make_handler

    def make_handler(bridge):
        Base = orig(bridge)

        class Handler(Base):
            def do_GET(self):
                path = urlparse(self.path).path
                if not path.startswith("/api/live/"):
                    return Base.do_GET(self)
                bridge.last_request = time.time()
                if not self.authed():
                    return self.send(401, {"error": "code requis"})
                if bridge.discreet:
                    return self.send(403, {"error": "mode discret"})
                try:
                    n = int(path.rsplit("/", 1)[-1])
                except ValueError:
                    return self.send(404, {"error": "introuvable"})
                with LIVE["lock"]:
                    LIVE["want"][n] = time.time()
                    img = LIVE["img"].get(n)
                if not img or time.time() - img[1] > 12:
                    return self.send(204, b"", "text/plain")
                return self.send(200, img[0], "image/jpeg")

        return Handler

    webremote.make_handler = make_handler
    webremote._extras_live = True
    # serveur déjà démarré : nouvelles requêtes servies par la version complétée
    web = getattr(main, "web", None)
    srv = getattr(web, "server", None) if web else None
    if srv is not None:
        try:
            srv.RequestHandlerClass = make_handler(web.bridge)
        except Exception as e:
            log("aperçu téléphone : %r" % e)


def patch_page():
    from .. import webremote
    page = getattr(webremote, "PAGE", None)
    if not isinstance(page, str) or "v2.5 : aperçu des écrans" in page:
        return
    i = page.rfind("</style>")
    j = page.rfind("</script>")
    if i < 0 or j < 0:
        return
    page = page[:j] + JS + page[j:]
    page = page[:i] + CSS + page[i:]
    webremote.PAGE = page


# ----------------------------------------------------------------------------------------
# Captures (fil Qt)
# ----------------------------------------------------------------------------------------
def to_jpeg(img):
    if img is None or img.isNull():
        return None
    if img.width() > WIDTH:
        img = img.scaledToWidth(WIDTH, Qt.SmoothTransformation)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "JPG", 62)
    buf.close()
    return bytes(ba)


def store(n, data):
    if data:
        with LIVE["lock"]:
            LIVE["img"][n] = (data, time.time())


def grab_screen(main, n):
    scr = main.ctrl.screens()
    if not (1 <= n <= len(scr)):
        return
    pm = scr[n - 1].grabWindow(0)
    store(n, to_jpeg(pm.toImage()))


def grab_player(n, mp):
    """Capture VLC (asynchrone côté fichier) : lue un peu plus tard."""
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = str(SHOT_DIR / ("%d.png" % n))
    if mp.video_take_snapshot(0, path, WIDTH, 0) != 0:
        return False
    LIVE["busy"].add(n)

    def load():
        LIVE["busy"].discard(n)
        img = QImage(path)
        try:
            os.remove(path)
        except OSError:
            pass
        store(n, to_jpeg(img))
    QTimer.singleShot(250, lambda: safe(load))
    return True


def tick(main):
    if not enabled(main) or getattr(main, "discreet", False):
        with LIVE["lock"]:
            LIVE["img"].clear()
        return
    now = time.time()
    with LIVE["lock"]:
        wanted = [n for n, t in LIVE["want"].items() if now - t < 6]
        for n in [n for n in LIVE["img"] if n not in wanted]:
            LIVE["img"].pop(n, None)               # personne ne regarde : on libère
    for n in wanted:
        if n in LIVE["busy"]:
            continue
        p = main.ctrl.players.get(n)
        if p is None:
            with LIVE["lock"]:
                LIVE["img"].pop(n, None)
            continue
        try:
            mosaic = getattr(p, "is_mosaic", False)
            mp = None if mosaic else getattr(p, "player", None)
            if mp is None or not grab_player(n, mp):
                grab_screen(main, n)               # mosaïque, ou capture VLC impossible
        except Exception as e:
            log("aperçu écran %d : %r" % (n, e))


def menu(main, m):
    a = m.addAction("Aperçu des écrans sur le téléphone")
    a.setCheckable(True)
    a.setChecked(enabled(main))
    a.triggered.connect(lambda on: db.set_setting(main.conn, "remote_live", "1" if on else "0"))


def install(main):
    patch_page()
    patch_server(main)
    t = QTimer(main)
    t.setInterval(1500)
    t.timeout.connect(lambda: safe(tick, main))
    t.start()
    main._live_timer = t
