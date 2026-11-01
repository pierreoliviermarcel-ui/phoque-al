"""Langue de l'interface : français (d'origine), anglais, espagnol, allemand.

Le logiciel est écrit en français ; les textes sont traduits à l'affichage :
- textes des fenêtres, boutons, menus, cases, listes déroulantes, bulles d'aide, messages ;
- textes dessinés (bannière, frise, séance…) et messages affichés sur les vidéos ;
- page de la télécommande du téléphone.
Le choix se fait dans « ⋯ → Langue / Language » (relancer Phoque Al). Sans choix, la langue de Windows.
"""
import re

from PySide6.QtCore import QEvent, QLocale, QObject, QTimer
from PySide6.QtWidgets import (QAbstractButton, QApplication, QComboBox, QGroupBox, QLabel, QLineEdit, QMenu,
                               QMessageBox, QTabWidget, QWidget)

from .common import log

LANGS = [("fr", "Français"), ("en", "English"), ("es", "Español"), ("de", "Deutsch")]
STATE = {"lang": "fr", "installed": False}
TR = {}            # francais -> traduction
REV = {}           # traduction -> francais
PATTERNS = []      # (regex, [morceaux du modele traduit], [types])
CACHE = {}
RDYN = {}          # traductions déjà faites -> texte d'origine
_PH = re.compile(r"%%|%[-+ 0#]*\d*(?:\.\d+)?[sdfg]")
_SKIP = {"Phoque Al", "PHOQUE AL"}


# ----------------------------------------------------------------------------------------
# Dictionnaire
# ----------------------------------------------------------------------------------------
def _regex_for(key):
    parts, kinds, pos = [], [], 0
    for m in _PH.finditer(key):
        parts.append(re.escape(key[pos:m.start()]))
        ph = m.group(0)
        if ph == "%%":
            parts.append("%")
        else:
            c = ph[-1]
            kinds.append(c)
            if c == "d":
                parts.append(r"([+-]?\d+)")
            elif c in "fg":
                parts.append(r"([+-]?\d+(?:[.,]\d+)?(?:e[+-]?\d+)?)")
            else:
                parts.append(r"(.*?)")
        pos = m.end()
    parts.append(re.escape(key[pos:]))
    return re.compile("^" + "".join(parts) + "$", re.S), kinds


def _split_template(t):
    """'Écran %d : %s' -> ['Screen ', 0, ': ', 1] (indices des valeurs, '%%' -> '%')."""
    out, pos, n = [], 0, 0
    for m in _PH.finditer(t):
        out.append(t[pos:m.start()])
        if m.group(0) == "%%":
            out.append("%")
        else:
            out.append(n)
            n += 1
        pos = m.end()
    out.append(t[pos:])
    return out


def load(lang):
    TR.clear(); REV.clear(); CACHE.clear(); RDYN.clear(); del PATTERNS[:]
    STATE["lang"] = lang
    if lang == "fr":
        return
    from .i18n_data import DATA, LANGS as ORDER
    if lang not in ORDER:
        return
    k = ORDER.index(lang)
    for fr, tup in DATA.items():
        t = tup[k]
        if _PH.search(fr) and not set(fr) <= set("%% "):
            if any(ph != "%%" for ph in _PH.findall(fr)):
                rx, kinds = _regex_for(fr)
                PATTERNS.append((rx, _split_template(t), kinds, fr))
            # meme si modele, le texte brut peut s'afficher tel quel (ex. « Musique %d %% » jamais)
        TR[fr] = t
        if t != fr:
            REV.setdefault(t, fr)
    # modeles les plus longs d'abord (les plus précis)
    PATTERNS.sort(key=lambda p: -len(p[3]))


def _pattern(text):
    for rx, tmpl, kinds, _fr in PATTERNS:
        m = rx.match(text)
        if not m:
            continue
        vals = list(m.groups())
        vals = [tr(v) if kinds[i] == "s" else v for i, v in enumerate(vals)]
        try:
            return "".join(vals[p] if isinstance(p, int) else p for p in tmpl)
        except IndexError:
            continue
    return None


def tr(text):
    """Traduit un texte affiché (renvoie le texte d'origine si inconnu)."""
    if STATE["lang"] == "fr" or not text or not isinstance(text, str):
        return text
    r = CACHE.get(text)
    if r is not None:
        return r
    r = _tr(text)
    if len(CACHE) > 20000:
        CACHE.clear()
        RDYN.clear()
    CACHE[text] = r
    if r != text:
        RDYN[r] = text
    return r


