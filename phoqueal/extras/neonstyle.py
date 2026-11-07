"""Style néon commun (séance, POP mode) : polices intégrées, couleurs des phases, phrases d'encouragement.

Les phrases se règlent dans « POP mode… → Phrases » (une par ligne) ; vides = phrases par défaut.
"""
import base64
import random

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainterPath, QPen

from .. import db

FAMILIES = {}

# clé : (mot affiché, couleur, pictogramme)
PHASES = {
    "CALME": ("CALME", "#3ee8ff", "wave"),
    "MONTEE": ("MONTÉE", "#ff4fd8", "up"),
    "RAPIDE": ("RAPIDE", "#ff8a2b", "fast"),
    "INTENSE": ("INTENSE", "#ff2f6d", "flame"),
    "RALENTIS": ("RALENTIS", "#ffc23e", "slow"),
    "STOP": ("STOP", "#ff2f4f", "stop"),
    "REDESCENTE": ("REDESCENTE", "#8c6bff", "down"),
    "FINAL": ("FINAL", "#ffd36b", "star"),
}
ORDER = ["CALME", "MONTEE", "RAPIDE", "INTENSE", "RALENTIS", "STOP", "REDESCENTE", "FINAL"]
# nom de phase de la séance -> clé
KEY = {"Calme": "CALME", "Montée": "MONTEE", "Monte": "MONTEE", "Rapide": "RAPIDE", "Intense": "INTENSE",
       "Ralentis": "RALENTIS", "Stop": "STOP", "Redescente": "REDESCENTE", "Final": "FINAL"}
DEFAULT_PHRASES = {
    "CALME": ["Respire. Laisse venir.", "Tout en douceur…", "Prends ton temps."],
    "MONTEE": ["Ça monte… laisse-toi faire.", "Encore un peu.", "Sens la chaleur grimper."],
    "RAPIDE": ["Accélère.", "Suis le rythme.", "Plus vite, plus fort."],
    "INTENSE": ["Tout donner.", "Ne t'arrête pas.", "Au maximum."],
    "RALENTIS": ["Doucement… savoure.", "Reste au bord.", "Garde le contrôle."],
    "STOP": ["Ne bouge plus.", "Tiens bon.", "Attends le signal."],
    "REDESCENTE": ["Reprends ton souffle.", "Laisse retomber.", "Bien joué."],
    "FINAL": ["Lâche tout.", "C'est le moment.", "Tout, maintenant."],
}


def tr(text):
    try:
        from . import i18n
        return i18n.tr(text)
    except Exception:
        return text


def word(key):
    return tr(PHASES[key][0]) if key in PHASES else key


def fonts():
    """{'title', 'title_bold', 'text', 'text_med', 'pop'} -> nom de famille (chargées une seule fois)."""
    if FAMILIES:
        return FAMILIES
    try:
        from .neonfonts import DATA
        for key, name in (("orbitron_black", "title"), ("orbitron_bold", "title_bold"), ("rajdhani_bold", "text"),
                          ("rajdhani_medium", "text_med"), ("monoton", "pop")):
            fid = QFontDatabase.addApplicationFontFromData(base64.b64decode("".join(DATA[key])))
            fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
            FAMILIES[name] = fams[0] if fams else "Segoe UI"
    except Exception:
        for name in ("title", "title_bold", "text", "text_med", "pop"):
            FAMILIES[name] = "Segoe UI"
    return FAMILIES


def font(kind, px, bold=None, spacing=None):
    f = QFont(fonts()[kind])
    f.setPixelSize(int(px))
    if kind == "text":
        f.setWeight(QFont.Bold)
    elif kind == "text_med":
        f.setWeight(QFont.Medium)
    if bold is not None:
        f.setBold(bold)
    if spacing:
        f.setLetterSpacing(QFont.PercentageSpacing, spacing)
    return f


def phrases(conn, key):
    raw = db.get_setting(conn, "pop_phr_" + key, "") or ""
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    return lines or [tr(x) for x in DEFAULT_PHRASES.get(key, [""])]


def pick_phrase(conn, key, avoid=None):
    lst = phrases(conn, key)
    choices = [p for p in lst if p != avoid] or lst
    return random.choice(choices) if choices else ""


