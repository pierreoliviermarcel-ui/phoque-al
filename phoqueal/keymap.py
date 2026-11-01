"""Attribution des touches : actions, touches par defaut, personnalisation."""
import json

from . import db

# (identifiant, portee, libelle, touche par defaut)
ACTIONS = [("focus_home", "global", "Revenir à l'accueil", "0")]
ACTIONS += [("focus_%d" % n, "global", "Aller au lecteur de l'écran %d" % n, str(n)) for n in range(1, 10)]
ACTIONS += [
    ("all_pause", "global", "Tous les lecteurs : pause / lecture", "Shift+Space"),
    ("all_back", "global", "Tous les lecteurs : reculer de 10 s", "Shift+Left"),
    ("all_fwd", "global", "Tous les lecteurs : avancer de 10 s", "Shift+Right"),
    ("all_vol_up", "global", "Tous les lecteurs : volume +", "Shift+Up"),
    ("all_vol_down", "global", "Tous les lecteurs : volume −", "Shift+Down"),
    ("all_mute", "global", "Tous les lecteurs : couper / remettre le son", "Shift+M"),
    ("all_close", "global", "Fermer tous les lecteurs", "Shift+Esc"),
    ("rotate", "global", "Décaler les vidéos d'un écran (1 → 2 → 3 → 1)", "Shift+R"),
    ("rotate_back", "global", "Décaler les vidéos dans l'autre sens (3 → 2 → 1 → 3)", "Shift+E"),
    ("all_blur", "global", "Tous les lecteurs : fond flou sur les bords", "Shift+B"),
    ("all_view", "global", "Tous les lecteurs : changer le format d'image", "Shift+C"),
    ("panic", "global", "Touche panique (tout masquer)", "F12"),
    ("discreet", "global", "Mode discret : activer / désactiver", "Ctrl+D"),
    ("remote", "global", "Télécommande (mode souris) : afficher / masquer", "Ctrl+T"),
    ("p_pause", "player", "Pause / lecture", "Space"),
    ("p_back", "player", "Reculer de 10 s", "Left"),
    ("p_fwd", "player", "Avancer de 10 s", "Right"),
    ("p_back_long", "player", "Reculer d'1 min", "Ctrl+Left"),
    ("p_fwd_long", "player", "Avancer d'1 min", "Ctrl+Right"),
    ("p_vol_up", "player", "Volume +", "Up"),
    ("p_vol_down", "player", "Volume −", "Down"),
    ("p_mute", "player", "Couper / remettre le son", "M"),
    ("p_fullscreen", "player", "Plein écran / fenêtre", "F"),
    ("p_info", "player", "Afficher les infos", "I"),
    ("p_close", "player", "Fermer le lecteur", "Esc"),
    ("p_like", "player", "J'aime / je n'aime plus cette vidéo", "L"),
    ("p_blur", "player", "Fond flou sur les bords : activer / désactiver", "B"),
    ("p_view", "player", "Format d'image : changer (bandes noires, remplir, 4:3…)", "C"),
    ("p_next", "player", "Playlist : vidéo suivante", "N"),
    ("p_prev", "player", "Playlist : vidéo précédente", "P"),
    ("m_toggle", "music", "Musique : lecture / pause", "Ctrl+Space"),
    ("m_next", "music", "Musique : morceau suivant", "Ctrl+N"),
    ("m_prev", "music", "Musique : morceau précédent", "Ctrl+B"),
    ("m_vol_up", "music", "Musique : volume +", "Ctrl+Up"),
    ("m_vol_down", "music", "Musique : volume −", "Ctrl+Down"),
    ("m_montage", "music", "Montage au rythme : activer / désactiver", "Ctrl+R"),
    ("mo_save", "mosaic", "Enregistrer la mosaïque", "Ctrl+S"),
    ("h_next", "home", "Bannière : diapositive suivante", "PgDown"),
    ("h_prev", "home", "Bannière : diapositive précédente", "PgUp"),
    ("h_play", "home", "Bannière : lire la vidéo en vedette", "Ctrl+L"),
    ("h_multi", "home", "Bannière : lancer le thème sur tous les écrans", "Ctrl+E"),
]
DEFAULTS = {a[0]: a[3] for a in ACTIONS}
SCOPES = [("global", "Partout (accueil et lecteurs)"),
          ("player", "Dans le lecteur actif (cadre bleu)"),
          ("music", "Musique de fond (partout)"),
          ("mosaic", "Dans une mosaïque (les touches du lecteur agissent sur toutes ses vidéos)"),
          ("home", "Sur l'accueil (grande bannière)")]


def load(conn):
    try:
        custom = json.loads(db.get_setting(conn, "keymap", "{}") or "{}")
    except ValueError:
        custom = {}
    return {k: custom.get(k, v) for k, v in DEFAULTS.items()}


def save(conn, mapping):
    custom = {k: v for k, v in mapping.items() if k in DEFAULTS and v != DEFAULTS[k]}
    db.set_setting(conn, "keymap", json.dumps(custom) if custom else None)


def conflicts(mapping):
    """Touches attribuees a plusieurs actions qui peuvent se gener : {touche: [libelles]}.
    Une touche peut servir a la fois sur l'accueil et dans les lecteurs (fenetres differentes) ;
    les touches « partout » et « musique » entrent en conflit avec tout."""
    labels = {a[0]: a[2] for a in ACTIONS}
    ctx = {a[0]: a[1] for a in ACTIONS}
    wide = ("global", "music")

    def clash(a, b):
        return a == b or a in wide or b in wide or {a, b} <= {"player", "mosaic"}

    seen = {}
    for k, seq in mapping.items():
        if seq and k in labels:
            seen.setdefault(seq, []).append(k)
    out = {}
    for seq, keys in seen.items():
        bad = [k for k in keys if any(j != k and clash(ctx[k], ctx[j]) for j in keys)]
        if len(bad) > 1:
            out[seq] = [labels[k] for k in bad]
    return out


def pretty(seq):
    if not seq:
        return "(aucune)"
    rep = {"Shift": "Maj", "Space": "Espace", "Left": "←", "Right": "→", "Up": "↑", "Down": "↓",
           "Esc": "Échap", "Return": "Entrée", "Del": "Suppr", "PgUp": "Page préc.", "PgDown": "Page suiv."}
    return " + ".join(rep.get(p, p) for p in seq.split("+"))


def help_text(mapping):
    lines = []
    for scope, title in SCOPES:
        lines.append(title.upper())
        for k, sc, label, _ in ACTIONS:
            if sc == scope and k not in ("focus_%d" % n for n in range(4, 10)):
                lines.append("%s : %s" % (pretty(mapping.get(k)), label))
        lines.append("")
    lines.append("Les touches se modifient dans Paramètres → Clavier.")
    return "\n".join(lines)