def _tr(text):
    if text in _SKIP:
        return text
    t = TR.get(text)
    if t is not None:
        return t
    # raccourcis Qt (« Personnes && tags », « &Ouvrir »)
    if "&" in text:
        plain = text.replace("&&", "\x00").replace("&", "").replace("\x00", "&")
        if plain != text:
            t = TR.get(plain)
            if t is not None:
                return t.replace("&", "&&") if "&&" in text else t
    # espaces autour
    s = text.strip()
    if s != text and s:
        t = _tr(s)
        if t != s:
            i = text.index(s)
            return text[:i] + t + text[i + len(s):]
        return text
    if "\n" in text:
        lines = text.split("\n")
        out = [tr(l) for l in lines]
        return "\n".join(out)
    p = _pattern(text)
    if p is not None:
        return p
    # « titre   ·   info » : morceaux séparés
    for sep in ("   ·   ", "  ·  ", " · ", "  —  ", " — "):
        if sep in text:
            parts = text.split(sep)
            out = [tr(x) for x in parts]
            if out != parts:
                return sep.join(out)
    return text


def untr(text):
    """Texte français d'origine d'un texte traduit (pour les comparaisons internes)."""
    if STATE["lang"] == "fr" or not text:
        return text
    if text in RDYN:
        return RDYN[text]
    if text in REV:
        return REV[text]
    plain = text.replace("&&", "\x00").replace("&", "").replace("\x00", "&")
    return REV.get(plain, text)


def lang():
    return STATE["lang"]


# ----------------------------------------------------------------------------------------
# Widgets
# ----------------------------------------------------------------------------------------
def _set(get, put):
    try:
        old = get()
    except Exception:
        return
    if old:
        new = tr(old)
        if new != old:
            put(new)


def translate_widget(w):
    try:
        if w.isWindow():
            _set(w.windowTitle, w.setWindowTitle)
        if w.toolTip():
            _set(w.toolTip, w.setToolTip)
        if isinstance(w, QLabel):
            _set(w.text, w.setText)
        elif isinstance(w, QAbstractButton):
            _set(w.text, w.setText)
        elif isinstance(w, QGroupBox):
            _set(w.title, w.setTitle)
        elif isinstance(w, QLineEdit):
            _set(w.placeholderText, w.setPlaceholderText)
        elif isinstance(w, QComboBox):
            for i in range(w.count()):
                _set(lambda i=i: w.itemText(i), lambda t, i=i: w.setItemText(i, t))
            if w.isEditable() and w.lineEdit() is not None:
                _set(w.lineEdit().placeholderText, w.lineEdit().setPlaceholderText)
        elif isinstance(w, QTabWidget):
            for i in range(w.count()):
                _set(lambda i=i: w.tabText(i), lambda t, i=i: w.setTabText(i, t))
        if isinstance(w, QMenu):
            _set(w.title, w.setTitle)
            for a in w.actions():
                translate_action(a)
        if isinstance(w, QMessageBox):
            _set(w.text, w.setText)
            _set(w.informativeText, w.setInformativeText)
    except RuntimeError:
        pass


def translate_action(a):
    try:
        _set(a.text, a.setText)
        if a.toolTip() and a.toolTip() != a.text():
            _set(a.toolTip, a.setToolTip)
        m = a.menu()
        if m is not None:
            _set(m.title, m.setTitle)
    except RuntimeError:
        pass


def translate_tree(root):
    translate_widget(root)
    try:
        kids = root.findChildren(QWidget)
    except RuntimeError:
        return
    for w in kids:
        translate_widget(w)


class Filter(QObject):
    def eventFilter(self, obj, ev):
        try:
            t = ev.type()
            if t in (QEvent.Show, QEvent.Polish) and isinstance(obj, QWidget):
                if isinstance(obj, QMenu) or obj.isWindow():
                    translate_tree(obj)
                else:
                    translate_widget(obj)
            elif t == QEvent.ToolTip and isinstance(obj, QWidget):
                if obj.toolTip():
                    _set(obj.toolTip, obj.setToolTip)
        except Exception:
            pass
        return False


def sweep():
    """Filet de sécurité : textes changés après l'affichage (barre d'état, compteurs…)."""
    w = QApplication.activeWindow()
    if w is not None:
        try:
            translate_tree(w)
        except RuntimeError:
            pass


# ----------------------------------------------------------------------------------------
# Appels Python : setText, textes dessinés, messages sur les vidéos
# ----------------------------------------------------------------------------------------
def _wrap_method(cls, name, arg_index=0):
    orig = getattr(cls, name, None)
    if orig is None or getattr(orig, "_focal_tr", False):
        return

    def f(self, *a, **kw):
        if len(a) > arg_index and isinstance(a[arg_index], str):
            a = list(a)
            a[arg_index] = tr(a[arg_index])
        return orig(self, *a, **kw)
    f._focal_tr = True
    try:
        setattr(cls, name, f)
    except Exception as e:
        log("langue : %s.%s %r" % (cls.__name__, name, e))