def glow_text(p, rect, text, f, color, align=Qt.AlignCenter, glow=3, alpha=1.0, core="#ffffff"):
    """Texte néon : halo de la couleur (contours larges et transparents) puis lettres claires."""
    path = QPainterPath()
    from PySide6.QtGui import QFontMetricsF
    fm = QFontMetricsF(f)
    w = fm.horizontalAdvance(text)
    if align & Qt.AlignHCenter:
        x = rect.x() + (rect.width() - w) / 2
    elif align & Qt.AlignRight:
        x = rect.right() - w
    else:
        x = rect.x()
    if align & Qt.AlignVCenter:
        y = rect.y() + (rect.height() + fm.ascent() - fm.descent()) / 2
    elif align & Qt.AlignBottom:
        y = rect.bottom() - fm.descent()
    else:
        y = rect.y() + fm.ascent()
    path.addText(QPointF(x, y), f, text)
    c = QColor(color)
    p.setBrush(Qt.NoBrush)
    size = f.pixelSize() or 20
    for k in range(glow, 0, -1):
        g = QColor(c)
        g.setAlphaF(max(0.0, min(1.0, (0.07 + 0.05 * (glow - k)) * alpha)))
        p.setPen(QPen(g, size * 0.06 * k * 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)
    edge = QColor(c)
    edge.setAlphaF(0.9 * alpha)
    p.setPen(QPen(edge, max(1.0, size * 0.035), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    fill = QColor(core)
    fill.setAlphaF(alpha)
    p.setBrush(fill)
    p.drawPath(path)
    return w


def icon(p, kind, rect, color, alpha=1.0):
    """Petits pictogrammes des phases (traits néon)."""
    c = QColor(color)
    c.setAlphaF(alpha)
    s = rect.width() / 24.0
    x0, y0 = rect.x(), rect.y()

    def P(x, y):
        return QPointF(x0 + x * s, y0 + y * s)
    path = QPainterPath()
    if kind == "wave":
        path.moveTo(P(2, 12)); path.cubicTo(P(5, 6), P(7, 6), P(10, 12)); path.cubicTo(P(13, 18), P(15, 18), P(18, 12))
        path.cubicTo(P(20, 9), P(21, 9), P(22, 10))
    elif kind in ("up", "down"):
        d = 1 if kind == "down" else -1
        for yy in (8, 15):
            yy = yy if d > 0 else 24 - yy
            path.moveTo(P(4, yy - 3 * d)); path.lineTo(P(12, yy + 3 * d)); path.lineTo(P(20, yy - 3 * d))
    elif kind == "fast":
        for xx in (3, 12):
            path.moveTo(P(xx, 6)); path.lineTo(P(xx + 7, 12)); path.lineTo(P(xx, 18))
    elif kind == "flame":
        path.moveTo(P(12, 22)); path.cubicTo(P(6, 22), P(4, 17), P(6, 12)); path.cubicTo(P(8, 8), P(9, 6), P(9, 3))
        path.cubicTo(P(12, 6), P(13, 8), P(13, 10)); path.cubicTo(P(14, 9), P(15, 8), P(15, 6))
        path.cubicTo(P(18, 9), P(20, 13), P(19, 16)); path.cubicTo(P(18, 20), P(15, 22), P(12, 22))
    elif kind == "slow":
        path.moveTo(P(6, 3)); path.lineTo(P(18, 3)); path.moveTo(P(6, 21)); path.lineTo(P(18, 21))
        path.moveTo(P(7, 3)); path.cubicTo(P(7, 9), P(17, 9), P(17, 21)); path.moveTo(P(17, 3)); path.cubicTo(P(17, 9), P(7, 9), P(7, 21))
    elif kind == "stop":
        path.addRect(QRectF(P(5, 5), P(19, 19)))
    else:  # star
        pts = [(12, 2), (15, 9), (22, 9.6), (16.6, 14.2), (18.3, 21.5), (12, 17.8), (5.7, 21.5), (7.4, 14.2), (2, 9.6), (9, 9)]
        path.moveTo(P(*pts[0]))
        for q in pts[1:]:
            path.lineTo(P(*q))
        path.closeSubpath()
    p.setBrush(Qt.NoBrush)
    for k in (3, 2, 1):
        g = QColor(c)
        g.setAlphaF(0.12 * alpha)
        p.setPen(QPen(g, 2.2 * s * k * 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)
    p.setPen(QPen(c, 2.2 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawPath(path)


def cut_corners(r, k):
    """Rectangle aux coins coupés (haut droit et bas gauche)."""
    path = QPainterPath()
    path.moveTo(r.left(), r.top())
    path.lineTo(r.right() - k, r.top())
    path.lineTo(r.right(), r.top() + k)
    path.lineTo(r.right(), r.bottom())
    path.lineTo(r.left() + k, r.bottom())
    path.lineTo(r.left(), r.bottom() - k)
    path.closeSubpath()
    return path
