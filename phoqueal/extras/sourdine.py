"""Sourdine PMV : quand ta musique joue (lecteur de musique de Phoque Al), la musique des PMV ne la gêne plus.

- PMV créés par Phoque Al (3 pistes son) : on passe sur la piste « sons des vidéos » (voix, cris… sans musique).
- PMV dont la musique a été retirée par l'IA (module separation) : on passe sur ce son séparé.
- Autres PMV (musique déjà mélangée) : leur son est coupé ou baissé (réglable).
- Les vidéos qui ne sont pas des PMV gardent leur son normal.
- Dès que ta musique s'arrête, tout revient comme avant.
- Touche U dans le lecteur : forcer / annuler pour la vidéo en cours.
Un PMV = tag « pmv » ou « PMV » dans le titre / le nom du fichier (comme pour la frise).
"""
from PySide6.QtCore import QTimer

from .. import db
from .common import log, owner, safe

ACTIONS = [("p_sourdine", "player", "PMV : couper / rendre la musique de la vidéo", "U")]
MODES = [("piste", "Piste sans musique si elle existe, sinon couper"),
         ("baisser", "Piste sans musique si elle existe, sinon baisser à 15 %"),
         ("off", "Désactivé")]
STATE = {"timer": None}


def S(main, k, d):
    return db.get_setting(main.conn, "sd_" + k, d)


def is_pmv(main, o):
    try:
        from .rythme import is_pmv as rp
        return rp(main, o)
    except Exception:
        return False


def clean_track_id(player):
    """Id VLC de la piste « sons des vidéos » (3e piste d'un PMV Phoque Al), ou None."""
    try:
        desc = player.audio_get_track_description() or []
    except Exception:
        return None
    tracks = [(i, (n.decode("utf-8", "replace") if isinstance(n, bytes) else str(n))) for i, n in desc if i != -1]
    try:
        from .separation import slave_for
        if slave_for(player) and len(tracks) >= 2:
            return tracks[-1][0]                     # son séparé par l'IA (piste ajoutée en dernier)
    except Exception:
        pass
    for i, n in tracks:
        if "sans musique" in n.lower() or "sons des vid" in n.lower():
            return i
    return tracks[2][0] if len(tracks) == 3 else None


def windows(main):
    for w in list(main.ctrl.players.values()):
        if getattr(w, "is_remote", False):
            continue
        if getattr(w, "is_mosaic", False):
            for t in getattr(w, "tiles", []):
                yield t
        else:
            yield w


def _restore(o):
    st = getattr(o, "_sd", None)
    if st is None:
        return
    pl, mode, prev = st
    try:
        if mode == "piste" and prev is not None:
            pl.audio_set_track(prev)
        elif mode in ("baisser", "couper") and not getattr(o, "muted", False):
            pl.audio_set_volume(int(getattr(o, "volume", 80)))
    except Exception:
        pass
    o._sd = None


def apply(main, o, on):
    """on=True : sourdine ; False : retour au son normal. Appelé régulièrement (idempotent)."""
    p = getattr(o, "player", None)
    if p is None:
        return
    st = getattr(o, "_sd", None)
    if st is not None and st[0] is not p:          # le montage a change de surface video
        _restore(o)
        st = None
    if not on or getattr(o, "muted", False):
        _restore(o)
        _not_slave(p)
        return
    tid = clean_track_id(p)
    if tid is not None:
        if st is not None and st[1] != "piste":
            _restore(o)
            st = None
        if st is None:
            o._sd = (p, "piste", p.audio_get_track())
        if p.audio_get_track() != tid:
            p.audio_set_track(tid)
        return
    mode = "baisser" if S(main, "mode", "piste") == "baisser" else "couper"
    if st is None:
        o._sd = (p, mode, None)
    target = int(getattr(o, "volume", 80) * 0.15) if mode == "baisser" else 0
    if p.audio_get_volume() != target:           # le lecteur remet son volume au demarrage d'une video
        p.audio_set_volume(target)


def _not_slave(p):
    """Sans sourdine, le son séparé ne doit jamais être la piste jouée."""
    try:
        from .separation import slave_for
        if not slave_for(p):
            return
        desc = [i for i, _n in (p.audio_get_track_description() or []) if i != -1]
        if len(desc) >= 2 and p.audio_get_track() == desc[-1]:
            p.audio_set_track(desc[0])
    except Exception:
        pass


def tick(main):
    if S(main, "mode", "piste") == "off":
        want_any = False
    else:
        want_any = bool(main.music.is_playing())
    for o in windows(main):
        forced = getattr(o, "_sd_force", None)            # touche U : True / False / None
        vid = getattr(o, "video_id", None)
        if getattr(o, "_sd_vid", None) != vid:            # nouvelle video : le choix manuel est oublie
            o._sd_vid = vid
            o._sd_force = forced = None
            if getattr(o, "_sd", None) is not None and o._sd[0] is not getattr(o, "player", None):
                o._sd = None
        on = forced if forced is not None else (want_any and is_pmv(main, o))
        safe(apply, main, o, bool(on))


def toggle(win):
    o = owner(win)
    main = win.ctrl.main
    cur = getattr(o, "_sd", None) is not None
    o._sd_force = not cur
    safe(apply, main, o, not cur)
    try:
        o.osd("Musique de la vidéo : %s" % ("retirée" if not cur else "rendue"), 1400)
    except Exception:
        pass


PLAYER_KEYS = {"p_sourdine": toggle}
MOSAIC_KEYS = dict(PLAYER_KEYS)


def menu(main, m):
    sub = m.addMenu("PMV pendant ma musique")
    cur = S(main, "mode", "piste")
    for k, lab in MODES:
        a = sub.addAction(lab)
        a.setCheckable(True)
        a.setChecked(k == cur)
        a.triggered.connect(lambda _=False, k=k: db.set_setting(main.conn, "sd_mode", k))
    i = sub.addAction("U dans le lecteur : retirer / rendre la musique de la vidéo en cours")
    i.setEnabled(False)


def install(main):
    t = QTimer(main)
    t.timeout.connect(lambda: safe(tick, main))
    t.start(700)
    STATE["timer"] = t