def _wrap_last_str(cls, name):
    orig = getattr(cls, name, None)
    if orig is None or getattr(orig, "_focal_tr", False):
        return

    def f(self, *a, **kw):
        if a and isinstance(a[-1], str):
            a = a[:-1] + (tr(a[-1]),)
        return orig(self, *a, **kw)
    f._focal_tr = True
    try:
        setattr(cls, name, f)
    except Exception as e:
        log("langue : %s.%s %r" % (cls.__name__, name, e))


def _wrap_static(cls, name, idx):
    """QMessageBox.information(parent, titre, texte, …) / QInputDialog.getText(…)."""
    orig = getattr(cls, name, None)
    if orig is None or getattr(orig, "_focal_tr", False):
        return

    def f(*a, **kw):
        a = list(a)
        for i in idx:
            if len(a) > i and isinstance(a[i], str):
                a[i] = tr(a[i])
        for k in ("title", "text", "label", "caption"):
            if isinstance(kw.get(k), str):
                kw[k] = tr(kw[k])
        return orig(*a, **kw)
    f._focal_tr = True
    try:
        setattr(cls, name, staticmethod(f))
    except Exception as e:
        log("langue : %s.%s %r" % (cls.__name__, name, e))


def patch_qt():
    from PySide6.QtGui import QAction, QPainter
    from PySide6.QtWidgets import QFileDialog, QInputDialog, QPushButton
    _wrap_method(QLabel, "setText")
    for c in (QAbstractButton, QPushButton):
        _wrap_method(c, "setText")
    _wrap_method(QWidget, "setToolTip")
    _wrap_method(QWidget, "setWindowTitle")
    _wrap_method(QGroupBox, "setTitle")
    _wrap_method(QLineEdit, "setPlaceholderText")
    _wrap_method(QAction, "setText")
    _wrap_method(QAction, "setToolTip")
    _wrap_method(QMenu, "setTitle")
    _wrap_method(QMenu, "addMenu")
    _wrap_last_str(QMenu, "addSection")
    _wrap_method(QComboBox, "setItemText", 1)
    _wrap_method(QTabWidget, "setTabText", 1)
    _wrap_method(QMessageBox, "setText")
    _wrap_method(QMessageBox, "setInformativeText")
    _wrap_last_str(QPainter, "drawText")
    for name in ("information", "warning", "critical", "question"):
        _wrap_static(QMessageBox, name, (1, 2))
    for name in ("getText", "getItem", "getInt", "getDouble", "getMultiLineText"):
        _wrap_static(QInputDialog, name, (1, 2))
    for name in ("getExistingDirectory", "getOpenFileName", "getOpenFileNames", "getSaveFileName"):
        _wrap_static(QFileDialog, name, (1,))
    _patch_add_action()
    _patch_add_item()


def _patch_add_action():
    """QMenu.addAction(« texte »), QMenu.addAction(icone, « texte »)."""
    orig = QMenu.addAction
    if getattr(orig, "_focal_tr", False):
        return

    def f(self, *a, **kw):
        a = list(a)
        for i, v in enumerate(a[:2]):
            if isinstance(v, str):
                a[i] = tr(v)
                break
        return orig(self, *a, **kw)
    f._focal_tr = True
    QMenu.addAction = f


def _patch_add_item():
    orig_add, orig_ins, orig_adds = QComboBox.addItem, QComboBox.insertItem, QComboBox.addItems

    def add(self, *a, **kw):
        a = list(a)
        for i, v in enumerate(a[:2]):
            if isinstance(v, str):
                a[i] = tr(v)
                break
        return orig_add(self, *a, **kw)

    def ins(self, *a, **kw):
        a = list(a)
        for i, v in enumerate(a[:3]):
            if isinstance(v, str):
                a[i] = tr(v)
                break
        return orig_ins(self, *a, **kw)

    def adds(self, items, *a, **kw):
        return orig_adds(self, [tr(x) for x in items], *a, **kw)
    if not getattr(orig_add, "_focal_tr", False):
        add._focal_tr = ins._focal_tr = adds._focal_tr = True
        QComboBox.addItem, QComboBox.insertItem, QComboBox.addItems = add, ins, adds


def patch_osd():
    """Messages affichés sur les vidéos (VLC) : lecteurs, mosaïques, satellites, mur, séances."""
    import importlib
    for modname, clsnames in (("players", ("PlayerWindow",)), ("mosaic", ("Tile", "MosaicWindow")),
                              ("network", ("RemoteScreen",)), ("music", ("MusicPlayer",)),
                              ("extras.mur", ("WallWindow", "WallSession"))):
        try:
            mod = importlib.import_module("phoqueal." + modname)
        except Exception:
            try:
                mod = importlib.import_module(__package__.rsplit(".", 1)[0] + "." + modname)
            except Exception:
                continue
        for cn in clsnames:
            cls = getattr(mod, cn, None)
            if cls is not None and "osd" in cls.__dict__:
                _wrap_method(cls, "osd")


def translate_page(page):
    """Traduit les textes d'une page HTML/JS : texte entre balises, chaînes "…" / '…' et morceaux de `…`.
    Seule une chaîne entière connue est traduite ; les valeurs techniques (data-cmd="pause"…) ne bougent pas."""
    ident = re.compile(r"^[a-z0-9_]+$")
    allow = {"libre", "groupe", "lecture", "piloté", "ensemble"}
    tech = re.compile(r"(?:data-[\w-]+|id|class|name|type|href|src|rel|content|value|for|lang|charset|method)=$")

    def conv(chunk):
        if not chunk.strip() or (ident.match(chunk) and chunk not in allow):
            return chunk
        return tr(chunk)

    def q(m):
        start = m.start()
        if tech.search(page[max(0, start - 30):start]):
            return m.group(0)
        return m.group(1) + conv(m.group(2)) + m.group(1)

    def node(m):
        return ">" + conv(m.group(1)) + "<"

    def tpl(m):
        return m.group(1) + conv(m.group(2)) + m.group(3)

    out = page
    out = re.sub(r'(")([^"\n<>]{1,400})"', q, out)
    page = out
    out = re.sub(r"(')([^'\n<>]{1,400})'", q, out)
    out = re.sub(r">([^<>`]{1,400})<", node, out)
    out = re.sub(r"([`}])([^`${}<>\"]{1,400})(`|\$\{|<)", tpl, out)
    return out.replace('<html lang="fr"', '<html lang="%s"' % STATE["lang"])


def patch_webremote():
    """Page du téléphone : remplace les phrases françaises dans le HTML servi."""
    try:
        from .. import webremote
    except Exception:
        return
    page = getattr(webremote, "PAGE", None)
    if isinstance(page, str):
        webremote.PAGE = translate_page(page)


# ----------------------------------------------------------------------------------------
# Réglage et menu
# ----------------------------------------------------------------------------------------
def wanted(conn):
    from .. import db
    v = db.get_setting(conn, "lang", "") or ""
    if v in dict(LANGS):
        return v
    sys = QLocale.system().name()[:2].lower()
    return sys if sys in dict(LANGS) else "en"


def menu(main, m):
    sub = m.addMenu("Langue / Language")
    cur = STATE["lang"]
    for k, name in LANGS:
        a = sub.addAction(name)              # noms de langues : jamais traduits
        a.setCheckable(True)
        a.setChecked(k == cur)
        a.triggered.connect(lambda _=False, k=k: choose(main, k))


def choose(main, k):
    from .. import db
    db.set_setting(main.conn, "lang", k)
    msg = {"fr": "Langue : français. Relance Phoque Al pour l'appliquer partout.",
           "en": "Language: English. Restart Phoque Al to apply it everywhere.",
           "es": "Idioma: español. Reinicia Phoque Al para aplicarlo en todas partes.",
           "de": "Sprache: Deutsch. Starte Phoque Al neu, um sie überall anzuwenden."}[k]
    box = QMessageBox(main)
    box.setWindowTitle("Phoque Al")
    box.setIcon(QMessageBox.Information)
    box.setText(msg)
    box.exec()


def _load_qt_translator(app, lang):
    try:
        import os
        from PySide6.QtCore import QLibraryInfo, QTranslator
        path = QLibraryInfo.path(QLibraryInfo.TranslationsPath)
        t = QTranslator(app)
        if t.load("qtbase_" + lang, path) or t.load("qt_" + lang, path):
            app.installTranslator(t)
            STATE["qt_tr"] = t
    except Exception as e:
        log("langue : traductions Qt %r" % e)


def early(main):
    """Appelé avant les autres modules : charge la langue (sans encore toucher aux widgets)."""
    try:
        load(wanted(main.conn))
    except Exception as e:
        log("langue : %r" % e)
        load("fr")


def install(main):
    """Appelé en dernier : à partir d'ici, les textes affichés sont traduits."""
    if STATE["lang"] == "fr" or STATE["installed"]:
        return
    STATE["installed"] = True
    app = QApplication.instance()
    _load_qt_translator(app, STATE["lang"])
    patch_qt()
    patch_osd()
    patch_webremote()
    f = Filter(app)
    app.installEventFilter(f)
    STATE["filter"] = f
    translate_tree(main)
    t = QTimer(main)
    t.timeout.connect(sweep)
    t.start(2500)
    STATE["timer"] = t
    log("langue : %s (%d textes)" % (STATE["lang"], len(TR)))
